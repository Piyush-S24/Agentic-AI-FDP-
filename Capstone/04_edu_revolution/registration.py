"""
EdU Revolution — Application / Registration module.

Turns the advisor from a pure Q&A bot into an agent that can **directly file** a
student's EDU Revolution nomination. It validates the submitted details, checks
eligibility against the manual's rules, persists the application to a local store,
and returns a reference ID plus the official next steps (the UMS -> LMS portal
path from the manual).

Storage is a simple append-only JSONL file (`data/applications.jsonl`) — there is
no external UMS API to call, so "registration" means a durable, retrievable record
with a reference number. Swap `ApplicationStore` for a real UMS/LMS integration to
go live.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config import DATA_DIR
from rules import RuleEngine, required_metric, METRIC_LABELS
from verification import ProofVerifier
from duplicate import DuplicateChecker
from decision import DecisionEngine, status_for, HIERARCHY_LABELS
from proofs import ProofStore
from directory import StudentDirectory

logger = logging.getLogger("edu_revolution.registration")

# Automation engines (stateless singletons).
_rule_engine = RuleEngine()
_verifier = ProofVerifier()
_decider = DecisionEngine()


# =========================================================
# Reference data (grounded in the EDU Revolution manual)
# =========================================================
# Initiatives = the pathways a student converts into academic benefits.
INITIATIVES: List[Dict] = [
    {"key": "revenue_generation", "label": "Revenue Generation",
     "hint": "Income earned through entrepreneurial / professional / technical work (Earn Your Fee)."},
    {"key": "nptel_mooc_certification", "label": "NPTEL / Proctored MOOC / Certification",
     "hint": "Recognized online courses & certifications completed beyond the curriculum."},
    {"key": "project", "label": "Project / Hackathon",
     "hint": "Industry, government, academic, startup or hackathon projects."},
    {"key": "internship_beyond_curriculum", "label": "Internship Beyond Curriculum",
     "hint": "Supervised work-based learning outside the curriculum."},
    {"key": "rpl", "label": "Recognition of Prior Learning (RPL)",
     "hint": "Skills from prior work experience, training or academic learning."},
    {"key": "community_service", "label": "Community Service",
     "hint": "Approved community / social-impact service projects."},
]

# Academic benefits = what the student receives if the nomination is approved.
ACADEMIC_BENEFITS: List[Dict] = [
    {"key": "course_equivalence", "label": "Course Equivalence"},
    {"key": "grade_upgradation", "label": "Grade Upgradation"},
    {"key": "attendance_benefit", "label": "10% Attendance Benefit"},
    {"key": "duty_leave", "label": "Duty Leave (30–150 hrs)"},
    {"key": "evaluation_ca_mtt", "label": "Evaluation Benefit (CA / MTT)"},
    {"key": "rpl_recognition", "label": "RPL Recognition"},
    {"key": "transcript_value_addition", "label": "Value Addition in Transcript"},
]

YEAR_OPTIONS = ["1st Year", "2nd Year", "3rd Year (Pre-Final)", "4th Year (Final)", "5th Year"]

_INITIATIVE_KEYS = {i["key"] for i in INITIATIVES}
_BENEFIT_KEYS = {b["key"] for b in ACADEMIC_BENEFITS}
_BENEFIT_LABELS = {b["key"]: b["label"] for b in ACADEMIC_BENEFITS}
_INITIATIVE_LABELS = {i["key"]: i["label"] for i in INITIATIVES}

# Official next steps (verbatim intent from the manual).
PORTAL_PATH = "Login to UMS → LMS → 'Edu Revolution: Be the Change' → Apply for EDU Revolution"
QUERY_ZONE = "Edu-Revolution Query & Assistance Zone: Block 38-205B"

REQUIRED_FIELDS = [
    "student_name", "registration_id", "email", "program", "year_of_study",
    "cgpa", "attendance_percent", "initiative", "academic_benefit",
    "activity_title", "activity_description", "declaration",
]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ApplicationError(ValueError):
    """Raised when an application payload is invalid. Carries per-field messages."""

    def __init__(self, errors: Dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


# =========================================================
# Eligibility — advisory rules drawn from the manual
# =========================================================
def _to_float(value) -> Optional[float]:
    try:
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def check_eligibility(payload: Dict) -> Dict:
    """
    Advisory eligibility check based on the manual's stated prerequisites.

    Returns ``{"status": "eligible|conditional|action_required", "notes": [...]}``.
    These are *guidance* flags only — the Standing Committee makes the final,
    binding decision, so nothing here hard-blocks a submission.
    """
    notes: List[str] = []
    status = "eligible"

    cgpa = _to_float(payload.get("cgpa"))
    attendance = _to_float(payload.get("attendance_percent"))
    initiative = payload.get("initiative")
    benefit = payload.get("academic_benefit")

    def require(flag_ok: bool, message: str, hard: bool = False):
        nonlocal status
        if not flag_ok:
            notes.append(message)
            if hard:
                status = "action_required"
            elif status == "eligible":
                status = "conditional"

    # --- Attendance-based benefits ---
    if benefit == "attendance_benefit":
        require(cgpa is not None and cgpa >= 7.5,
                "The 10% Attendance Benefit needs a minimum CGPA of 7.5 (Pre-Final/Final year).",
                hard=True)
        require(attendance is not None and attendance >= 60,
                "You must maintain at least 60% attendance (excluding bonus/duty leave) to claim attendance benefits.",
                hard=True)

    # --- Duty leave / project & internship attendance floor ---
    if benefit == "duty_leave" or initiative in ("project", "internship_beyond_curriculum"):
        require(attendance is None or attendance >= 65,
                "Duty-Leave / project / internship benefits require at least 65% attendance (excluding bonus & duty leave).")

    # --- CGPA floors per initiative ---
    if initiative == "nptel_mooc_certification" and benefit == "course_equivalence":
        require(cgpa is not None and cgpa >= 7.0,
                "Complete Course Equivalence via NPTEL/MOOC needs a minimum CGPA of 7.0.")
    if initiative in ("project", "internship_beyond_curriculum"):
        require(cgpa is not None and cgpa >= 6.0,
                "Projects / internships have a standard minimum CGPA of 6.0 (exceptional cases may still be considered).")
    if initiative == "revenue_generation":
        notes.append("Revenue Generation has no minimum CGPA requirement; benefits apply only to courses you have passed.")

    # --- Universal reminders from the manual ---
    notes.append("Submit revenue proof / supporting documents at least 15 days before the last teaching day of the semester.")
    notes.append("You must still appear for the End Term Examination (ETE) regardless of the benefit claimed.")
    notes.append("All nominations are verified and decided by the School's Standing Committee — this decision is final and binding.")

    return {"status": status, "notes": notes}


def next_steps() -> List[str]:
    """The official action items after filing (from the manual)."""
    return [
        f"Portal: {PORTAL_PATH}.",
        "Attach the prescribed Undertaking Form plus your proofs (revenue statement / certificate / stipend letter, etc.).",
        "Track your nomination's status on the EDU Revolution portal for the Standing Committee's review.",
        f"Need help in person? {QUERY_ZONE}.",
    ]


# =========================================================
# Validation
# =========================================================
def validate(payload: Dict) -> Dict:
    """Validate + normalize an application payload. Raises ApplicationError."""
    errors: Dict[str, str] = {}
    clean: Dict = {}

    def text(field: str, label: str, min_len: int = 1):
        val = str(payload.get(field, "") or "").strip()
        if len(val) < min_len:
            errors[field] = f"{label} is required."
        clean[field] = val

    text("student_name", "Full name", 2)
    text("registration_id", "Registration / UMS ID", 3)
    text("program", "Program / branch", 2)
    text("activity_title", "Activity title", 3)
    text("activity_description", "Activity description", 10)

    # Email
    email = str(payload.get("email", "") or "").strip()
    if not _EMAIL_RE.match(email):
        errors["email"] = "A valid email address is required."
    clean["email"] = email

    # Optional phone
    clean["phone"] = str(payload.get("phone", "") or "").strip()
    clean["school"] = str(payload.get("school", "") or "").strip()
    clean["supporting_documents"] = str(payload.get("supporting_documents", "") or "").strip()

    # Uploaded proof documents — the client sends proof ids; the SERVER resolves them
    # (so filename/hash/extracted text can't be spoofed by the client).
    ids = []
    for p in (payload.get("proof_files") or []):
        pid = p.get("proof_id") if isinstance(p, dict) else p
        if pid:
            ids.append(str(pid))
    clean["proof_file_ids"] = ids

    # Optional proof links + tenant + achievement stage (2.0 automation inputs)
    clean["proof_links"] = str(payload.get("proof_links", "") or "").strip()
    clean["tenant_id"] = (str(payload.get("tenant_id", "") or "").strip() or "lpu-default")
    clean["achievement_stage"] = str(payload.get("achievement_stage", "") or "").strip()

    # Optional structured metrics that let the rule engine auto-decide.
    for metric in ("revenue_amount", "stipend_amount", "duration_months"):
        clean[metric] = _to_float(payload.get(metric))

    # Year
    year = str(payload.get("year_of_study", "") or "").strip()
    if not year:
        errors["year_of_study"] = "Select your year of study."
    clean["year_of_study"] = year

    # CGPA
    cgpa = _to_float(payload.get("cgpa"))
    if cgpa is None or not (0 <= cgpa <= 10):
        errors["cgpa"] = "Enter a valid CGPA between 0 and 10."
    clean["cgpa"] = cgpa

    # Attendance
    att = _to_float(payload.get("attendance_percent"))
    if att is None or not (0 <= att <= 100):
        errors["attendance_percent"] = "Enter a valid attendance percentage between 0 and 100."
    clean["attendance_percent"] = att

    # Initiative + benefit (accept key or label)
    initiative = _resolve_key(payload.get("initiative"), _INITIATIVE_KEYS, INITIATIVES)
    if initiative is None:
        errors["initiative"] = "Choose an EDU Revolution initiative."
    clean["initiative"] = initiative
    clean["initiative_label"] = _INITIATIVE_LABELS.get(initiative, "")

    benefit = _resolve_key(payload.get("academic_benefit"), _BENEFIT_KEYS, ACADEMIC_BENEFITS)
    if benefit is None:
        errors["academic_benefit"] = "Choose the academic benefit you're applying for."
    clean["academic_benefit"] = benefit
    clean["academic_benefit_label"] = _BENEFIT_LABELS.get(benefit, "")

    # Declaration
    if not bool(payload.get("declaration")):
        errors["declaration"] = "You must confirm the declaration to file the nomination."
    clean["declaration"] = bool(payload.get("declaration"))

    # Per-filing compulsory metric: e.g. Revenue Generation must include the revenue
    # amount; an Internship course-waiver must include the stipend. Driven by the rules.
    if initiative and benefit:
        need = required_metric(initiative, benefit)
        if need and clean.get(need) is None:
            label = METRIC_LABELS.get(need, need)
            errors[need] = f"{label} is required for {clean.get('initiative_label') or initiative}."

    if errors:
        raise ApplicationError(errors)
    return clean


def _resolve_key(value, valid_keys, options) -> Optional[str]:
    """Accept either a canonical key or a human label and return the key."""
    if not value:
        return None
    v = str(value).strip()
    if v in valid_keys:
        return v
    low = v.lower()
    for opt in options:
        if opt["label"].lower() == low or opt["key"].lower() == low:
            return opt["key"]
    return None


# =========================================================
# Achievement Profile (Gap #2) — one evolving record per real achievement
# =========================================================
def _achievement_signature(nom: Dict) -> str:
    """A stable signature for an achievement: its first proof identifier, else its title."""
    blob = " ".join(str(nom.get(k, "")) for k in
                    ("activity_title", "supporting_documents", "proof_links"))
    found = _verifier.extract_identifiers(blob)
    flat = sorted(f"{k}:{v.lower()}" for k, vs in found.items() for v in vs)
    title = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", nom.get("activity_title", "").lower())).strip()
    return flat[0] if flat else f"title:{title}"


class AchievementStore:
    """Persistent per-student achievements, reused across stages/nominations."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else (DATA_DIR / "achievements.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _all(self) -> List[Dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def _rewrite(self, records: List[Dict]):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def upsert(self, nom: Dict, reference_id: str) -> str:
        """Attach this nomination to the student's evolving achievement; return its id."""
        reg = str(nom.get("registration_id", "")).strip().lower()
        sig = _achievement_signature(nom)
        stage = nom.get("achievement_stage") or "n/a"
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            records = self._all()
            for rec in records:
                if rec.get("registration_id", "").lower() == reg and rec.get("signature") == sig:
                    if reference_id not in rec["nominations"]:
                        rec["nominations"].append(reference_id)
                    if stage not in rec["stages"]:
                        rec["stages"].append(stage)
                    rec["updated_at"] = now
                    self._rewrite(records)
                    return rec["achievement_id"]
            achievement_id = f"ACH-{len(records) + 1:05d}"
            records.append({
                "achievement_id": achievement_id, "registration_id": nom.get("registration_id"),
                "signature": sig, "title": nom.get("activity_title"),
                "stages": [stage], "nominations": [reference_id],
                "created_at": now, "updated_at": now,
            })
            self._rewrite(records)
            return achievement_id

    def list_for(self, registration_id: str) -> List[Dict]:
        reg = str(registration_id).strip().lower()
        return [r for r in self._all() if r.get("registration_id", "").lower() == reg]


# =========================================================
# Store — filed nominations + the automated decision pipeline
# =========================================================
class ApplicationStore:
    """JSONL store for filed nominations; runs the 2.0 automation on submit."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else (DATA_DIR / "applications.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.achievements = AchievementStore(
            self.path.with_name("achievements.jsonl") if path else None
        )
        self.proofs = ProofStore(self.path.parent / "proofs" if path else None)
        self.directory = StudentDirectory(self.path.parent / "students.json" if path else None)

    def _count(self) -> int:
        if not self.path.exists():
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def create(self, payload: Dict) -> Dict:
        """Validate, run the automation pipeline, persist, and return the record."""
        clean = validate(payload)

        # --- Authoritative data: trust the college record, not what was typed ---
        # If the registration id is in the directory, use its CGPA/attendance for the
        # decision and flag any mismatch with what the student self-reported.
        authoritative = self.directory.get(clean["registration_id"])
        if authoritative:
            self_cgpa, self_att = clean.get("cgpa"), clean.get("attendance_percent")
            auth_cgpa = _to_float(authoritative.get("cgpa"))
            auth_att = _to_float(authoritative.get("attendance_percent"))
            mismatch = []
            if self_cgpa is not None and auth_cgpa is not None and abs(self_cgpa - auth_cgpa) > 0.05:
                mismatch.append(f"CGPA claimed {self_cgpa} vs official record {auth_cgpa}")
            if self_att is not None and auth_att is not None and abs(self_att - auth_att) > 1:
                mismatch.append(f"attendance claimed {self_att}% vs official record {auth_att}%")
            clean["self_reported"] = {"cgpa": self_cgpa, "attendance_percent": self_att}
            clean["cgpa"] = auth_cgpa if auth_cgpa is not None else self_cgpa
            clean["attendance_percent"] = auth_att if auth_att is not None else self_att
            clean["data_source"] = "college_records"
            clean["data_mismatch"] = mismatch
            clean["identity_verified"] = bool(payload.get("identity_verified"))
        else:
            clean["data_source"] = "self_reported"
            clean["data_mismatch"] = []
            clean["identity_verified"] = False

        # Resolve the student's uploaded proof documents server-side. The extracted
        # PDF text feeds verification/fraud checks but isn't persisted in full.
        proof_records = self.proofs.resolve(clean.pop("proof_file_ids", []))
        clean["proof_files"] = [ProofStore.public(r) for r in proof_records]
        proof_text = "\n".join((r.get("extracted_text") or "") for r in proof_records)
        analysis = dict(clean, proof_text=proof_text)

        eligibility = check_eligibility(analysis)

        # --- Automation pipeline (deterministic; no LLM) ---
        rule_eval = _rule_engine.evaluate(analysis)
        verification = _verifier.verify(analysis)
        duplicate_check = DuplicateChecker(self.list_all).check(analysis)
        decision = _decider.decide(analysis, rule_eval, verification, duplicate_check)

        # A self-reported/record mismatch is an integrity red flag — never auto-clear it.
        if clean["data_mismatch"] and decision["outcome"] == "auto_approve":
            decision = {
                "outcome": "escalate", "confidence": 0.5,
                "reason": "Self-reported data does not match the official record: "
                          + "; ".join(clean["data_mismatch"]) + ". Routed for review.",
                "escalate_to": "school_coordinator", "sla_days": 5, "signals": decision.get("signals", {}),
            }
        status = status_for(decision)

        now = datetime.now(timezone.utc)
        sla_due = (now + timedelta(days=decision["sla_days"])).isoformat()
        decision_log = [{
            "at": now.isoformat(), "actor": "AI Decision Engine",
            "action": decision["outcome"], "to": decision["escalate_to"],
            "to_label": HIERARCHY_LABELS.get(decision["escalate_to"], decision["escalate_to"]),
            "reason": decision["reason"], "rule_version": rule_eval.get("rule_version"),
        }]

        with self._lock:
            seq = self._count() + 1
            reference_id = f"EDU-REV-{now.year}-{seq:05d}"
            achievement_id = self.achievements.upsert(clean, reference_id)
            record = {
                "reference_id": reference_id,
                "status": status,
                "submitted_at": now.isoformat(),
                "sla_due": sla_due,
                "current_owner": decision["escalate_to"],
                "current_owner_label": HIERARCHY_LABELS.get(decision["escalate_to"], decision["escalate_to"]),
                "achievement_id": achievement_id,
                "eligibility": eligibility,
                "rule_eval": rule_eval,
                "verification": verification,
                "duplicate_check": duplicate_check,
                "decision": decision,
                "decision_log": decision_log,
                "next_steps": next_steps(),
                **clean,
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(f"Filed {reference_id} ({clean['initiative']}→{clean['academic_benefit']}) "
                    f"→ {decision['outcome']} (conf {decision['confidence']}, dup {duplicate_check['risk']})")
        return record

    def list_all(self) -> List[Dict]:
        if not self.path.exists():
            return []
        records = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def get(self, reference_id: str) -> Optional[Dict]:
        for rec in self.list_all():
            if rec.get("reference_id") == reference_id:
                return rec
        return None

    def _rewrite(self, records: List[Dict]):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def apply_decision(self, reference_id: str, actor: str, action: str,
                       note: str = "", to: str = None) -> Optional[Dict]:
        """
        Record a human decision (Layer 6). action ∈ {approve, reject, escalate, request_info}.
        Appends to the audit log and moves the nomination's status/owner.
        """
        with self._lock:
            records = self.list_all()
            target = None
            for rec in records:
                if rec.get("reference_id") == reference_id:
                    target = rec
                    break
            if target is None:
                return None

            now = datetime.now(timezone.utc).isoformat()
            if action == "approve":
                target["status"], owner = "approved", "student"
            elif action == "reject":
                target["status"], owner = "rejected", "student"
            elif action == "escalate":
                owner = to or "standing_committee"
                target["status"] = f"pending_{owner}"
            else:  # request_info
                owner = "student"
                target["status"] = "info_requested"

            target["current_owner"] = owner
            target["current_owner_label"] = HIERARCHY_LABELS.get(owner, owner)
            target.setdefault("decision_log", []).append({
                "at": now, "actor": actor or "reviewer", "action": action,
                "to": owner, "to_label": HIERARCHY_LABELS.get(owner, owner), "reason": note,
            })
            self._rewrite(records)
            return target

    def review_queue(self, tenant: str = None, status: str = None) -> List[Dict]:
        """Staff queue (Layer 9): needs-action first, then by fraud risk, then oldest."""
        recs = self.list_all()
        if tenant:
            recs = [r for r in recs if r.get("tenant_id") == tenant]
        if status:
            recs = [r for r in recs if r.get("status") == status]

        def sort_key(r):
            pending = 0 if str(r.get("status", "")).startswith(("pending_", "info_requested")) else 1
            risk = -float((r.get("duplicate_check") or {}).get("risk_score", 0))
            return (pending, risk, r.get("submitted_at", ""))

        return sorted(recs, key=sort_key)

    def analytics(self, tenant: str = None) -> Dict:
        """Aggregate metrics (Layer 9) — turnaround, auto-resolution, fraud, category mix."""
        recs = self.list_all()
        if tenant:
            recs = [r for r in recs if r.get("tenant_id") == tenant]
        total = len(recs)

        by_status: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        auto_resolved = 0
        fraud_flags = 0
        turnaround_days: List[float] = []

        for r in recs:
            st = r.get("status", "unknown")
            by_status[st] = by_status.get(st, 0) + 1
            cat = r.get("initiative_label") or r.get("initiative") or "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1
            if st in ("auto_approved", "auto_rejected"):
                auto_resolved += 1
            if (r.get("duplicate_check") or {}).get("risk") in ("high", "medium"):
                fraud_flags += 1
            # turnaround for decided cases: last log time - submitted
            log = r.get("decision_log") or []
            if st in ("approved", "rejected", "auto_approved", "auto_rejected") and log:
                try:
                    t0 = datetime.fromisoformat(r["submitted_at"])
                    t1 = datetime.fromisoformat(log[-1]["at"])
                    turnaround_days.append((t1 - t0).total_seconds() / 86400.0)
                except Exception:
                    pass

        return {
            "tenant": tenant or "all",
            "total": total,
            "auto_resolved": auto_resolved,
            "auto_resolution_rate": round(auto_resolved / total, 3) if total else 0,
            "fraud_flags": fraud_flags,
            "avg_turnaround_days": round(sum(turnaround_days) / len(turnaround_days), 2) if turnaround_days else 0,
            "by_status": by_status,
            "by_category": by_category,
        }

"""
EDU Revolution 2.0 — Deterministic Rule Engine (Layer 5 core / Gap #1).

The policy's objective criteria (CGPA / attendance thresholds, revenue & stipend
matrices, required proofs, mapped benefits) are digitized here as *data*, not prose,
so eligibility is decided by explicit rules — never left to LLM judgment. Surfacing
these at intake time is what closes "eligibility invisible until after submission".

`RULE_VERSION` is stamped onto every decision for the audit trail and lets the
regression engine re-evaluate historical cases when rules change.
"""

from __future__ import annotations

from typing import Dict, List, Optional

RULE_VERSION = "2026.07"


def _f(value) -> Optional[float]:
    try:
        return float(str(value).strip().replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


# =========================================================
# Digitized policy — one entry per (initiative, benefit)
# =========================================================
# check fields:
#   min_cgpa / min_attendance  -> numeric floors (None = not required)
#   revenue_tiers              -> [(low, high, note)] mapping ₹ amount to the benefit
#   metric_required            -> the numeric field that MUST be present to auto-decide
#   objective                  -> True = fully rule-decidable; False = needs human judgement
#   required_proofs            -> proofs the student must attach
RULES: List[Dict] = [
    # ---- Revenue Generation (no min CGPA; passed courses only) ----
    {"id": "rev_course_equiv", "initiative": "revenue_generation", "benefit": "course_equivalence",
     "objective": True, "min_cgpa": None, "min_attendance": None,
     "metric_required": "revenue_amount", "revenue_min": 125000,
     "required_proofs": ["Revenue proof (invoices/bank statements)", "Undertaking Form"],
     "note": "Complete course equivalence for 1 non-core course when revenue > ₹1,25,000."},
    {"id": "rev_attendance", "initiative": "revenue_generation", "benefit": "attendance_benefit",
     "objective": True, "min_cgpa": None, "min_attendance": 60,
     "metric_required": "revenue_amount",
     "revenue_tiers": [(50000, 100000, "10% attendance"), (100000, 500000, "15% attendance"),
                       (500000, None, "20% attendance")],
     "required_proofs": ["Revenue proof (invoices/bank statements)"],
     "note": "Attendance relaxation scales with revenue; needs ≥60% attendance."},
    {"id": "rev_ca", "initiative": "revenue_generation", "benefit": "evaluation_ca_mtt",
     "objective": True, "min_cgpa": None, "min_attendance": None,
     "metric_required": "revenue_amount",
     "revenue_tiers": [(5000, 10000, "1 CA equivalence (1 course)"),
                       (10000, 25000, "1 CA equivalence (up to 2 courses)")],
     "required_proofs": ["Revenue proof"],
     "note": "CA equivalence for revenue between ₹5,000 and ₹25,000."},
    {"id": "rev_grade", "initiative": "revenue_generation", "benefit": "grade_upgradation",
     "objective": True, "min_cgpa": None, "min_attendance": None,
     "metric_required": "revenue_amount", "revenue_min": 25000,
     "required_proofs": ["Revenue proof", "Grade Upgradation Criteria Sheet mapping"],
     "note": "Grade upgradation eligible when revenue > ₹25,000 (per criteria sheet)."},

    # ---- NPTEL / MOOC / Certification ----
    {"id": "nptel_course_equiv", "initiative": "nptel_mooc_certification", "benefit": "course_equivalence",
     "objective": True, "min_cgpa": 7.0, "min_attendance": None,
     "required_proofs": ["Course registration proof", "Completion certificate", "Undertaking Form",
                         "Examination registration proof"],
     "note": "Complete course equivalence needs CGPA ≥ 7.0 (partial equivalence has no CGPA bar)."},

    # ---- Projects / Hackathons ----
    {"id": "proj_course_equiv", "initiative": "project", "benefit": "course_equivalence",
     "objective": False, "min_cgpa": 6.0, "min_attendance": None,
     "required_proofs": ["Project report", "Course mapping proposal", "Proof of participation/outcome"],
     "note": "Up to 2 courses via ETP neutral-panel evaluation; panel outcome is subjective."},
    {"id": "proj_ca", "initiative": "project", "benefit": "evaluation_ca_mtt",
     "objective": False, "min_cgpa": 6.0, "min_attendance": None,
     "required_proofs": ["Project report", "Proof of participation/outcome"],
     "note": "Up to 3 courses CA equivalence via viva-voce (subjective)."},
    {"id": "proj_duty_leave", "initiative": "project", "benefit": "duty_leave",
     "objective": True, "min_cgpa": 6.0, "min_attendance": 65,
     "required_proofs": ["Proof of participation", "Prior approval (if during class hours)"],
     "note": "10% attendance relaxation / 30 hrs Duty Leave; needs ≥65% attendance."},

    # ---- Internship Beyond Curriculum ----
    {"id": "intern_course_equiv", "initiative": "internship_beyond_curriculum", "benefit": "course_equivalence",
     "objective": True, "min_cgpa": 6.0, "min_attendance": None,
     "metric_required": "stipend_amount", "stipend_min": 100000,
     "required_proofs": ["Internship offer & completion letter", "Stipend statement"],
     "note": "Complete course waiver when total stipend > ₹1,00,000 (mapped by committee)."},
    {"id": "intern_ca", "initiative": "internship_beyond_curriculum", "benefit": "evaluation_ca_mtt",
     "objective": False, "min_cgpa": 6.0, "min_attendance": None,
     "required_proofs": ["Internship completion letter", "Stipend statement"],
     "note": "CA / grade / course benefits per stipend & duration matrix (committee-mapped)."},
    {"id": "intern_duty_leave", "initiative": "internship_beyond_curriculum", "benefit": "duty_leave",
     "objective": True, "min_cgpa": 6.0, "min_attendance": 65,
     "required_proofs": ["Internship letter", "Prior approval (if during class hours)"],
     "note": "10%/15% Duty Leave; needs ≥65% attendance."},

    # ---- RPL ----
    {"id": "rpl_recognition", "initiative": "rpl", "benefit": "rpl_recognition",
     "objective": False, "min_cgpa": None, "min_attendance": None,
     "required_proofs": ["Industry certifications", "Work-experience letters", "Prior-learning evidence"],
     "note": "Recognition subject to prescribed assessment (subjective)."},
    {"id": "rpl_course_equiv", "initiative": "rpl", "benefit": "course_equivalence",
     "objective": False, "min_cgpa": None, "min_attendance": None,
     "required_proofs": ["Industry certifications", "Assessment clearance"],
     "note": "Course equivalence via RPL assessment (subjective)."},

    # ---- Community Service ----
    {"id": "comm_grade", "initiative": "community_service", "benefit": "grade_upgradation",
     "objective": False, "min_cgpa": None, "min_attendance": None,
     "required_proofs": ["Service certificate", "Hours/impact evidence"],
     "note": "Grade upgradation / transcript recognition (committee-assessed)."},
    {"id": "comm_transcript", "initiative": "community_service", "benefit": "transcript_value_addition",
     "objective": False, "min_cgpa": None, "min_attendance": None,
     "required_proofs": ["Service certificate"],
     "note": "Transcript value-addition for approved community service."},
]

_RULE_INDEX = {(r["initiative"], r["benefit"]): r for r in RULES}


class RuleEngine:
    """Evaluate a nomination against the digitized policy. No LLM involved."""

    version = RULE_VERSION

    def rule_for(self, initiative: str, benefit: str) -> Optional[Dict]:
        return _RULE_INDEX.get((initiative, benefit))

    def rules_for_initiative(self, initiative: str) -> List[Dict]:
        return [r for r in RULES if r["initiative"] == initiative]

    def evaluate(self, nom: Dict) -> Dict:
        """
        Returns a structured evaluation:
          matched, rule_id, rule_version, objective, eligible,
          checks[{name, required, actual, passed}], required_proofs,
          mapped_benefit (resolved tier text), reasons[], missing_metric
        """
        initiative = nom.get("initiative")
        benefit = nom.get("academic_benefit")
        rule = self.rule_for(initiative, benefit)

        if not rule:
            return {
                "matched": False, "rule_id": None, "rule_version": self.version,
                "objective": False, "eligible": False, "checks": [],
                "required_proofs": [], "mapped_benefit": None, "missing_metric": None,
                "reasons": [f"No digitized rule for {initiative} → {benefit}; needs human review."],
            }

        checks: List[Dict] = []
        reasons: List[str] = []
        cgpa = _f(nom.get("cgpa"))
        attendance = _f(nom.get("attendance_percent"))
        missing_metric = None

        # CGPA floor
        if rule.get("min_cgpa") is not None:
            ok = cgpa is not None and cgpa >= rule["min_cgpa"]
            checks.append({"name": "Minimum CGPA", "required": rule["min_cgpa"], "actual": cgpa, "passed": ok})
            if not ok:
                reasons.append(f"CGPA {cgpa if cgpa is not None else '—'} is below the required {rule['min_cgpa']}.")

        # Attendance floor
        if rule.get("min_attendance") is not None:
            ok = attendance is not None and attendance >= rule["min_attendance"]
            checks.append({"name": "Minimum attendance %", "required": rule["min_attendance"],
                           "actual": attendance, "passed": ok})
            if not ok:
                reasons.append(f"Attendance {attendance if attendance is not None else '—'}% is below "
                               f"the required {rule['min_attendance']}%.")

        mapped_benefit = rule.get("note")

        # Single numeric minimum (revenue_min / stipend_min)
        for metric, key in (("revenue_amount", "revenue_min"), ("stipend_amount", "stipend_min")):
            if key in rule:
                val = _f(nom.get(metric))
                if val is None:
                    missing_metric = metric
                    checks.append({"name": f"{metric.replace('_', ' ')}", "required": f"≥ ₹{rule[key]:,}",
                                   "actual": None, "passed": False})
                    reasons.append(f"Provide the {metric.replace('_', ' ')} to auto-verify this benefit.")
                else:
                    ok = val >= rule[key]
                    checks.append({"name": f"{metric.replace('_', ' ')}", "required": f"≥ ₹{rule[key]:,}",
                                   "actual": val, "passed": ok})
                    if not ok:
                        reasons.append(f"{metric.replace('_', ' ')} ₹{val:,.0f} is below the required ₹{rule[key]:,}.")

        # Tiered revenue → resolves the exact sub-benefit
        if "revenue_tiers" in rule:
            val = _f(nom.get("revenue_amount"))
            if val is None:
                missing_metric = "revenue_amount"
                checks.append({"name": "revenue amount", "required": "tiered", "actual": None, "passed": False})
                reasons.append("Provide the revenue amount to map the exact benefit tier.")
            else:
                tier = self._match_tier(val, rule["revenue_tiers"])
                if tier:
                    mapped_benefit = f"{tier} (revenue ₹{val:,.0f})"
                    checks.append({"name": "revenue tier", "required": "within a defined tier",
                                   "actual": f"₹{val:,.0f}", "passed": True})
                else:
                    checks.append({"name": "revenue tier", "required": "within a defined tier",
                                   "actual": f"₹{val:,.0f}", "passed": False})
                    reasons.append(f"Revenue ₹{val:,.0f} does not fall in any benefit tier for this category.")

        objective = bool(rule.get("objective"))
        # Eligible only if every check that COULD be evaluated passed, and no metric is missing.
        evaluable = [c for c in checks if c["actual"] is not None or c["required"] == "tiered"]
        eligible = missing_metric is None and all(c["passed"] for c in checks)

        return {
            "matched": True, "rule_id": rule["id"], "rule_version": self.version,
            "objective": objective, "eligible": eligible, "checks": checks,
            "required_proofs": rule.get("required_proofs", []),
            "mapped_benefit": mapped_benefit, "missing_metric": missing_metric,
            "reasons": reasons or (["All objective criteria met."] if eligible else []),
        }

    @staticmethod
    def _match_tier(value: float, tiers) -> Optional[str]:
        for low, high, label in tiers:
            if value >= low and (high is None or value < high):
                return label
        return None


# Human labels for the structured metric fields.
METRIC_LABELS = {
    "revenue_amount": "Revenue amount (₹)",
    "stipend_amount": "Stipend (₹ / month)",
    "duration_months": "Duration (months)",
}


def required_metric(initiative: str, benefit: str) -> Optional[str]:
    """The structured field that is COMPULSORY for this (initiative, benefit), if any."""
    rule = _RULE_INDEX.get((initiative, benefit))
    return rule.get("metric_required") if rule else None


def field_requirements() -> Dict[str, str]:
    """Map "initiative|benefit" -> the compulsory metric field (for the form)."""
    reqs = {}
    for r in RULES:
        m = r.get("metric_required")
        if m:
            reqs[f"{r['initiative']}|{r['benefit']}"] = m
    return reqs


def rules_catalog() -> List[Dict]:
    """Public, student-facing view of the digitized rules (for intake transparency)."""
    return [
        {
            "id": r["id"], "initiative": r["initiative"], "benefit": r["benefit"],
            "objective": r["objective"], "min_cgpa": r.get("min_cgpa"),
            "min_attendance": r.get("min_attendance"),
            "metric_required": r.get("metric_required"),
            "required_proofs": r.get("required_proofs", []), "note": r.get("note", ""),
        }
        for r in RULES
    ]

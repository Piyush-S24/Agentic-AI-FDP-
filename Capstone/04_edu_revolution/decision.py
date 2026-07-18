"""
EDU Revolution 2.0 — Confidence-Scored Decision Engine (Layer 5 & 6).

Combines the deterministic rule match + proof verification + duplicate check into
a single outcome and confidence, then routes it into the escalation hierarchy with
an SLA:

    auto_approve   — objective rule met, proof present, no duplicate risk
    auto_reject    — an objective rule is clearly failed (with a cited reason)
    escalate       — subjective category, missing proof/metric, or a fraud signal

Eligibility is NEVER decided by an LLM — only by these explicit signals.
"""

from __future__ import annotations

from typing import Dict, List

# Escalation hierarchy (maps to the existing RMS-style structure).
HIERARCHY = ["student", "school_coordinator", "standing_committee", "registrar"]

HIERARCHY_LABELS = {
    "student": "Student",
    "school_coordinator": "School Edu-Rev Coordinator",
    "standing_committee": "Standing Committee",
    "registrar": "Registrar / Admin",
}

# SLA (working days) by the level a case currently sits at.
SLA_DAYS = {
    "auto": 0,
    "school_coordinator": 5,
    "standing_committee": 10,
    "registrar": 15,
}


class DecisionEngine:
    """Turn the three signal blocks into one routed, scored decision."""

    def decide(self, nom: Dict, rule_eval: Dict, verification: Dict, duplicate: Dict) -> Dict:
        signals = {
            "rule": {"matched": rule_eval.get("matched"), "objective": rule_eval.get("objective"),
                     "eligible": rule_eval.get("eligible"), "missing_metric": rule_eval.get("missing_metric")},
            "verification": {"confidence": verification.get("confidence"),
                             "needs_human": verification.get("needs_human"),
                             "verified_count": verification.get("verified_count")},
            "duplicate": {"risk": duplicate.get("risk"), "risk_score": duplicate.get("risk_score")},
        }

        dup_risk = duplicate.get("risk")
        # 1) Fraud signal always wins — escalate to the committee with the evidence.
        if dup_risk == "high":
            return self._escalate(
                "standing_committee", 0.9,
                "Possible fraud: a proof identifier is shared with another student. Escalated with evidence.",
                signals)

        # 2) No digitized rule / subjective category → human judgement.
        if not rule_eval.get("matched"):
            return self._escalate(
                "school_coordinator", 0.4,
                "No digitized rule matches this category/benefit — needs manual review.", signals)

        # 3) Objective rule clearly failed → auto-reject with a cited reason.
        if rule_eval.get("objective") and not rule_eval.get("eligible") \
                and not rule_eval.get("missing_metric"):
            reason = "; ".join(rule_eval.get("reasons", [])) or "Objective eligibility criteria not met."
            return {
                "outcome": "auto_reject", "confidence": 0.9,
                "reason": f"Auto-rejected: {reason}",
                "escalate_to": "student", "sla_days": SLA_DAYS["auto"], "signals": signals,
            }

        # 4) Missing metric or unverifiable proof → escalate for completion/verification.
        if rule_eval.get("missing_metric") or verification.get("needs_human"):
            bits = []
            if rule_eval.get("missing_metric"):
                bits.append(f"missing {rule_eval['missing_metric'].replace('_', ' ')}")
            if verification.get("needs_human"):
                bits.append("no verifiable proof identifier")
            return self._escalate(
                "school_coordinator", 0.55,
                "Needs a reviewer: " + ", ".join(bits) + ".", signals)

        # 5) Subjective (but rule-matched & eligible) → route to the right human level.
        if not rule_eval.get("objective"):
            level = "standing_committee" if nom.get("initiative") in ("project", "rpl") else "school_coordinator"
            return self._escalate(
                level, 0.6,
                "Rule prerequisites met; final benefit is a subjective/panel decision.", signals)

        # 6) Objective + eligible + proof present + no duplicate → auto-approve.
        confidence = round(min(0.98, 0.7 + 0.3 * float(verification.get("confidence") or 0)), 2)
        mapped = rule_eval.get("mapped_benefit") or "the mapped benefit"
        note = " (low-risk prior reference noted)" if dup_risk in ("low", "medium") else ""
        return {
            "outcome": "auto_approve", "confidence": confidence,
            "reason": f"Auto-approved: all objective criteria met and proof present — {mapped}{note}.",
            "escalate_to": "student", "sla_days": SLA_DAYS["auto"], "signals": signals,
        }

    @staticmethod
    def _escalate(level: str, confidence: float, reason: str, signals: Dict) -> Dict:
        return {
            "outcome": "escalate", "confidence": confidence, "reason": reason,
            "escalate_to": level, "sla_days": SLA_DAYS.get(level, 10), "signals": signals,
        }


def status_for(decision: Dict) -> str:
    """Map a decision to a persisted nomination status."""
    outcome = decision.get("outcome")
    if outcome == "auto_approve":
        return "auto_approved"
    if outcome == "auto_reject":
        return "auto_rejected"
    return f"pending_{decision.get('escalate_to', 'school_coordinator')}"

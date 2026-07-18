"""
EDU Revolution 2.0 — Proactive Nudge Agent (Gap #9: reactive → proactive).

Eligible students who never open the app never claim their benefit. Using the
authoritative record from the Student Directory, this agent checks which benefit
pathways a student ALREADY clears on the academic prerequisites (CGPA / attendance
floors) and suggests them — turning "come find out" into "you qualify for X, here's
how". It only reads rule data + the directory; no LLM.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from rules import RULES, _f  # noqa: reuse the numeric parser


def _labels():
    # local import avoids a hard dependency cycle at module import time
    from registration import _INITIATIVE_LABELS, _BENEFIT_LABELS
    return _INITIATIVE_LABELS, _BENEFIT_LABELS


def find_opportunities(record: Dict) -> Dict:
    """
    Given a student's authoritative record, return the benefit pathways whose
    academic prerequisites (CGPA / attendance) they already meet.
    """
    init_labels, benefit_labels = _labels()
    cgpa = _f(record.get("cgpa"))
    att = _f(record.get("attendance_percent"))

    seen = set()
    opportunities: List[Dict] = []
    for r in RULES:
        cgpa_ok = r.get("min_cgpa") is None or (cgpa is not None and cgpa >= r["min_cgpa"])
        att_ok = r.get("min_attendance") is None or (att is not None and att >= r["min_attendance"])
        if not (cgpa_ok and att_ok):
            continue
        key = (r["initiative"], r["benefit"])
        if key in seen:
            continue
        seen.add(key)
        opportunities.append({
            "initiative": r["initiative"],
            "initiative_label": init_labels.get(r["initiative"], r["initiative"]),
            "benefit": r["benefit"],
            "benefit_label": benefit_labels.get(r["benefit"], r["benefit"]),
            "min_cgpa": r.get("min_cgpa"),
            "min_attendance": r.get("min_attendance"),
            "note": r.get("note", ""),
            "needs": f"a qualifying {init_labels.get(r['initiative'], r['initiative'])} activity + its proofs",
        })

    # A concrete headline signal: the 10% attendance benefit has a clear CGPA gate.
    highlights = [o for o in opportunities if o["benefit"] == "attendance_benefit"]

    return {
        "registration_id": record.get("registration_id"),
        "name": record.get("name"),
        "cgpa": cgpa, "attendance_percent": att,
        "eligible_count": len(opportunities),
        "opportunities": opportunities,
        "highlight": (f"You clear the CGPA bar for the {highlights[0]['benefit_label']} "
                      "— pair it with a qualifying activity to claim it.") if highlights else
                     (f"You clear the prerequisites for {len(opportunities)} benefit pathway(s)."
                      if opportunities else "No pathway prerequisites cleared yet — raise CGPA/attendance to unlock benefits."),
    }


def scan_all(records: List[Dict]) -> Dict:
    """Directory-wide view for staff: who to nudge, and how many pathways each clears."""
    rows = []
    for rec in records:
        opp = find_opportunities(rec)
        rows.append({
            "registration_id": rec.get("registration_id"), "name": rec.get("name"),
            "program": rec.get("program"), "cgpa": rec.get("cgpa"),
            "attendance_percent": rec.get("attendance_percent"),
            "eligible_count": opp["eligible_count"],
            "top": [o["benefit_label"] for o in opp["opportunities"][:3]],
        })
    rows.sort(key=lambda r: r["eligible_count"], reverse=True)
    return {"total_students": len(rows),
            "with_opportunities": sum(1 for r in rows if r["eligible_count"]),
            "students": rows}

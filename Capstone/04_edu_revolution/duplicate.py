"""
EDU Revolution 2.0 — Duplicate & Regression Engine (Layer 4 / Gap #4).

Cross-checks a new nomination against (a) the same student's own history — to
recognise legitimate stage progression (patent filed → published → granted) vs a
double-claim — and (b) every other student's submissions, where a *shared* proof
identifier is a strong fraud signal.

`ProofVerifier` supplies the identifiers; this engine only compares them, so it
never calls the LLM or the network.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List

from verification import ProofVerifier

_verifier = ProofVerifier()


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (text or "").lower())).strip()


def _identifier_set(nom: Dict) -> set:
    blob = " ".join(str(nom.get(k, "")) for k in
                    ("activity_title", "activity_description", "supporting_documents",
                     "proof_links", "proof_text"))
    ids = _verifier.extract_identifiers(blob)
    flat = set()
    for kind, values in ids.items():
        for v in values:
            flat.add(f"{kind}:{v.lower()}")
    # The uploaded file itself is an identifier: the SAME document (byte-identical)
    # submitted by two students is the strongest fraud signal we have.
    for pf in (nom.get("proof_files") or []):
        sha = (pf or {}).get("sha256")
        if sha:
            flat.add(f"file:{sha.lower()}")
    # For already-stored nominations, reuse the identifiers the verifier found at filing
    # time (these include ones extracted from inside uploaded PDFs).
    for kind, values in ((nom.get("verification") or {}).get("identifiers") or {}).items():
        for v in values:
            flat.add(f"{kind}:{str(v).lower()}")
    return flat


class DuplicateChecker:
    """Compare a nomination against previously filed ones."""

    def __init__(self, records_provider: Callable[[], List[Dict]]):
        # A callable returning all stored records — avoids importing the store here.
        self._all = records_provider

    def check(self, nom: Dict, exclude_ref: str = None) -> Dict:
        reg_id = str(nom.get("registration_id", "")).strip().lower()
        new_ids = _identifier_set(nom)
        new_title = _norm_title(nom.get("activity_title", ""))

        own_matches: List[Dict] = []
        cross_matches: List[Dict] = []

        for rec in self._all():
            if exclude_ref and rec.get("reference_id") == exclude_ref:
                continue
            rec_ids = _identifier_set(rec)
            shared = new_ids & rec_ids
            same_title = new_title and _norm_title(rec.get("activity_title", "")) == new_title
            if not shared and not same_title:
                continue

            same_student = str(rec.get("registration_id", "")).strip().lower() == reg_id
            entry = {
                "reference_id": rec.get("reference_id"),
                "registration_id": rec.get("registration_id"),
                "shared_identifiers": sorted(shared),
                "same_title": same_title,
                "benefit": rec.get("academic_benefit"),
                "stage": rec.get("achievement_stage"),
            }
            (own_matches if same_student else cross_matches).append(entry)

        reasons: List[str] = []
        # Cross-student shared proof = the strongest fraud signal.
        if any(m["shared_identifiers"] for m in cross_matches):
            risk, score = "high", 0.95
            reasons.append("A proof identifier on this nomination is already used by ANOTHER student — "
                           "possible fraudulent/duplicate claim.")
        elif cross_matches:
            risk, score = "medium", 0.6
            reasons.append("Another student has a submission with the same activity title — review for overlap.")
        elif any(m["shared_identifiers"] for m in own_matches):
            risk, score = "low", 0.3
            reasons.append("You have already referenced this proof — if this is a new stage of the same "
                           "achievement that's fine; if it's the same claim it will be de-duplicated.")
        elif own_matches:
            risk, score = "low", 0.2
            reasons.append("You previously submitted a nomination with this title (likely the same achievement).")
        else:
            risk, score = "none", 0.0

        return {
            "risk": risk, "risk_score": score,
            "own_matches": own_matches, "cross_matches": cross_matches,
            "reasons": reasons,
        }

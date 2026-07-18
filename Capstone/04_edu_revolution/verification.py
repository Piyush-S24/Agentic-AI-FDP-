"""
EDU Revolution 2.0 — Verification / Anti-Fraud Agent (Layer 3 / Gap #3).

Extracts verifiable identifiers from a nomination's proof text (patent numbers,
DOIs, certificate/NPTEL IDs, URLs) and validates their FORMAT. Format-valid
identifiers are marked ``pending_external`` — the hooks to confirm them against the
real issuing sources (Indian Patent Office, DOI resolver / IEEE Xplore, NPTEL
portal, exam authorities) are defined in ``VERIFICATION_SOURCES`` and
``verify_external`` so a follow-up integration only wires the network call.

Nominations whose claimed proof yields no verifiable identifier are flagged
``needs_human`` and routed to a reviewer automatically.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List

logger = logging.getLogger("edu_revolution.verification")

# Live external confirmation is OFF by default (makes network calls). Turn on with
# EDUREV_LIVE_VERIFY=true. DOI resolution needs no credentials; patent/NPTEL sources
# would be wired the same way (their endpoints/keys go here).
LIVE_VERIFY = os.getenv("EDUREV_LIVE_VERIFY", "false").strip().lower() in ("1", "true", "yes")

# identifier type -> (regex, issuing source that would confirm it live)
VERIFICATION_SOURCES: Dict[str, Dict] = {
    "doi": {
        "regex": re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I),
        "source": "DOI resolver / IEEE Xplore / publisher",
    },
    "patent": {
        "regex": re.compile(r"\b(?:patent\s*(?:no\.?|number)?\s*[:#]?\s*)?((?:IN|US|EP|WO)?\s?\d{6,}(?:[-/]?[A-Z0-9]{1,4})?)\b", re.I),
        "source": "Indian Patent Office / patent status portal",
        "context": re.compile(r"patent", re.I),  # only treat as patent if 'patent' is nearby
    },
    "nptel": {
        "regex": re.compile(r"\bNPTEL[0-9A-Z]{2,}\d{2,}\b", re.I),
        "source": "NPTEL / SWAYAM portal",
    },
    "certificate": {
        "regex": re.compile(r"\b(?:cert(?:ificate)?\s*(?:id|no\.?|number)?\s*[:#]\s*)([A-Z0-9][A-Z0-9\-]{5,})\b", re.I),
        "source": "Issuing certification authority",
    },
    "url": {
        "regex": re.compile(r"https?://[^\s)]+", re.I),
        "source": "Live URL fetch",
    },
}


class ProofVerifier:
    """Deterministic proof identifier extraction + format validation."""

    def extract_identifiers(self, text: str) -> Dict[str, List[str]]:
        text = text or ""
        found: Dict[str, List[str]] = {}
        for kind, spec in VERIFICATION_SOURCES.items():
            if "context" in spec and not spec["context"].search(text):
                continue  # e.g. only extract 'patent' numbers when the word patent appears
            matches = []
            for m in spec["regex"].finditer(text):
                val = (m.group(1) if m.groups() else m.group(0)).strip()
                if val and val not in matches:
                    matches.append(val)
            if matches:
                found[kind] = matches
        return found

    def verify(self, nom: Dict) -> Dict:
        """
        Returns:
          identifiers, checks[{type, value, source, status}], verified_count,
          needs_human, confidence (0..1), notes[]
        status ∈ {"format_valid" (a.k.a. pending external), "invalid"}
        """
        # Includes `proof_text` — text extracted from the student's UPLOADED documents,
        # so identifiers inside the actual certificate/statement are found too.
        blob = " ".join(str(nom.get(k, "")) for k in
                        ("activity_title", "activity_description", "supporting_documents",
                         "proof_links", "proof_text"))
        identifiers = self.extract_identifiers(blob)

        checks: List[Dict] = []
        for kind, values in identifiers.items():
            source = VERIFICATION_SOURCES[kind]["source"]
            for val in values:
                check = {
                    "type": kind, "value": val, "source": source,
                    "status": "pending_external",  # format valid; live check is the extension point
                }
                if LIVE_VERIFY:
                    check["status"] = self.verify_external(check)
                checks.append(check)

        # An attached document is itself evidence — note it and lift confidence a little.
        attached = nom.get("proof_files") or []
        verified_count = len(checks)
        notes: List[str] = []
        if verified_count == 0:
            if attached:
                notes.append(f"{len(attached)} document(s) attached but no machine-verifiable identifier "
                             "(DOI / patent no. / certificate ID / URL) was found in them — needs a human reviewer.")
                confidence = 0.4
            else:
                notes.append("No verifiable identifier (DOI / patent no. / certificate ID / URL) and no attached "
                             "document — routed to a human reviewer.")
                confidence = 0.2
            needs_human = True
        else:
            kinds = sorted({c["type"] for c in checks})
            notes.append(f"Found {verified_count} format-valid identifier(s): {', '.join(kinds)}. "
                         "Pending live confirmation with the issuing source.")
            # Confidence rises with distinct identifiers, and again if a document backs them up.
            confidence = min(0.9, 0.5 + 0.15 * len(kinds) + (0.1 if attached else 0))
            needs_human = False

        if attached:
            notes.append(f"{len(attached)} proof document(s) attached: "
                         + ", ".join(str(a.get('filename', '?')) for a in attached[:5]) + ".")

        return {
            "identifiers": identifiers, "checks": checks, "verified_count": verified_count,
            "attached_documents": len(attached),
            "needs_human": needs_human, "confidence": round(confidence, 2), "notes": notes,
        }

    # --- Live external confirmation (enabled by EDUREV_LIVE_VERIFY) ---
    def verify_external(self, check: Dict) -> str:
        """
        Confirm one identifier against its live issuing source; returns a status:
        "verified" / "not_found" / "pending_external" (unsupported/error).

        DOI is implemented (credential-free via doi.org). Patent office / NPTEL /
        exam authorities plug in here the same way (add their endpoint + parsing).
        """
        kind, value = check.get("type"), check.get("value", "")
        try:
            if kind == "doi":
                return self._verify_doi(value)
            # TODO: patent -> Indian Patent Office status API; nptel -> NPTEL/SWAYAM; url -> HEAD
            return "pending_external"
        except Exception as e:  # network/parse failure → stay pending, never crash a filing
            logger.info(f"External verify ({kind}) failed: {e}")
            return "pending_external"

    @staticmethod
    def _verify_doi(doi: str) -> str:
        """Resolve a DOI via the credential-free doi.org handle API."""
        import json
        import urllib.request
        import urllib.error
        url = f"https://doi.org/api/handles/{doi}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            return "verified" if data.get("responseCode") == 1 else "not_found"
        except urllib.error.HTTPError as e:
            return "not_found" if e.code == 404 else "pending_external"

"""
EDU Revolution 2.0 — Student Directory connector (Gap: self-reported data).

The single most important upgrade: stop trusting numbers the student types. This
connector is the authoritative source for a student's identity and academic record
(CGPA, attendance, program, year). With it, the rule engine decides on real data and
the fake-CGPA loophole closes.

`StudentDirectory` here reads a local JSON directory (seeded with sample students so
the demo runs offline). To go live, subclass it and override `get` / `all_records` /
`verify` to call the real UMS/LMS student API — nothing else in the app changes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from config import DATA_DIR

logger = logging.getLogger("edu_revolution.directory")

# Seed directory (stand-in for the college UMS). `secret` is a second factor used for
# identity verification (here a date of birth); a real integration would use SSO/UMS auth.
_SEED: List[Dict] = [
    {"registration_id": "12100001", "name": "Aarav Sharma", "program": "B.Tech CSE",
     "school": "School of Computer Science & Engineering", "year_of_study": "3rd Year (Pre-Final)",
     "cgpa": 8.2, "attendance_percent": 78, "email": "aarav.12100001@lpu.in", "secret": "2005-03-14"},
    {"registration_id": "12100002", "name": "Diya Patel", "program": "B.Tech ECE",
     "school": "School of Electronics & Electrical Engineering", "year_of_study": "4th Year (Final)",
     "cgpa": 7.6, "attendance_percent": 84, "email": "diya.12100002@lpu.in", "secret": "2004-07-22"},
    {"registration_id": "12100003", "name": "Rohan Mehta", "program": "BBA",
     "school": "Mittal School of Business", "year_of_study": "2nd Year",
     "cgpa": 6.4, "attendance_percent": 71, "email": "rohan.12100003@lpu.in", "secret": "2006-01-09"},
    {"registration_id": "12100004", "name": "Sara Khan", "program": "B.Tech CSE",
     "school": "School of Computer Science & Engineering", "year_of_study": "3rd Year (Pre-Final)",
     "cgpa": 7.9, "attendance_percent": 61, "email": "sara.12100004@lpu.in", "secret": "2005-11-30"},
    {"registration_id": "12100005", "name": "Ishaan Gupta", "program": "B.Sc (Hons) Physics",
     "school": "School of Chemical Engineering & Physical Sciences", "year_of_study": "1st Year",
     "cgpa": 9.1, "attendance_percent": 88, "email": "ishaan.12100005@lpu.in", "secret": "2007-05-18"},
    {"registration_id": "12100006", "name": "Ananya Rao", "program": "B.Tech ME",
     "school": "School of Mechanical Engineering", "year_of_study": "4th Year (Final)",
     "cgpa": 5.8, "attendance_percent": 55, "email": "ananya.12100006@lpu.in", "secret": "2004-09-02"},
    {"registration_id": "12100007", "name": "Kabir Singh", "program": "MBA",
     "school": "Mittal School of Business", "year_of_study": "2nd Year",
     "cgpa": 8.7, "attendance_percent": 91, "email": "kabir.12100007@lpu.in", "secret": "2002-12-25"},
    {"registration_id": "12100008", "name": "Meera Nair", "program": "B.Tech CSE",
     "school": "School of Computer Science & Engineering", "year_of_study": "3rd Year (Pre-Final)",
     "cgpa": 7.5, "attendance_percent": 75, "email": "meera.12100008@lpu.in", "secret": "2005-06-11"},
]

# Fields safe to return to a client (never the `secret`).
_PUBLIC_FIELDS = ("registration_id", "name", "program", "school", "year_of_study",
                  "cgpa", "attendance_percent", "email")


class StudentDirectory:
    """Authoritative student records. Swap for a real UMS API by subclassing."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else (DATA_DIR / "students.json")
        if not self.path.exists():
            self._seed()
        self._cache: Optional[Dict[str, Dict]] = None

    def _seed(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(_SEED, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Seeded student directory with {len(_SEED)} sample students at {self.path}")

    def _load(self) -> Dict[str, Dict]:
        if self._cache is None:
            try:
                records = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Directory load failed ({e}); using seed.")
                records = _SEED
            self._cache = {str(r["registration_id"]): r for r in records}
        return self._cache

    # ---- public API (override these for a real UMS integration) ----
    def get(self, registration_id: str) -> Optional[Dict]:
        """Authoritative record (without the secret), or None if unknown."""
        rec = self._load().get(str(registration_id or "").strip())
        return {k: rec[k] for k in _PUBLIC_FIELDS if k in rec} if rec else None

    def all_records(self) -> List[Dict]:
        return [{k: r[k] for k in _PUBLIC_FIELDS if k in r} for r in self._load().values()]

    def verify(self, registration_id: str, secret: str) -> Optional[Dict]:
        """Identity check: registration id + secret (e.g. DOB). Returns the record or None."""
        rec = self._load().get(str(registration_id or "").strip())
        if rec and str(secret or "").strip() and str(rec.get("secret")) == str(secret).strip():
            return {k: rec[k] for k in _PUBLIC_FIELDS if k in rec}
        return None

    def exists(self, registration_id: str) -> bool:
        return str(registration_id or "").strip() in self._load()

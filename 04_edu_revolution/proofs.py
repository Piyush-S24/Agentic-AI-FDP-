"""
EDU Revolution 2.0 — Proof document store.

Students attach their own evidence to a nomination (certificate, revenue statement,
stipend letter, patent PDF …). Each upload is validated, hashed (SHA-256) and — for
PDFs — its text is extracted so the Verification Agent can find real identifiers
(DOI / patent no. / certificate ID) *inside the document* rather than only in what
the student typed.

The SHA-256 also feeds the Duplicate & Fraud engine: the **same file** submitted by
two different students is a strong fraud signal.

Files live in ``data/proofs/`` (git-ignored — student PII) with a JSONL index so the
server, not the client, is the source of truth for filename/hash/extracted text.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config import DATA_DIR

logger = logging.getLogger("edu_revolution.proofs")

ALLOWED_PROOF_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx"}
MAX_PROOF_MB = 10
MAX_EXTRACT_CHARS = 8000


class ProofError(ValueError):
    """Raised when an uploaded proof file is rejected."""


def _safe_name(filename: str) -> str:
    name = Path(filename or "file").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "file"
    return name[:80]


def _extract_pdf_text(content: bytes) -> str:
    """Best-effort text extraction from a PDF."""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages[:20]:  # cap: proofs are short
            try:
                t = page.extract_text()
                if t:
                    parts.append(t)
            except Exception:
                continue
        return "\n".join(parts)[:MAX_EXTRACT_CHARS]
    except Exception as e:
        logger.info(f"PDF text extraction skipped: {e}")
        return ""


def _extract_image_text(content: bytes) -> str:
    """
    OCR an image proof (screenshot of a certificate, etc.) so its identifiers are
    readable. Requires the Tesseract binary + `pytesseract` + `Pillow`; if they're
    not installed the file is still stored — OCR just yields nothing (graceful).
    """
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(content))
        return (pytesseract.image_to_string(img) or "")[:MAX_EXTRACT_CHARS]
    except Exception as e:
        logger.info(f"Image OCR unavailable ({e}); stored without text. "
                    "Install Tesseract + pytesseract + Pillow to enable.")
        return ""


class ProofStore:
    """Validated storage + index for student proof documents."""

    def __init__(self, directory: Optional[Path] = None):
        self.dir = Path(directory) if directory else (DATA_DIR / "proofs")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.jsonl"
        self._lock = threading.Lock()

    # ---------- index ----------
    def _all(self) -> List[Dict]:
        if not self.index_path.exists():
            return []
        out = []
        with open(self.index_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def get(self, proof_id: str) -> Optional[Dict]:
        for rec in self._all():
            if rec.get("proof_id") == proof_id:
                return rec
        return None

    def path_for(self, proof_id: str) -> Optional[Path]:
        rec = self.get(proof_id)
        if not rec:
            return None
        p = self.dir / rec["stored_as"]
        return p if p.exists() else None

    # ---------- save ----------
    def save(self, filename: str, content: bytes) -> Dict:
        """Validate + store one proof file. Returns its index record."""
        ext = Path(filename or "").suffix.lower()
        if ext not in ALLOWED_PROOF_EXTENSIONS:
            raise ProofError(
                f"'{filename}': unsupported type '{ext or '?'}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_PROOF_EXTENSIONS))}."
            )
        if not content:
            raise ProofError(f"'{filename}' is empty.")
        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_PROOF_MB:
            raise ProofError(f"'{filename}' is {size_mb:.1f}MB — the limit is {MAX_PROOF_MB}MB.")

        sha256 = hashlib.sha256(content).hexdigest()
        proof_id = f"PRF-{uuid.uuid4().hex[:10]}"
        stored_as = f"{proof_id}_{_safe_name(filename)}"

        if ext == ".pdf":
            text = _extract_pdf_text(content)
        elif ext in (".png", ".jpg", ".jpeg", ".webp"):
            text = _extract_image_text(content)  # OCR (best-effort)
        else:
            text = ""

        with self._lock:
            # Re-ensure the folder exists (it may have been cleared while running).
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / stored_as).write_bytes(content)
            record = {
                "proof_id": proof_id,
                "filename": _safe_name(filename),
                "stored_as": stored_as,
                "size_bytes": len(content),
                "sha256": sha256,
                "content_type": ext.lstrip("."),
                "extracted_text": text,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.index_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(f"Stored proof {proof_id} ({record['filename']}, {len(content)} bytes, "
                    f"{len(text)} chars extracted)")
        return record

    @staticmethod
    def public(record: Dict) -> Dict:
        """The client-safe view (no extracted text blob)."""
        return {
            "proof_id": record["proof_id"], "filename": record["filename"],
            "size_bytes": record["size_bytes"], "sha256": record["sha256"],
            "content_type": record.get("content_type", ""),
            "extracted_chars": len(record.get("extracted_text") or ""),
        }

    def resolve(self, proof_ids: List[str]) -> List[Dict]:
        """Turn client-supplied proof ids into trusted server-side records."""
        out = []
        for pid in proof_ids:
            rec = self.get(str(pid))
            if rec:
                out.append(rec)
        return out

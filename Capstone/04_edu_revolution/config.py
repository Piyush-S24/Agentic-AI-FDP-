"""
EdU Revolution — Configuration Management
Loads environment variables and provides app-wide settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# === Base Paths ===
BASE_DIR = Path(__file__).resolve().parent

# Load the project's .env explicitly (by absolute path) so the config is identical
# no matter which working directory the app/scripts are launched from.
load_dotenv(BASE_DIR / ".env")


def _project_path(env_name: str, default_subdir: str) -> Path:
    """
    Resolve a configurable directory. A *relative* value (e.g. ``./chroma_db``)
    is anchored to the PROJECT folder, never the current working directory —
    so the knowledge base is always found regardless of where you run from.
    """
    value = os.getenv(env_name, "").strip()
    p = Path(value) if value else (BASE_DIR / default_subdir)
    return p if p.is_absolute() else (BASE_DIR / p).resolve()


UPLOAD_DIR = _project_path("UPLOAD_DIR", "uploads")
CHROMA_DB_PATH = _project_path("CHROMA_DB_PATH", "chroma_db")
DATA_DIR = _project_path("DATA_DIR", "data")
STATIC_DIR = BASE_DIR / "static"

# Ensure directories exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DB_PATH.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# === Groq API ===
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# === Embedding Model ===
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# === Text Splitting ===
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# === ChromaDB Collection ===
CHROMA_COLLECTION_NAME = "edu_revolution_knowledge"

# === Validation ===
ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE_MB = 50

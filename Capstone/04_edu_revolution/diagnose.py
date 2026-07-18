"""
EdU Revolution - Diagnostic Script
Run this: python diagnose.py
It will check every dependency and report what's wrong.
"""
import sys
print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print()

errors = []

# Check each dependency
deps = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "dotenv": "python-dotenv",
    "multipart": "python-multipart",
    "groq": "groq",
    "chromadb": "chromadb",
    "PyPDF2": "PyPDF2",
    "sentence_transformers": "sentence-transformers",
    "langchain_text_splitters": "langchain-text-splitters",
    "jinja2": "jinja2",
    "aiofiles": "aiofiles",
    "pydantic": "pydantic",
}

print("=" * 50)
print("DEPENDENCY CHECK")
print("=" * 50)

missing = []
for module_name, pip_name in deps.items():
    try:
        mod = __import__(module_name)
        ver = getattr(mod, "__version__", "OK")
        print(f"  ✅ {pip_name:30s} v{ver}")
    except ImportError as e:
        print(f"  ❌ {pip_name:30s} NOT INSTALLED")
        missing.append(pip_name)
    except Exception as e:
        print(f"  ⚠️ {pip_name:30s} Error: {e}")
        errors.append(f"{pip_name}: {e}")

print()

if missing:
    print("=" * 50)
    print("MISSING PACKAGES - Run this command:")
    print("=" * 50)
    print(f"\n  pip install {' '.join(missing)}\n")
    errors.append(f"Missing packages: {', '.join(missing)}")

# Check .env
print("=" * 50)
print("CONFIGURATION CHECK")
print("=" * 50)

import os
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    print(f"  ✅ .env file found")
    # Check API key
    with open(env_path) as f:
        content = f.read()
    if "your_groq_api_key_here" in content:
        print(f"  ❌ GROQ_API_KEY is still the placeholder - edit .env!")
        errors.append("API key not set")
    elif "GROQ_API_KEY=" in content:
        print(f"  ✅ GROQ_API_KEY is set")
    else:
        print(f"  ❌ GROQ_API_KEY not found in .env")
        errors.append("API key missing from .env")
else:
    print(f"  ❌ .env file NOT FOUND - copy .env.example to .env")
    errors.append(".env file missing")

# Check static files
static_dir = Path(__file__).parent / "static"
for fname in ["index.html", "styles.css", "app.js"]:
    fpath = static_dir / fname
    if fpath.exists():
        print(f"  ✅ static/{fname}")
    else:
        print(f"  ❌ static/{fname} MISSING")
        errors.append(f"Missing: static/{fname}")

# Check directories
for dname in ["uploads", "chroma_db"]:
    dpath = Path(__file__).parent / dname
    if dpath.exists():
        print(f"  ✅ {dname}/ directory exists")
    else:
        print(f"  ⚠️ {dname}/ directory missing (will be created automatically)")

print()

# Try importing our modules
print("=" * 50)
print("MODULE IMPORT CHECK")
print("=" * 50)

try:
    sys.path.insert(0, str(Path(__file__).parent))
    import config
    print(f"  ✅ config.py imported OK")
except Exception as e:
    print(f"  ❌ config.py FAILED: {e}")
    errors.append(f"config.py: {e}")

if not missing:  # Only try these if deps are installed
    try:
        from pdf_processor import PDFProcessor
        print(f"  ✅ pdf_processor.py imported OK")
    except Exception as e:
        print(f"  ❌ pdf_processor.py FAILED: {e}")
        errors.append(f"pdf_processor.py: {e}")

    try:
        from rag_engine import RAGEngine
        print(f"  ✅ rag_engine.py imported OK")
    except Exception as e:
        print(f"  ❌ rag_engine.py FAILED: {e}")
        errors.append(f"rag_engine.py: {e}")

    try:
        import app
        print(f"  ✅ app.py imported OK")
    except Exception as e:
        print(f"  ❌ app.py FAILED: {e}")
        errors.append(f"app.py: {e}")

print()
print("=" * 50)

if errors:
    print("ISSUES FOUND:")
    for i, err in enumerate(errors, 1):
        print(f"  {i}. {err}")
    print()
    print("Fix the issues above, then run:")
    print("  python -m uvicorn app:app --reload")
else:
    print("ALL CHECKS PASSED! ✅")
    print()
    print("Run the server with:")
    print("  python -m uvicorn app:app --reload")
    print()
    print("Then open: http://localhost:8000")

print("=" * 50)
input("\nPress Enter to exit...")

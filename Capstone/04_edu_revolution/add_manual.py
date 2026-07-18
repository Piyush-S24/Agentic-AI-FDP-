from pdf_processor import PDFProcessor
import os
from pathlib import Path

# Seed the knowledge base from the bundled manual (or the first PDF found next to
# this script). Kept relative so the project is self-contained after a move.
BASE_DIR = Path(__file__).resolve().parent
_default = BASE_DIR / "260706155032419_1_EdU Revolution Manual.pdf"
if _default.exists():
    pdf_path = str(_default)
else:
    _pdfs = sorted(BASE_DIR.glob("*.pdf"))
    pdf_path = str(_pdfs[0]) if _pdfs else str(_default)

if __name__ == "__main__":
    if os.path.exists(pdf_path):
        print(f"Found PDF at {pdf_path}. Processing into ChromaDB...")
        try:
            processor = PDFProcessor()
            res = processor.process_pdf(pdf_path)
            print("Successfully processed PDF!")
            print("Result Details:", res)
        except Exception as e:
            print("Error processing PDF:", e)
    else:
        print(f"Error: no PDF found in {BASE_DIR}")
        found = [p.name for p in BASE_DIR.iterdir() if p.is_file()]
        print("Files currently in that folder:", found or "(none)")
        print(
            "\nFix: put the EDU Revolution manual PDF in the project folder above,\n"
            "then run this script FROM that folder, e.g.:\n"
            f'    cd "{BASE_DIR}"\n'
            "    python add_manual.py"
        )

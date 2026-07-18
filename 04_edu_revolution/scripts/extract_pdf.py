import PyPDF2
from pathlib import Path

# The manual PDF lives at the project root (one level up from scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_pdfs = sorted(PROJECT_ROOT.glob("*.pdf"))
pdf_path = str(_pdfs[0]) if _pdfs else str(PROJECT_ROOT / "260706155032419_1_EdU Revolution Manual.pdf")

reader = PyPDF2.PdfReader(pdf_path)
for i, page in enumerate(reader.pages):
    text = page.extract_text()
    print(f'--- PAGE {i+1} ---')
    print(text)
    print()

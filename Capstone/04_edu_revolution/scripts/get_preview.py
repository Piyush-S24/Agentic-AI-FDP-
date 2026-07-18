import PyPDF2
from pathlib import Path

# Resolve paths relative to the project (one level up from scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_pdfs = sorted(PROJECT_ROOT.glob("*.pdf"))
pdf_path = str(_pdfs[0]) if _pdfs else str(PROJECT_ROOT / "260706155032419_1_EdU Revolution Manual.pdf")
output_path = str(Path(__file__).resolve().parent / "manual_preview.txt")

if __name__ == "__main__":
    try:
        reader = PyPDF2.PdfReader(pdf_path)
        print(f"Total Pages: {len(reader.pages)}")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"Total Pages: {len(reader.pages)}\n\n")
            for i in range(min(15, len(reader.pages))):
                f.write(f"--- PAGE {i+1} ---\n")
                f.write(reader.pages[i].extract_text() or "[No text]")
                f.write("\n\n")
        print(f"Preview successfully written to {output_path}!")
    except Exception as e:
        print("Error:", e)

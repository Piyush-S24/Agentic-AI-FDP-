import chromadb
from pathlib import Path

# Resolve paths relative to the project (one level up from scripts/).
# Note: chroma_db is created after you seed the KB (python add_manual.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
db_path = str(PROJECT_ROOT / "chroma_db")
output_path = str(Path(__file__).resolve().parent / "db_preview.txt")

if __name__ == "__main__":
    try:
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_collection("edu_revolution_knowledge")
        print(f"Collection count: {collection.count()}")
        
        # Get first 15 records
        res = collection.get(limit=15)
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"Total Chunks: {collection.count()}\n\n")
            for i in range(len(res['ids'])):
                f.write(f"--- CHUNK {i+1} (ID: {res['ids'][i]}) ---\n")
                f.write(f"Metadata: {res['metadatas'][i]}\n")
                f.write(res['documents'][i])
                f.write("\n\n")
        print(f"ChromaDB preview successfully written to {output_path}!")
    except Exception as e:
        print("Error:", e)

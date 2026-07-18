"""
EdU Revolution — PDF Processor
Handles PDF ingestion: text extraction, chunking, embedding, and ChromaDB storage.
Supports multiple PDFs to build a growing knowledge base.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Optional

import PyPDF2
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
import chromadb

from config import (
    CHROMA_DB_PATH,
    CHROMA_COLLECTION_NAME,
    EMBEDDING_MODEL_NAME,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)

logger = logging.getLogger("edu_revolution.pdf")


class PDFProcessor:
    """
    Processes PDF files into vectorized chunks stored in ChromaDB.
    Supports adding/removing documents dynamically.
    """

    def __init__(self):
        # Initialize embedding model (runs locally)
        self._embedding_model: Optional[SentenceTransformer] = None

        # Initialize text splitter
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        # Initialize ChromaDB persistent client
        # Compatible with chromadb >= 0.4.x
        try:
            self.chroma_client = chromadb.PersistentClient(
                path=str(CHROMA_DB_PATH),
            )
        except TypeError:
            # Fallback for older chromadb versions
            self.chroma_client = chromadb.Client(chromadb.Settings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=str(CHROMA_DB_PATH),
                anonymized_telemetry=False,
            ))

        # Get or create the collection
        self.collection = self.chroma_client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"ChromaDB initialized — collection '{CHROMA_COLLECTION_NAME}' has {self.collection.count()} chunks")

    @property
    def embedding_model(self) -> SentenceTransformer:
        """Lazy-load the embedding model to avoid startup delay."""
        if self._embedding_model is None:
            logger.info(f"Loading embedding model '{EMBEDDING_MODEL_NAME}'... (first time may download ~80MB)")
            self._embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info("Embedding model loaded successfully.")
        return self._embedding_model

    def _compute_file_hash(self, file_path: str) -> str:
        """Compute SHA256 hash of a file for deduplication."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def extract_text_from_pdf(self, file_path: str) -> List[Dict]:
        """
        Extract text from a PDF file, page by page.
        Returns a list of dicts with 'text', 'page_number', and 'source'.
        """
        pages = []
        filename = Path(file_path).name

        try:
            reader = PyPDF2.PdfReader(file_path)
            total_pages = len(reader.pages)
            logger.info(f"Extracting text from '{filename}' ({total_pages} pages)...")

            for i, page in enumerate(reader.pages):
                try:
                    text = page.extract_text()
                    if text and text.strip():
                        pages.append({
                            "text": text.strip(),
                            "page_number": i + 1,
                            "source": filename,
                        })
                except Exception as page_err:
                    logger.warning(f"Skipping page {i+1} of '{filename}': {page_err}")
                    continue

        except Exception as e:
            raise RuntimeError(f"Failed to extract text from {filename}: {str(e)}")

        if not pages:
            raise ValueError(
                f"No readable text found in '{filename}'. "
                "The PDF may be scanned/image-based. Please use a text-based PDF."
            )

        logger.info(f"Extracted text from {len(pages)}/{total_pages if 'total_pages' in dir() else '?'} pages")
        return pages

    def chunk_text(self, pages: List[Dict]) -> List[Dict]:
        """
        Split extracted pages into smaller chunks for embedding.
        Preserves metadata (source, page number) for each chunk.
        """
        chunks = []
        for page_data in pages:
            page_chunks = self.text_splitter.split_text(page_data["text"])
            for chunk_text in page_chunks:
                chunks.append({
                    "text": chunk_text,
                    "page_number": page_data["page_number"],
                    "source": page_data["source"],
                })
        logger.info(f"Split into {len(chunks)} chunks (from {len(pages)} pages)")
        return chunks

    def embed_and_store(self, chunks: List[Dict], doc_id: str) -> int:
        """
        Generate embeddings for text chunks and store them in ChromaDB.
        Returns the number of chunks stored.
        """
        if not chunks:
            return 0

        # Extract texts for batch embedding
        texts = [c["text"] for c in chunks]

        # Generate embeddings locally
        logger.info(f"Generating embeddings for {len(texts)} chunks...")
        embeddings = self.embedding_model.encode(texts, show_progress_bar=False).tolist()

        # Prepare data for ChromaDB
        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "source": c["source"],
                "page_number": c["page_number"],
                "doc_id": doc_id,
                "chunk_index": i,
            }
            for i, c in enumerate(chunks)
        ]

        # Upsert into ChromaDB in batches (ChromaDB has a batch limit)
        batch_size = 100
        for start in range(0, len(ids), batch_size):
            end = min(start + batch_size, len(ids))
            self.collection.upsert(
                ids=ids[start:end],
                embeddings=embeddings[start:end],
                documents=texts[start:end],
                metadatas=metadatas[start:end],
            )

        logger.info(f"Stored {len(chunks)} chunks in ChromaDB")
        return len(chunks)

    def process_pdf(self, file_path: str) -> Dict:
        """
        Full pipeline: extract → chunk → embed → store.
        Returns processing metadata.
        """
        filename = Path(file_path).name
        file_hash = self._compute_file_hash(file_path)
        doc_id = f"doc_{file_hash[:12]}"

        # Check if already processed (deduplication)
        try:
            existing = self.collection.get(
                where={"doc_id": doc_id},
                limit=1,
            )
            if existing and existing["ids"]:
                logger.info(f"'{filename}' already in knowledge base (doc_id={doc_id})")
                return {
                    "doc_id": doc_id,
                    "filename": filename,
                    "status": "already_exists",
                    "message": f"'{filename}' is already in the knowledge base.",
                    "chunks": len(existing["ids"]),
                }
        except Exception:
            pass  # Collection may be empty, continue processing

        # Process the PDF
        pages = self.extract_text_from_pdf(file_path)
        chunks = self.chunk_text(pages)
        num_stored = self.embed_and_store(chunks, doc_id)

        return {
            "doc_id": doc_id,
            "filename": filename,
            "status": "processed",
            "message": f"Successfully processed '{filename}'.",
            "pages": len(pages),
            "chunks": num_stored,
        }

    def query(self, query_text: str, n_results: int = 8) -> List[Dict]:
        """
        Query the vector database with a text string.
        Returns the most relevant chunks with metadata.
        """
        if self.collection.count() == 0:
            return []

        # Generate query embedding
        query_embedding = self.embedding_model.encode([query_text]).tolist()

        # Ensure n_results doesn't exceed collection size
        available = self.collection.count()
        n = min(n_results, available)

        try:
            results = self.collection.query(
                query_embeddings=query_embedding,
                n_results=n,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"ChromaDB query failed: {e}")
            return []

        # Format results
        formatted = []
        if results and results.get("documents") and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                dist = results["distances"][0][i] if results.get("distances") else 0
                formatted.append({
                    "text": doc,
                    "source": meta.get("source", "Unknown"),
                    "page_number": meta.get("page_number", 0),
                    "doc_id": meta.get("doc_id", ""),
                    "relevance_score": round(1 - dist, 4),  # cosine similarity
                })

        return formatted

    def get_all_documents(self) -> List[Dict]:
        """
        List all unique documents in the knowledge base.
        """
        if self.collection.count() == 0:
            return []

        try:
            # Get all metadata
            all_data = self.collection.get(include=["metadatas"])

            # Deduplicate by doc_id
            docs = {}
            for meta in all_data["metadatas"]:
                doc_id = meta.get("doc_id", "unknown")
                if doc_id not in docs:
                    docs[doc_id] = {
                        "doc_id": doc_id,
                        "filename": meta.get("source", "Unknown"),
                        "chunks": 0,
                    }
                docs[doc_id]["chunks"] += 1

            return list(docs.values())
        except Exception as e:
            logger.error(f"Failed to list documents: {e}")
            return []

    def delete_document(self, doc_id: str) -> bool:
        """
        Remove all chunks belonging to a specific document from the knowledge base.
        """
        try:
            # Get IDs of chunks belonging to this document
            existing = self.collection.get(
                where={"doc_id": doc_id},
            )
            if existing and existing["ids"]:
                self.collection.delete(ids=existing["ids"])
                logger.info(f"Deleted {len(existing['ids'])} chunks for doc_id={doc_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")
            return False

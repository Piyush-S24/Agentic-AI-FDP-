"""
EdU Revolution — FastAPI Application
Main application server with API endpoints for PDF management and chat.
"""

import os
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from config import UPLOAD_DIR, STATIC_DIR, ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB
from registration import (
    ApplicationStore,
    ApplicationError,
    INITIATIVES,
    ACADEMIC_BENEFITS,
    YEAR_OPTIONS,
)
from rules import rules_catalog, RULE_VERSION, field_requirements, METRIC_LABELS
from decision import HIERARCHY, HIERARCHY_LABELS
from proofs import ProofStore, ProofError, ALLOWED_PROOF_EXTENSIONS, MAX_PROOF_MB
from nudge import find_opportunities, scan_all

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("edu_revolution")


# =========================================================
# Lazy-loaded components (avoid import-time crashes)
# =========================================================
pdf_processor = None
rag_engine = None


def get_pdf_processor():
    """Lazy-load PDF processor to avoid crash if dependencies missing."""
    global pdf_processor
    if pdf_processor is None:
        from pdf_processor import PDFProcessor
        pdf_processor = PDFProcessor()
    return pdf_processor


def get_rag_engine():
    """Lazy-load RAG engine to avoid crash if API key not set."""
    global rag_engine
    if rag_engine is None:
        from rag_engine import RAGEngine
        rag_engine = RAGEngine(get_pdf_processor())
    return rag_engine


agent = None


def get_agent():
    """Lazy-load the Groq tool-calling agent."""
    global agent
    if agent is None:
        from agent import EDURevAgent
        agent = EDURevAgent(get_pdf_processor(), application_store)
    return agent


# Registration store is cheap and needs no API key / model — safe to load eagerly.
application_store = ApplicationStore()

# EDUREV_AGENT_MODE=false falls back to the RAG reflection loop instead of the agent.
AGENT_MODE = os.getenv("EDUREV_AGENT_MODE", "true").strip().lower() not in ("false", "0", "no")


# =========================================================
# Lifespan (replaces deprecated on_event)
# =========================================================
def _warm_up():
    """Pre-load the embedding model and warm the Groq model so the FIRST real
    question isn't slow (embedding load + Groq cold start). Best-effort, background."""
    try:
        proc = get_pdf_processor()
        _ = proc.embedding_model  # loads all-MiniLM once (else the first search is slow)
        _ = proc.query("edu revolution", n_results=1)
    except Exception as e:
        logger.info(f"Warm-up (embeddings) skipped: {e}")
    from config import GROQ_API_KEY
    if AGENT_MODE and GROQ_API_KEY and GROQ_API_KEY != "your_groq_api_key_here":
        try:
            ag = get_agent()
            ag.client.chat.completions.create(
                model=ag.model, messages=[{"role": "user", "content": "hi"}], max_tokens=1)
            logger.info("Agent warmed up — first reply will be fast.")
        except Exception as e:
            logger.info(f"Warm-up (Groq) skipped: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # Startup
    try:
        proc = get_pdf_processor()
        doc_count = len(proc.get_all_documents())
        chunk_count = proc.collection.count()
        logger.info("=" * 60)
        logger.info("  🎓 EDURev Advisor — LPU EDU Revolution")
        logger.info(f"  📚 Knowledge Base: {doc_count} documents, {chunk_count} chunks")
        logger.info(f"  🌐 Server: http://localhost:8000  ·  Staff: /staff")
        logger.info("=" * 60)
    except Exception as e:
        logger.warning(f"Startup warning: {e}")
        logger.info("Server starting — some features may be unavailable until dependencies are ready.")

    # Warm the model/embeddings in the background so the first question is fast.
    import threading
    threading.Thread(target=_warm_up, daemon=True).start()
    yield
    # Shutdown (cleanup if needed)
    logger.info("EDURev Advisor shutting down...")


# =========================================================
# Application Setup
# =========================================================
app = FastAPI(
    title="EdU Revolution",
    description="AI-Powered Academic Advisor with Self-Correcting RAG",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware (allows frontend to call API from any origin during dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory conversation store (per session)
conversations: Dict[str, List[Dict]] = {}


# =========================================================
# Request/Response Models
# =========================================================
class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    response: str
    metadata: dict


class PrefillRequest(BaseModel):
    session_id: str = "default"


class RegistrationRequest(BaseModel):
    """A filed EDU Revolution nomination. Validated in registration.validate()."""
    student_name: str = ""
    registration_id: str = ""
    email: str = ""
    phone: str = ""
    program: str = ""
    school: str = ""
    year_of_study: str = ""
    cgpa: Optional[float] = None
    attendance_percent: Optional[float] = None
    initiative: str = ""
    academic_benefit: str = ""
    activity_title: str = ""
    activity_description: str = ""
    supporting_documents: str = ""
    declaration: bool = False
    # 2.0 automation inputs (all optional)
    proof_links: str = ""
    proof_files: List[str] = []      # proof ids returned by POST /api/proofs
    achievement_stage: str = ""
    tenant_id: str = ""
    revenue_amount: Optional[float] = None
    stipend_amount: Optional[float] = None
    duration_months: Optional[float] = None

    @field_validator("cgpa", "attendance_percent", "revenue_amount",
                     "stipend_amount", "duration_months", mode="before")
    @classmethod
    def _blank_number_to_none(cls, v):
        # The form submits empty number inputs as "" — treat that as "not provided"
        # instead of letting Pydantic reject it (which produced an unlabelled 422).
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        return v


class ReviewDecisionRequest(BaseModel):
    """A staff decision on a nomination (Layer 6)."""
    actor: str = "reviewer"
    action: str = "approve"          # approve | reject | escalate | request_info
    note: str = ""
    to: Optional[str] = None         # escalation target level (for action=escalate)


class LoginRequest(BaseModel):
    """Identity verification against the Student Directory."""
    registration_id: str = ""
    secret: str = ""                 # second factor (e.g. DOB YYYY-MM-DD) — SSO in production


# =========================================================
# Routes (defined BEFORE static mount so they take priority)
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the main frontend UI."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found. Ensure /static/index.html exists.")
    return FileResponse(str(index_path), media_type="text/html")


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    try:
        proc = get_pdf_processor()
        doc_count = len(proc.get_all_documents())
        chunk_count = proc.collection.count()
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e),
            "documents_loaded": 0,
            "total_chunks": 0,
        }

    from config import GROQ_API_KEY, GROQ_MODEL
    return {
        "status": "healthy",
        "documents_loaded": doc_count,
        "total_chunks": chunk_count,
        "model": GROQ_MODEL,
        "api_key_set": bool(GROQ_API_KEY and GROQ_API_KEY != "your_groq_api_key_here"),
    }


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """
    Upload a PDF to the knowledge base.
    Extracts text, chunks it, generates embeddings, and stores in ChromaDB.
    """
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    # Validate file extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{ext}'. Only PDF files are allowed.",
        )

    # Read and validate file size
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="File is empty.")

    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f}MB). Maximum is {MAX_FILE_SIZE_MB}MB.",
        )

    # Save file to uploads directory
    safe_filename = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    file_path = UPLOAD_DIR / safe_filename

    try:
        with open(file_path, "wb") as f:
            f.write(contents)

        # Process the PDF
        proc = get_pdf_processor()
        result = proc.process_pdf(str(file_path))
        logger.info(f"PDF processed: {result}")
        return result

    except ValueError as e:
        # Clean up on failure
        if file_path.exists():
            os.remove(file_path)
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        # Clean up on failure
        if file_path.exists():
            os.remove(file_path)
        logger.error(f"PDF processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@app.get("/api/documents")
async def list_documents():
    """List all documents in the knowledge base."""
    try:
        proc = get_pdf_processor()
        documents = proc.get_all_documents()
        return {
            "documents": documents,
            "total": len(documents),
            "total_chunks": proc.collection.count(),
        }
    except Exception as e:
        return {"documents": [], "total": 0, "total_chunks": 0, "error": str(e)}


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Remove a document from the knowledge base."""
    proc = get_pdf_processor()
    success = proc.delete_document(doc_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{doc_id}' not found in knowledge base.",
        )
    return {"status": "deleted", "doc_id": doc_id}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Process a student message through the Agentic Reflection Loop.
    Returns a structured academic advisory response.
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # Check API key
    from config import GROQ_API_KEY
    if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_api_key_here":
        return ChatResponse(
            response=(
                "🔑 **API Key Not Configured**\n\n"
                "Please set your Groq API key in the `.env` file:\n\n"
                "```\nGROQ_API_KEY=gsk_your_actual_key_here\n```\n\n"
                "Then restart the server. You can get a free key at [console.groq.com](https://console.groq.com)"
            ),
            metadata={"status": "no_api_key", "iterations": 0},
        )

    # Check if knowledge base has any documents
    proc = get_pdf_processor()
    if proc.collection.count() == 0:
        return ChatResponse(
            response=(
                "📚 **EDU Revolution manual not loaded yet**\n\n"
                "The official EDU Revolution knowledge base is maintained by the University and "
                "hasn't been loaded on this server yet, so I can't answer accurately right now.\n\n"
                "Please try again shortly, or reach the **Edu-Revolution Query & Assistance Zone "
                "(Block 38-205B)** for immediate help.\n\n"
                "*(Administrator note: seed the manual with `python add_manual.py`.)*"
            ),
            metadata={"status": "no_documents", "iterations": 0},
        )

    # Get or create conversation history
    session_id = request.session_id
    if session_id not in conversations:
        conversations[session_id] = []

    # Add user message to history
    conversations[session_id].append({
        "role": "user",
        "content": request.message,
    })

    try:
        # Primary path: the Groq tool-calling AGENT (it plans + calls the engines
        # as tools). Fall back to the RAG reflection loop if EDUREV_AGENT_MODE=false.
        if AGENT_MODE:
            result = get_agent().run(
                message=request.message,
                conversation_history=conversations[session_id][:-1],  # exclude the just-added user msg
            )
        else:
            result = get_rag_engine().process_query(
                student_message=request.message,
                conversation_history=conversations[session_id],
            )

        # Add assistant response to history
        conversations[session_id].append({
            "role": "assistant",
            "content": result["response"],
        })

        # Keep conversation history manageable (last 20 messages)
        if len(conversations[session_id]) > 20:
            conversations[session_id] = conversations[session_id][-20:]

        return ChatResponse(
            response=result["response"],
            metadata=result["metadata"],
        )

    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        # Remove the failed user message from history
        if conversations[session_id] and conversations[session_id][-1]["role"] == "user":
            conversations[session_id].pop()
        return ChatResponse(
            response=f"❌ **Error processing your request:**\n\n{str(e)}\n\nPlease check your API key and try again.",
            metadata={"status": "error", "error": str(e), "iterations": 0},
        )


@app.post("/api/chat/reset")
async def reset_chat(session_id: str = "default"):
    """Reset conversation history for a session."""
    if session_id in conversations:
        del conversations[session_id]
    return {"status": "reset", "session_id": session_id}


# =========================================================
# EDU Revolution registration (file a nomination)
# =========================================================
@app.get("/api/initiatives")
async def list_initiatives():
    """Reference data for the nomination form's dropdowns."""
    return {
        "initiatives": INITIATIVES,
        "academic_benefits": ACADEMIC_BENEFITS,
        "years": YEAR_OPTIONS,
        "proof_upload": {
            "allowed_extensions": sorted(ALLOWED_PROOF_EXTENSIONS),
            "max_mb": MAX_PROOF_MB,
        },
        # "initiative|benefit" -> the metric field that is compulsory for that filing
        "requirements": field_requirements(),
        "metric_labels": METRIC_LABELS,
    }


@app.post("/api/prefill")
async def prefill_application(request: PrefillRequest):
    """
    Pre-fill the nomination form from a session's conversation so the student
    doesn't retype what they already told the advisor. Returns {} if there's
    nothing to extract (or no API key configured).
    """
    history = conversations.get(request.session_id, [])
    if not history:
        return {"prefill": {}}
    try:
        engine = get_rag_engine()
        prefill = engine.extract_prefill(history)
    except Exception as e:
        logger.warning(f"Prefill failed: {e}")
        prefill = {}
    return {"prefill": prefill}


@app.post("/api/register")
async def register_application(request: RegistrationRequest):
    """
    Directly file a student's EDU Revolution nomination: validate, run the
    eligibility check, persist it, and return a reference ID + next steps.
    """
    try:
        record = application_store.create(request.model_dump())
    except ApplicationError as e:
        # Field-level validation errors — surface them for inline display.
        raise HTTPException(status_code=422, detail={"message": "Please fix the highlighted fields.", "errors": e.errors})
    except Exception as e:
        logger.error(f"Registration failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Could not file the nomination: {e}")

    logger.info(f"Nomination filed: {record['reference_id']}")
    return record


@app.get("/api/applications")
async def list_applications():
    """List filed nominations (newest first). Useful for verification/admin."""
    records = application_store.list_all()
    records.reverse()
    return {"applications": records, "total": len(records)}


@app.get("/api/applications/{reference_id}")
async def get_application(reference_id: str):
    """Fetch a single filed nomination by its reference ID."""
    record = application_store.get(reference_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"No nomination found with reference '{reference_id}'.")
    return record


# =========================================================
# EDU Revolution 2.0 — automation, review & analytics
# =========================================================
@app.post("/api/proofs")
async def upload_proofs(files: List[UploadFile] = File(...)):
    """
    Upload the student's proof documents for a nomination (certificate, revenue
    statement, stipend letter, patent PDF …). Each file is validated + hashed, and
    PDF text is extracted so the Verification Agent can read identifiers inside it.
    Returns proof ids to submit with the nomination.
    """
    saved, errors = [], []
    for f in files:
        try:
            content = await f.read()
            rec = application_store.proofs.save(f.filename or "file", content)
            saved.append(ProofStore.public(rec))
        except ProofError as e:
            errors.append(str(e))
        except Exception as e:
            logger.error(f"Proof upload failed for {f.filename}: {e}", exc_info=True)
            errors.append(f"'{f.filename}': could not be stored — {type(e).__name__}: {e}")

    if not saved and errors:
        raise HTTPException(status_code=422, detail={"message": "Upload rejected.", "errors": errors})
    return {"proofs": saved, "errors": errors}


@app.get("/api/proofs/{proof_id}")
async def download_proof(proof_id: str):
    """Download a stored proof document (used by staff during review)."""
    rec = application_store.proofs.get(proof_id)
    path = application_store.proofs.path_for(proof_id)
    if not rec or not path:
        raise HTTPException(status_code=404, detail=f"Proof '{proof_id}' not found.")
    return FileResponse(str(path), filename=rec["filename"])


@app.post("/api/login")
async def login(req: LoginRequest):
    """
    Verify a student's identity against the college directory (registration id +
    second factor). Returns the authoritative record so the form can auto-fill and
    lock CGPA/attendance. In production this is UMS SSO.
    """
    rec = application_store.directory.verify(req.registration_id, req.secret)
    if not rec:
        raise HTTPException(status_code=401, detail="Could not verify — check your registration ID and secret.")
    return {"verified": True, "student": rec, "nudges": find_opportunities(rec)}


@app.get("/api/student/{registration_id}")
async def get_student(registration_id: str):
    """Authoritative academic record for a registration id (no secret fields)."""
    rec = application_store.directory.get(registration_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"No student record for '{registration_id}'.")
    return rec


@app.get("/api/nudges/{registration_id}")
async def student_nudges(registration_id: str):
    """Proactive suggestions: which benefit pathways this student already qualifies for."""
    rec = application_store.directory.get(registration_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"No student record for '{registration_id}'.")
    return find_opportunities(rec)


@app.get("/api/nudges")
async def all_nudges():
    """Directory-wide nudge scan (staff): who to proactively reach out to."""
    return scan_all(application_store.directory.all_records())


@app.get("/api/rules")
async def get_rules():
    """The digitized policy rules (Gap #1) — thresholds & required proofs, surfaced at intake."""
    return {
        "rule_version": RULE_VERSION,
        "rules": rules_catalog(),
        "initiatives": INITIATIVES,
        "academic_benefits": ACADEMIC_BENEFITS,
        "hierarchy": [{"key": k, "label": HIERARCHY_LABELS[k]} for k in HIERARCHY],
    }


@app.get("/api/review/queue")
async def review_queue(status: Optional[str] = None, tenant: Optional[str] = None):
    """Staff review queue (Layer 9): pre-scored, needs-action first, fraud-risk ranked."""
    items = application_store.review_queue(tenant=tenant, status=status)
    return {"queue": items, "total": len(items)}


@app.post("/api/review/{reference_id}/decision")
async def review_decision(reference_id: str, req: ReviewDecisionRequest):
    """Record a human decision on a nomination (Layer 6) + append to the audit log."""
    if req.action not in ("approve", "reject", "escalate", "request_info"):
        raise HTTPException(status_code=400, detail="action must be approve | reject | escalate | request_info")
    rec = application_store.apply_decision(
        reference_id, actor=req.actor, action=req.action, note=req.note, to=req.to
    )
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No nomination found with reference '{reference_id}'.")
    return rec


@app.get("/api/analytics")
async def analytics(tenant: Optional[str] = None):
    """Aggregate analytics for staff (Layer 9)."""
    return application_store.analytics(tenant=tenant)


@app.get("/api/achievements/{registration_id}")
async def achievements(registration_id: str):
    """A student's persistent Achievement Profile (Gap #2) — reused across stages."""
    items = application_store.achievements.list_for(registration_id)
    return {"registration_id": registration_id, "achievements": items, "total": len(items)}


@app.get("/staff", response_class=HTMLResponse)
async def serve_staff():
    """Staff review & analytics dashboard."""
    page = STATIC_DIR / "staff.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Staff dashboard not found.")
    return FileResponse(str(page), media_type="text/html")


# =========================================================
# Static files mount (AFTER routes to avoid intercepting API)
# =========================================================
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# =========================================================
# Run directly: python app.py
# =========================================================
if __name__ == "__main__":
    import uvicorn
    print("\n🎓 Starting EdU Revolution server...")
    print("   Open http://localhost:8000 in your browser\n")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)


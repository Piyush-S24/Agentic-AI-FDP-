"""
main.py
=======
FastAPI entrypoint for the CareerForge evaluation system.

Endpoints
---------
POST /api/v1/ats-review
    Standalone ATS score + resume improvement report for a resume against a
    target role (job description optional).

POST /api/v1/start-evaluation
    Runs Step 1 (gap analysis) + the ATS/resume review + generates Step 2
    questions. Returns the session id, gap analysis, ATS report, and the
    first question. The job description is optional — when omitted the
    candidate is evaluated against typical requirements for the target role.

POST /api/v1/submit-answer
    State-machine endpoint. Behavior depends on the session phase:
      * VERBAL  -> grade the answer, return the next question, OR transition
                   to WRITING and return the writing challenge.
      * WRITING -> score the submission and mark the session COMPLETE.

POST /api/v1/final-pathway
    Runs Step 4 synthesis and returns the Markdown career pathway.

POST /api/v1/upgrade-resume
    Final service: rewrites the candidate's full resume tailored to the
    target job, applying the ATS audit findings. Available any time after
    the session is created; the result is cached on the session.

Run locally:
    uvicorn main:app --reload
"""

from __future__ import annotations

import os
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents import (
    ATSAgent,
    PathwayAgent,
    ProfileAgent,
    ResumeUpgradeAgent,
    TechnicalAgent,
    VerbalAgent,
)
from state import (
    ATSReport,
    EvaluationState,
    GapAnalysis,
    Phase,
    Question,
    WritingChallenge,
    session_store,
)
from utils import (
    CareerForgeError,
    LLMConfigError,
    ResumeParseError,
    clean_resume_text,
    extract_text_from_pdf,
)

# Reject oversized uploads early (10 MB is generous for a resume PDF).
MAX_PDF_BYTES = 10 * 1024 * 1024

app = FastAPI(
    title="CareerForge Evaluation Agent",
    version="1.0.0",
    description=(
        "A multi-agent pipeline that evaluates a candidate against a target "
        "role and produces a personalized, time-bounded career pathway."
    ),
)

# Resolve the static assets directory (always relative to THIS file).
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    """Serve the CareerForge web app."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# Instantiate agents once (they are stateless).
profile_agent = ProfileAgent()
ats_agent = ATSAgent()
verbal_agent = VerbalAgent()
technical_agent = TechnicalAgent()
pathway_agent = PathwayAgent()
resume_upgrade_agent = ResumeUpgradeAgent()


# --------------------------------------------------------------------------- #
# Request / response schemas
# --------------------------------------------------------------------------- #
class ResumeParseResponse(BaseModel):
    filename: str
    char_count: int
    resume_text: str
    preview: str = Field(..., description="First ~400 chars for UI confirmation.")


class StartRequest(BaseModel):
    resume_text: str = Field(
        ..., min_length=1,
        description="Plain-text resume (from PDF extraction or typed/pasted).",
    )
    job_description: str = Field(
        default="",
        description=(
            "Optional. When empty, the candidate is evaluated against typical "
            "industry requirements for the target role."
        ),
    )
    target_role: str = Field(..., min_length=1, examples=["Machine Learning Engineer"])


class ATSReviewRequest(BaseModel):
    resume_text: str = Field(..., min_length=1, description="Plain-text resume.")
    job_description: str = Field(default="", description="Optional job description.")
    target_role: str = Field(..., min_length=1)


class QuestionOut(BaseModel):
    id: int
    text: str
    targets_skill: str

    @classmethod
    def of(cls, q: Question) -> "QuestionOut":
        return cls(id=q.id, text=q.text, targets_skill=q.targets_skill)


class StartResponse(BaseModel):
    session_id: str
    phase: Phase
    gap_analysis: GapAnalysis
    ats_report: Optional[ATSReport]
    first_question: Optional[QuestionOut]


class SubmitRequest(BaseModel):
    session_id: str
    answer: str = Field(..., description="Verbal answer or writing submission.")


class SubmitResponse(BaseModel):
    session_id: str
    phase: Phase
    # Verbal-round feedback (present while answering questions)
    last_answer_score: Optional[int] = None
    last_answer_feedback: Optional[str] = None
    next_question: Optional[QuestionOut] = None
    # Writing-round payloads
    writing_challenge: Optional[WritingChallenge] = None
    writing_score: Optional[int] = None
    writing_feedback: Optional[str] = None
    message: str


class PathwayRequest(BaseModel):
    session_id: str


class UpgradeResumeRequest(BaseModel):
    session_id: str


class UpgradeResumeResponse(BaseModel):
    session_id: str
    resume_markdown: str = Field(
        ..., description="The full rewritten resume, tailored to the target job."
    )


class PathwayResponse(BaseModel):
    session_id: str
    ats_score: Optional[int]
    verbal_average: float
    writing_score: Optional[int]
    pathway_markdown: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_session(session_id: str) -> EvaluationState:
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No evaluation session with id '{session_id}'.",
        )
    return state


# --------------------------------------------------------------------------- #
# Exception handling: translate domain errors to HTTP
# --------------------------------------------------------------------------- #
@app.exception_handler(LLMConfigError)
async def _llm_config_handler(_request, exc: LLMConfigError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ResumeParseError)
async def _resume_parse_handler(_request, exc: ResumeParseError):
    from fastapi.responses import JSONResponse

    # A bad/unreadable upload is a client error, not a server fault.
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(CareerForgeError)
async def _careerforge_handler(_request, exc: CareerForgeError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=502, content={"detail": str(exc)})


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/parse-resume", response_model=ResumeParseResponse, tags=["evaluation"])
async def parse_resume(file: UploadFile = File(...)) -> ResumeParseResponse:
    """Accept a PDF upload and return the extracted plain text.

    Used by the frontend for BOTH the resume and the job-description PDF —
    it is a generic "PDF to text" step (the ``resume_text`` field simply
    carries the extracted text). The caller then passes the text to
    ``/start-evaluation``. Keeping extraction separate keeps the evaluation
    endpoint a clean JSON contract.
    """
    filename = file.filename or "resume.pdf"
    is_pdf = filename.lower().endswith(".pdf") or (file.content_type == "application/pdf")
    if not is_pdf:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF resumes are accepted. Please upload a .pdf file.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Resume PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB limit.",
        )

    text = extract_text_from_pdf(data)  # raises ResumeParseError -> HTTP 400
    return ResumeParseResponse(
        filename=filename,
        char_count=len(text),
        resume_text=text,
        preview=text[:400] + ("…" if len(text) > 400 else ""),
    )


@app.post("/api/v1/ats-review", response_model=ATSReport, tags=["evaluation"])
def ats_review(req: ATSReviewRequest) -> ATSReport:
    """Standalone ATS score + resume improvement report (no session created).

    Useful when the candidate only wants their resume checked/improved for a
    specific job without going through the full evaluation pipeline.
    """
    state = EvaluationState(
        resume_text=clean_resume_text(req.resume_text),
        job_description=req.job_description,
        target_role=req.target_role,
    )
    return ats_agent.review(state)


@app.post("/api/v1/start-evaluation", response_model=StartResponse, tags=["evaluation"])
def start_evaluation(req: StartRequest) -> StartResponse:
    """Step 1 (gap analysis) + ATS/resume review + Step 2 bootstrap."""
    resume = clean_resume_text(req.resume_text)

    state = EvaluationState(
        resume_text=resume,
        job_description=req.job_description,
        target_role=req.target_role,
    )

    # Step 1: gap analysis
    state.gap_analysis = profile_agent.analyze(state)

    # Step 1b: ATS score + resume improvement suggestions for this job.
    # Non-fatal: the evaluation can proceed even if this call fails.
    try:
        state.ats_report = ats_agent.review(state)
    except CareerForgeError:
        state.ats_report = None

    # Step 2: pre-generate the question set from the gaps
    state.questions = verbal_agent.generate_questions(state.gap_analysis)
    state.current_question_index = 0
    state.phase = Phase.VERBAL

    session_store.create(state)

    first = state.current_question()
    return StartResponse(
        session_id=state.session_id,
        phase=state.phase,
        gap_analysis=state.gap_analysis,
        ats_report=state.ats_report,
        first_question=QuestionOut.of(first) if first else None,
    )


@app.post("/api/v1/submit-answer", response_model=SubmitResponse, tags=["evaluation"])
def submit_answer(req: SubmitRequest) -> SubmitResponse:
    """Advance the verbal state machine, or score the writing submission."""
    state = _get_session(req.session_id)

    if state.phase == Phase.VERBAL:
        return _handle_verbal(state, req.answer)
    if state.phase == Phase.WRITING:
        return _handle_writing(state, req.answer)

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Session is in phase '{state.phase.value}'. "
            "No further answers are accepted; call /api/v1/final-pathway."
        ),
    )


def _handle_verbal(state: EvaluationState, answer: str) -> SubmitResponse:
    question = state.current_question()
    grade = None

    if question is not None:
        # Grade the current answer and PERSIST progress immediately — before
        # the risky challenge-generation LLM call below. This guarantees the
        # grade and index advance survive even if challenge generation fails.
        grade = verbal_agent.grade_answer(question, answer)
        state.knowledge.grades.append(grade)
        state.current_question_index += 1
        session_store.save(state)

        next_q = state.current_question()
        if next_q is not None:
            return SubmitResponse(
                session_id=state.session_id,
                phase=state.phase,
                last_answer_score=grade.score,
                last_answer_feedback=grade.feedback,
                next_question=QuestionOut.of(next_q),
                message="Answer graded. Here is your next question.",
            )
    # If question is None we reached here because a PRIOR call graded the last
    # answer but its challenge generation failed. We simply retry it below —
    # no double-grading, no 409 dead-end. This makes the transition idempotent.

    # Verbal round finished -> transition to writing challenge (Step 3).
    challenge = technical_agent.create_challenge(state)
    state.writing_challenge = challenge
    state.phase = Phase.WRITING
    session_store.save(state)

    last = grade or (state.knowledge.grades[-1] if state.knowledge.grades else None)
    return SubmitResponse(
        session_id=state.session_id,
        phase=state.phase,
        last_answer_score=last.score if last else None,
        last_answer_feedback=last.feedback if last else None,
        writing_challenge=challenge,
        message=(
            "Knowledge round complete. Submit your response to the writing "
            "challenge via this same endpoint."
        ),
    )


def _handle_writing(state: EvaluationState, submission: str) -> SubmitResponse:
    if state.writing_challenge is None:  # defensive
        raise HTTPException(status_code=409, detail="No writing challenge issued.")

    state.writing_submission = submission
    evaluation = technical_agent.evaluate(state.writing_challenge, submission)
    state.writing_evaluation = evaluation
    state.phase = Phase.COMPLETE
    session_store.save(state)

    return SubmitResponse(
        session_id=state.session_id,
        phase=state.phase,
        writing_score=evaluation.score,
        writing_feedback=evaluation.feedback,
        message=(
            "Evaluation complete. Call /api/v1/final-pathway to generate your "
            "personalized career pathway."
        ),
    )


@app.post("/api/v1/final-pathway", response_model=PathwayResponse, tags=["evaluation"])
def final_pathway(req: PathwayRequest) -> PathwayResponse:
    """Step 4 synthesis: compile everything into a Markdown pathway."""
    state = _get_session(req.session_id)

    if state.phase != Phase.COMPLETE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Evaluation not finished (phase='{state.phase.value}'). "
                "Complete the verbal and writing rounds first."
            ),
        )

    # Cache the generated pathway so repeat calls are free.
    if state.pathway_markdown is None:
        state.pathway_markdown = pathway_agent.synthesize(state)
        session_store.save(state)

    return PathwayResponse(
        session_id=state.session_id,
        ats_score=state.ats_report.ats_score if state.ats_report else None,
        verbal_average=state.knowledge.average_score,
        writing_score=state.writing_evaluation.score if state.writing_evaluation else None,
        pathway_markdown=state.pathway_markdown,
    )


@app.post("/api/v1/upgrade-resume", response_model=UpgradeResumeResponse, tags=["evaluation"])
def upgrade_resume(req: UpgradeResumeRequest) -> UpgradeResumeResponse:
    """Final service: rewrite the full resume tailored to the target job.

    Works at any point after the session is created (it draws on the gap
    analysis and ATS audit when available). The result is cached so repeat
    calls are free.
    """
    state = _get_session(req.session_id)

    if state.upgraded_resume_markdown is None:
        state.upgraded_resume_markdown = resume_upgrade_agent.upgrade(state)
        session_store.save(state)

    return UpgradeResumeResponse(
        session_id=state.session_id,
        resume_markdown=state.upgraded_resume_markdown,
    )


# Mount static files AFTER all routes so the mount never shadows them.
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

"""
state.py
========
Pydantic schemas and in-memory state management for the CareerForge
multi-agent evaluation pipeline.

The application is intentionally stateful across HTTP requests: a single
candidate evaluation spans several calls (start -> submit answers ->
writing test -> final pathway). We model that lifecycle explicitly with an
``EvaluationState`` object stored in a process-local ``SessionStore``.

For a production deployment this store would be backed by Redis / Postgres;
the interface is deliberately narrow so it can be swapped without touching
the agent code.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Phase(str, Enum):
    """The linear stages a candidate moves through."""

    CREATED = "created"          # Session created, gap analysis done
    VERBAL = "verbal"            # Step 2: answering knowledge questions
    WRITING = "writing"          # Step 3: writing / scenario challenge
    COMPLETE = "complete"        # Step 4 ready: pathway can be generated


class SkillCategory(str, Enum):
    TECHNICAL = "technical"
    SOFT = "soft"
    EXPERIENCE = "experience"
    TOOLING = "tooling"


# --------------------------------------------------------------------------- #
# Step 1 — Gap Analysis schemas
# --------------------------------------------------------------------------- #
class SkillGap(BaseModel):
    """A single identified deficiency between the candidate and target role."""

    skill: str = Field(..., description="Name of the missing skill or experience.")
    category: SkillCategory = Field(..., description="Type of gap.")
    severity: int = Field(
        ..., ge=1, le=5,
        description="1 = minor / nice-to-have, 5 = critical blocker.",
    )
    rationale: str = Field(..., description="Why this is a gap for the target role.")


class GapAnalysis(BaseModel):
    """Structured output of the Profile / Gap Analysis agent (Step 1)."""

    target_role: str
    summary: str = Field(..., description="One-paragraph readiness summary.")
    matched_strengths: List[str] = Field(default_factory=list)
    gaps: List[SkillGap] = Field(default_factory=list)

    @property
    def critical_gaps(self) -> List[SkillGap]:
        return [g for g in self.gaps if g.severity >= 4]


# --------------------------------------------------------------------------- #
# ATS / Resume improvement schemas
# --------------------------------------------------------------------------- #
class ResumeImprovement(BaseModel):
    """One concrete before/after rewrite suggestion for the resume."""

    section: str = Field(..., description="Which part of the resume this targets.")
    original: str = Field(..., description="The current text (or a close paraphrase).")
    improved: str = Field(..., description="A stronger, job-tailored rewrite.")
    why: str = Field(..., description="Why the rewrite scores better with ATS/recruiters.")


class ATSSubscores(BaseModel):
    """Component scores that roll up into the overall ATS score."""

    keyword_match: int = Field(..., ge=0, le=100, description="Coverage of job keywords.")
    formatting: int = Field(..., ge=0, le=100, description="ATS-parseable structure.")
    impact: int = Field(..., ge=0, le=100, description="Quantified, action-led bullets.")
    relevance: int = Field(..., ge=0, le=100, description="Tailoring to the target job.")


class ATSReport(BaseModel):
    """Structured output of the ATS / Resume improvement agent."""

    ats_score: int = Field(..., ge=0, le=100, description="Overall ATS pass likelihood.")
    subscores: ATSSubscores
    matched_keywords: List[str] = Field(default_factory=list)
    missing_keywords: List[str] = Field(
        default_factory=list,
        description="Keywords from the job the resume should include (truthfully).",
    )
    formatting_issues: List[str] = Field(default_factory=list)
    improvements: List[ResumeImprovement] = Field(default_factory=list)
    improved_summary: str = Field(
        "", description="A rewritten professional summary tailored to the job."
    )
    quick_wins: List[str] = Field(
        default_factory=list,
        description="Fast, high-leverage edits the candidate can make today.",
    )
    verdict: str = Field(..., description="One-paragraph honest assessment.")


# --------------------------------------------------------------------------- #
# Step 2 — Verbal / Knowledge assessment schemas
# --------------------------------------------------------------------------- #
class Question(BaseModel):
    """A dynamically generated knowledge question tied to a gap."""

    id: int
    text: str
    targets_skill: str = Field(..., description="Which gap this probes.")
    ideal_answer_points: List[str] = Field(
        default_factory=list,
        description="Key concepts a strong answer should mention.",
    )


class AnswerGrade(BaseModel):
    """Grade for a single answer, produced by the Verbal agent."""

    question_id: int
    score: int = Field(..., ge=0, le=100, description="Conceptual accuracy 0-100.")
    covered_points: List[str] = Field(default_factory=list)
    missing_points: List[str] = Field(default_factory=list)
    feedback: str


class KnowledgeAssessment(BaseModel):
    """Aggregated result of the verbal round."""

    grades: List[AnswerGrade] = Field(default_factory=list)

    @property
    def average_score(self) -> float:
        if not self.grades:
            return 0.0
        return round(sum(g.score for g in self.grades) / len(self.grades), 1)


# --------------------------------------------------------------------------- #
# Step 3 — Technical writing / scenario schemas
# --------------------------------------------------------------------------- #
class WritingChallenge(BaseModel):
    prompt: str
    evaluation_criteria: List[str] = Field(default_factory=list)


class WritingEvaluation(BaseModel):
    """Score for the writing / scenario submission (Step 3)."""

    score: int = Field(..., ge=0, le=100)
    syntax: int = Field(..., ge=0, le=100)
    logic: int = Field(..., ge=0, le=100)
    depth: int = Field(..., ge=0, le=100)
    communication: int = Field(..., ge=0, le=100)
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    feedback: str


# --------------------------------------------------------------------------- #
# Aggregate session state
# --------------------------------------------------------------------------- #
class EvaluationState(BaseModel):
    """Everything we know about one candidate evaluation."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    phase: Phase = Phase.CREATED

    # Raw inputs (job_description is optional: when empty, agents evaluate
    # against typical industry requirements for the target role instead).
    resume_text: str
    job_description: str = ""
    target_role: str

    # Step 1
    gap_analysis: Optional[GapAnalysis] = None

    # ATS / resume improvement review (runs alongside Step 1)
    ats_report: Optional[ATSReport] = None

    # Step 2
    questions: List[Question] = Field(default_factory=list)
    current_question_index: int = 0
    knowledge: KnowledgeAssessment = Field(default_factory=KnowledgeAssessment)

    # Step 3
    writing_challenge: Optional[WritingChallenge] = None
    writing_submission: Optional[str] = None
    writing_evaluation: Optional[WritingEvaluation] = None

    # Step 4
    pathway_markdown: Optional[str] = None

    # Optional final service: full resume rewrite tailored to the target job
    upgraded_resume_markdown: Optional[str] = None

    # ----- Convenience helpers driving the state machine ------------------ #
    def current_question(self) -> Optional[Question]:
        if self.current_question_index < len(self.questions):
            return self.questions[self.current_question_index]
        return None

    def verbal_complete(self) -> bool:
        return len(self.knowledge.grades) >= len(self.questions) and bool(self.questions)


# --------------------------------------------------------------------------- #
# Thread-safe in-memory session store
# --------------------------------------------------------------------------- #
class SessionStore:
    """A minimal, thread-safe session registry.

    Swap this for a Redis/Postgres-backed implementation in production; the
    method surface (``create``, ``get``, ``save``) is all the app relies on.
    """

    def __init__(self) -> None:
        self._store: Dict[str, EvaluationState] = {}
        self._lock = threading.Lock()

    def create(self, state: EvaluationState) -> EvaluationState:
        with self._lock:
            self._store[state.session_id] = state
        return state

    def get(self, session_id: str) -> Optional[EvaluationState]:
        with self._lock:
            return self._store.get(session_id)

    def save(self, state: EvaluationState) -> EvaluationState:
        with self._lock:
            self._store[state.session_id] = state
        return state

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)


# A module-level singleton used by the FastAPI app.
session_store = SessionStore()

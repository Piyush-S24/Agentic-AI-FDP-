"""
agents.py
=========
The four cooperating agents that make up the CareerForge pipeline.

Each agent is a small, single-responsibility class. They share nothing except
the ``EvaluationState`` passed between them and the LLM helpers in ``utils``.
This keeps the pipeline easy to test (mock the LLM) and easy to re-order.

Pipeline
--------
    ProfileAgent       (Step 1) -> gap analysis           [MODEL_DEEP]
    ATSAgent           (Step 1b)-> ATS score + resume fix  [MODEL_DEEP]
    VerbalAgent        (Step 2) -> questions + grading     [MODEL_FAST]
    TechnicalAgent     (Step 3) -> writing challenge/score [MODEL_FAST]
    PathwayAgent       (Step 4) -> markdown pathway        [MODEL_DEEP]
    ResumeUpgradeAgent (final)  -> full tailored rewrite   [MODEL_DEEP]
"""

from __future__ import annotations

from typing import List

from state import (
    AnswerGrade,
    ATSReport,
    EvaluationState,
    GapAnalysis,
    KnowledgeAssessment,
    Question,
    WritingChallenge,
    WritingEvaluation,
)
from utils import (
    MODEL_DEEP,
    MODEL_FAST,
    CareerForgeError,
    chat_completion,
    structured_completion,
    truncate_for_context,
)


# --------------------------------------------------------------------------- #
# Step 1 — Profile / Gap Analysis Agent
# --------------------------------------------------------------------------- #
class ProfileAgent:
    """Compares a resume against a target job description and surfaces gaps."""

    SYSTEM = (
        "You are a senior technical recruiter and career coach. You perform "
        "rigorous, honest gap analysis between a candidate's resume and a "
        "target job description. Identify concrete missing technical skills, "
        "tooling, soft skills, and experience. Be specific and avoid flattery."
    )

    def analyze(self, state: EvaluationState) -> GapAnalysis:
        jd_block = (
            f"=== JOB DESCRIPTION ===\n{truncate_for_context(state.job_description)}"
            if state.job_description.strip()
            else (
                "=== JOB DESCRIPTION ===\n(None provided. Evaluate the candidate "
                f"against the typical, current industry requirements for a "
                f"'{state.target_role}' position: core technical skills, tooling, "
                "and experience employers commonly demand for this role.)"
            )
        )
        user_prompt = (
            f"TARGET ROLE: {state.target_role}\n\n"
            f"{jd_block}\n\n"
            f"=== CANDIDATE RESUME ===\n{truncate_for_context(state.resume_text)}\n\n"
            "Produce a semantic gap analysis. List matched strengths and every "
            "meaningful gap with a severity from 1 (minor) to 5 (critical blocker)."
        )
        analysis = structured_completion(
            self.SYSTEM,
            user_prompt,
            GapAnalysis,
            model=MODEL_DEEP,
            temperature=0.2,
        )
        # Ensure the target role is always populated even if the model omits it.
        if not analysis.target_role:
            analysis.target_role = state.target_role
        return analysis


# --------------------------------------------------------------------------- #
# Step 1b — ATS & Resume Improvement Agent
# --------------------------------------------------------------------------- #
class ATSAgent:
    """Scores the resume like an ATS would and rewrites weak sections for the job."""

    SYSTEM = (
        "You are an expert in Applicant Tracking Systems (ATS) and a professional "
        "resume writer. You audit resumes the way modern ATS parsers and "
        "recruiters do: exact keyword/skill coverage, parseable structure, "
        "quantified impact statements, and tailoring to the specific job. "
        "Your rewrites must be truthful — rephrase and strengthen what the "
        "candidate actually did; NEVER invent experience, employers, metrics, or "
        "skills the resume does not support. Be specific and practical."
    )

    def review(self, state: EvaluationState) -> ATSReport:
        jd_block = (
            f"=== JOB DESCRIPTION ===\n{truncate_for_context(state.job_description)}"
            if state.job_description.strip()
            else (
                "=== JOB DESCRIPTION ===\n(None provided. Score against the "
                f"keywords and requirements a typical '{state.target_role}' "
                "job posting would contain.)"
            )
        )
        user_prompt = (
            f"TARGET ROLE: {state.target_role}\n\n"
            f"{jd_block}\n\n"
            f"=== CANDIDATE RESUME ===\n{truncate_for_context(state.resume_text)}\n\n"
            "Audit this resume for THIS job. Provide:\n"
            "1. An overall ATS score (0-100) plus subscores for keyword_match, "
            "formatting, impact, and relevance.\n"
            "2. matched_keywords: job keywords the resume already covers.\n"
            "3. missing_keywords: important job keywords absent from the resume "
            "(only ones the candidate could truthfully add or should acquire).\n"
            "4. formatting_issues: structure problems an ATS parser would choke "
            "on (missing sections, dense paragraphs, no dates, etc.).\n"
            "5. improvements: 3-6 before/after rewrites of the weakest lines, "
            "tailored to this job, each with a short 'why'.\n"
            "6. improved_summary: a 2-3 sentence professional summary rewritten "
            "for this job using the candidate's real background.\n"
            "7. quick_wins: fast edits with the biggest score impact.\n"
            "8. verdict: one honest paragraph on how this resume will fare."
        )
        return structured_completion(
            self.SYSTEM,
            user_prompt,
            ATSReport,
            model=MODEL_DEEP,
            temperature=0.3,
            max_tokens=3000,
        )


# --------------------------------------------------------------------------- #
# Step 2 — Verbal / Adaptive Knowledge Assessment Agent
# --------------------------------------------------------------------------- #
class VerbalAgent:
    """Generates gap-driven questions and grades free-text answers."""

    GEN_SYSTEM = (
        "You are an interviewer designing a short oral knowledge check. Given a "
        "candidate's skill gaps, write focused conceptual questions that reveal "
        "whether they truly understand the missing areas. Questions must be "
        "answerable verbally in 1-3 minutes."
    )

    GRADE_SYSTEM = (
        "You are a strict but fair examiner grading a spoken answer for "
        "conceptual accuracy. Reward correct reasoning, penalize vagueness and "
        "factual errors. Score 0-100."
    )

    NUM_QUESTIONS = 3

    def generate_questions(self, analysis: GapAnalysis) -> List[Question]:
        gaps_text = "\n".join(
            f"- [{g.category.value} | severity {g.severity}] {g.skill}: {g.rationale}"
            for g in (analysis.critical_gaps or analysis.gaps)
        ) or "- General fundamentals for the target role."

        user_prompt = (
            f"TARGET ROLE: {analysis.target_role}\n\n"
            f"IDENTIFIED GAPS:\n{gaps_text}\n\n"
            f"Generate exactly {self.NUM_QUESTIONS} questions, prioritizing the "
            "most severe gaps. For each, list the key points an ideal answer "
            "would mention."
        )

        # A tiny wrapper schema so the model returns a list under a known key.
        from pydantic import BaseModel  # local import to keep module surface clean

        class _QList(BaseModel):
            questions: List[Question]

        result = structured_completion(
            self.GEN_SYSTEM,
            user_prompt,
            _QList,
            model=MODEL_FAST,
            temperature=0.5,
        )
        questions = result.questions[: self.NUM_QUESTIONS]
        # Normalize ids to 1..N regardless of what the model chose.
        for i, q in enumerate(questions, start=1):
            q.id = i
        if not questions:
            raise CareerForgeError("Question generation produced no questions.")
        return questions

    def grade_answer(self, question: Question, answer: str) -> AnswerGrade:
        ideal = "\n".join(f"- {p}" for p in question.ideal_answer_points) or "- (none)"
        user_prompt = (
            f"QUESTION: {question.text}\n"
            f"SKILL PROBED: {question.targets_skill}\n"
            f"KEY POINTS A STRONG ANSWER COVERS:\n{ideal}\n\n"
            f"CANDIDATE ANSWER:\n{answer.strip() or '(no answer provided)'}\n\n"
            "Grade the answer's conceptual accuracy."
        )
        grade = structured_completion(
            self.GRADE_SYSTEM,
            user_prompt,
            AnswerGrade,
            model=MODEL_FAST,
            temperature=0.1,
        )
        grade.question_id = question.id  # trust our own id, not the model's
        return grade


# --------------------------------------------------------------------------- #
# Step 3 — Technical Writing / Scenario Test Agent
# --------------------------------------------------------------------------- #
class TechnicalAgent:
    """Issues a role-appropriate writing/scenario prompt and scores the answer."""

    CHALLENGE_SYSTEM = (
        "You design a single practical challenge tailored to a role. For "
        "engineering roles use a system-design or debugging scenario; for "
        "writing/marketing roles use a copy brief; for data roles use an "
        "analysis scenario. The challenge must be answerable in ~300-500 words."
    )

    SCORE_SYSTEM = (
        "You are an expert evaluator scoring a written submission from 0-100 on "
        "four axes: syntax (clarity/correctness of form), logic (soundness of "
        "reasoning), depth (thoroughness), and communication (how well it lands "
        "with the intended reader). The overall score reflects practical hire-"
        "readiness for the target role."
    )

    def create_challenge(self, state: EvaluationState) -> WritingChallenge:
        gaps = state.gap_analysis
        focus = ", ".join(g.skill for g in (gaps.gaps if gaps else [])[:5]) or "core role skills"
        user_prompt = (
            f"TARGET ROLE: {state.target_role}\n"
            f"AREAS TO STRESS-TEST: {focus}\n\n"
            "Write one challenge prompt plus 3-5 evaluation criteria."
        )
        return structured_completion(
            self.CHALLENGE_SYSTEM,
            user_prompt,
            WritingChallenge,
            model=MODEL_FAST,
            temperature=0.5,
        )

    def evaluate(self, challenge: WritingChallenge, submission: str) -> WritingEvaluation:
        criteria = "\n".join(f"- {c}" for c in challenge.evaluation_criteria) or "- General quality"
        user_prompt = (
            f"CHALLENGE:\n{challenge.prompt}\n\n"
            f"EVALUATION CRITERIA:\n{criteria}\n\n"
            f"CANDIDATE SUBMISSION:\n{submission.strip() or '(empty submission)'}\n\n"
            "Score each axis and the overall submission."
        )
        return structured_completion(
            self.SCORE_SYSTEM,
            user_prompt,
            WritingEvaluation,
            model=MODEL_FAST,
            temperature=0.1,
        )


# --------------------------------------------------------------------------- #
# Step 4 — Pathway Generator Agent (Synthesis)
# --------------------------------------------------------------------------- #
class PathwayAgent:
    """Synthesizes all prior signals into a time-bounded Markdown pathway."""

    SYSTEM = (
        "You are a career development strategist. You compile evaluation data "
        "into an actionable, encouraging but honest learning pathway. Output "
        "clean GitHub-flavored Markdown only. Be concrete: name resources, set "
        "weekly milestones, and map each recommendation to a measured gap."
    )

    def synthesize(self, state: EvaluationState) -> str:
        analysis: GapAnalysis = state.gap_analysis  # guaranteed by caller
        knowledge: KnowledgeAssessment = state.knowledge
        writing: WritingEvaluation = state.writing_evaluation

        gap_lines = "\n".join(
            f"- ({g.severity}/5, {g.category.value}) {g.skill} — {g.rationale}"
            for g in analysis.gaps
        )
        grade_lines = "\n".join(
            f"- Q{g.question_id}: {g.score}/100 — missing: "
            f"{', '.join(g.missing_points) or 'none'}"
            for g in knowledge.grades
        )
        writing_block = (
            f"Overall {writing.score}/100 "
            f"(syntax {writing.syntax}, logic {writing.logic}, "
            f"depth {writing.depth}, communication {writing.communication}). "
            f"Weaknesses: {', '.join(writing.weaknesses) or 'none'}."
            if writing
            else "No writing submission scored."
        )

        ats = state.ats_report
        ats_block = (
            f"ATS score {ats.ats_score}/100 "
            f"(keywords {ats.subscores.keyword_match}, formatting "
            f"{ats.subscores.formatting}, impact {ats.subscores.impact}, "
            f"relevance {ats.subscores.relevance}). "
            f"Missing keywords: {', '.join(ats.missing_keywords[:10]) or 'none'}. "
            f"Quick wins: {'; '.join(ats.quick_wins[:5]) or 'none'}."
            if ats
            else "No ATS review performed."
        )

        user_prompt = (
            f"TARGET ROLE: {state.target_role}\n\n"
            f"STEP 1 — GAP ANALYSIS\nSummary: {analysis.summary}\n"
            f"Strengths: {', '.join(analysis.matched_strengths) or 'none noted'}\n"
            f"Gaps:\n{gap_lines}\n\n"
            f"STEP 1b — ATS / RESUME REVIEW\n{ats_block}\n\n"
            f"STEP 2 — KNOWLEDGE ROUND (avg {knowledge.average_score}/100)\n"
            f"{grade_lines or 'No answers graded.'}\n\n"
            f"STEP 3 — WRITING/SCENARIO\n{writing_block}\n\n"
            "Produce a personalized career development pathway in Markdown with "
            "these sections, in order:\n"
            "1. `## Scorecard` — a table with each step's score (include the "
            "ATS score if available).\n"
            "2. `## Key Gaps to Close` — prioritized bullet list mapped to gaps.\n"
            "3. `## Resume & ATS Action Items` — concrete resume edits driven "
            "by the ATS review (missing keywords to work in truthfully, "
            "formatting fixes, quick wins).\n"
            "4. `## Learning Objectives` — concrete objectives with resources.\n"
            "5. `## Week-by-Week Plan` — a checklist (`- [ ]`) spanning enough "
            "weeks to reach job readiness (4-8 weeks), each week with a theme "
            "and 2-4 tasks.\n"
            "6. `## Readiness Verdict` — an honest one-paragraph verdict."
        )
        messages = [
            {"role": "system", "content": self.SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        return chat_completion(
            messages,
            model=MODEL_DEEP,
            temperature=0.4,
            json_mode=False,
            max_tokens=3000,
        )


# --------------------------------------------------------------------------- #
# Final service — Resume Upgrade Agent
# --------------------------------------------------------------------------- #
class ResumeUpgradeAgent:
    """Rewrites the candidate's entire resume, tailored to the target job.

    Uses the ATS audit findings (missing keywords, formatting issues, quick
    wins) as concrete instructions so the rewrite directly fixes what was
    measured earlier in the pipeline.
    """

    SYSTEM = (
        "You are an elite professional resume writer and ATS optimization "
        "specialist. You rewrite complete resumes tailored to a specific job. "
        "STRICT RULES: stay 100% truthful to the candidate's real background — "
        "rephrase, reorganize, and strengthen, but NEVER fabricate employers, "
        "job titles, dates, degrees, metrics, or skills the source resume does "
        "not support. Where a quantified metric would help but is unknown, "
        "insert a bracketed placeholder like [X%] or [team size] for the "
        "candidate to fill in. Output clean GitHub-flavored Markdown ONLY — "
        "no commentary before or after the resume."
    )

    def upgrade(self, state: EvaluationState) -> str:
        jd_block = (
            f"=== TARGET JOB DESCRIPTION ===\n{truncate_for_context(state.job_description)}"
            if state.job_description.strip()
            else (
                "=== TARGET JOB DESCRIPTION ===\n(None provided. Tailor the "
                f"resume to a typical '{state.target_role}' job posting.)"
            )
        )

        hints: List[str] = []
        ats = state.ats_report
        if ats:
            if ats.missing_keywords:
                hints.append(
                    "Weave in these missing keywords, but ONLY where the "
                    "candidate's real experience genuinely supports them: "
                    + ", ".join(ats.missing_keywords[:12])
                )
            if ats.formatting_issues:
                hints.append("Fix these structure issues: " + "; ".join(ats.formatting_issues[:6]))
            if ats.quick_wins:
                hints.append("Apply these quick wins: " + "; ".join(ats.quick_wins[:6]))
            if ats.improved_summary:
                hints.append(
                    "Base the professional summary on this draft: " + ats.improved_summary
                )
        if state.gap_analysis and state.gap_analysis.matched_strengths:
            hints.append(
                "Lead with these verified strengths: "
                + ", ".join(state.gap_analysis.matched_strengths[:8])
            )
        hints_block = "\n".join(f"- {h}" for h in hints) or "- (no audit findings available)"

        user_prompt = (
            f"TARGET ROLE: {state.target_role}\n\n"
            f"{jd_block}\n\n"
            f"=== CURRENT RESUME ===\n{truncate_for_context(state.resume_text)}\n\n"
            f"=== AUDIT FINDINGS TO APPLY ===\n{hints_block}\n\n"
            "Rewrite the ENTIRE resume for this job. Requirements:\n"
            "1. ATS-friendly structure with these sections (skip any with no "
            "real content): `# <Candidate Name>` and contact line (keep the "
            "original details; use placeholders like [Your Name] if absent), "
            "`## Professional Summary`, `## Core Skills`, `## Experience` "
            "(reverse-chronological; 2-5 bullets per role, each starting with "
            "a strong action verb and quantified where truthful), "
            "`## Projects` (if any), `## Education`, `## Certifications` (if any).\n"
            "2. Mirror the job's terminology for skills the candidate really has.\n"
            "3. Cut filler and clichés; every bullet must convey a concrete fact.\n"
            "4. Keep it to a realistic one-page (or two-page for 8+ years) length."
        )
        messages = [
            {"role": "system", "content": self.SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        return chat_completion(
            messages,
            model=MODEL_DEEP,
            temperature=0.4,
            json_mode=False,
            max_tokens=3500,
        )

"""
EdU Revolution — RAG Engine
Implements the self-correcting Agentic Reflection Loop:
  1. Intent Analysis
  2. Multi-pass RAG Retrieval
  3. Verification Gate (self-correction)
  4. Profile Matching & Gap Analysis
  5. Structured Output Generation
"""

import json
import logging
import os
import re
from typing import List, Dict, Optional
from groq import Groq

from config import GROQ_API_KEY, GROQ_MODEL
from pdf_processor import PDFProcessor
from registration import INITIATIVES, ACADEMIC_BENEFITS

logger = logging.getLogger("edu_revolution.rag")

# Explicit "I want to file now" phrases — a fast, deterministic path to the form
# that works even without an LLM call.
_READY_PATTERNS = [
    r"\bready to (apply|file|register|submit|enroll|nominat)",
    r"\b(i('?m| am)? ready)\b",
    r"\b(register|enroll|sign) me\b",
    r"\b(apply|file|submit|register|enroll) (me )?now\b",
    r"\bfill (out |in )?(the |my )?(form|application|nomination)\b",
    r"\bstart (the |my )?(application|nomination|registration)\b",
    r"\b(file|submit|start) (a |my |the )?nomination\b",
    r"\b(i want|i'?d like|help me) to (apply|file|register|enroll|submit|nominate)",
    r"\blet'?s (apply|file|register|do it|go)\b",
]
# Short affirmatives — only count as "ready" if the advisor just offered to file.
_AFFIRMATIVES = {
    "yes", "yes please", "yeah", "yep", "sure", "ok", "okay", "yes i am",
    "i am", "i'm ready", "im ready", "ready", "let's do it", "lets do it",
    "go ahead", "please", "do it", "start", "yes lets do it", "yes let's do it",
}
_OFFER_MARKER = "<<OFFER_TO_FILE>>"  # hidden marker the model emits; stripped before display


class RAGEngine:
    """
    The core intelligence engine. Orchestrates the agentic loop
    between the LLM (Groq) and the vector database (ChromaDB).
    """

    def __init__(self, pdf_processor: PDFProcessor):
        self.pdf_processor = pdf_processor
        self.model = GROQ_MODEL
        self.max_rag_iterations = 3  # Maximum self-correction loops (thorough mode)
        self._groq_client: Optional[Groq] = None
        # FAST mode (default): 1 retrieval pass + 1 grounded answer = a single LLM
        # call per question. Set RAG_FAST_MODE=false for the slower multi-call
        # reflection loop (LLM intent analysis + self-correcting verification).
        self.fast_mode = os.getenv("RAG_FAST_MODE", "true").strip().lower() not in ("false", "0", "no")

    @property
    def groq_client(self) -> Groq:
        """Lazy-load Groq client to avoid crash if API key is missing at import time."""
        if self._groq_client is None:
            if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_api_key_here":
                raise ValueError(
                    "Groq API key not configured. "
                    "Please set GROQ_API_KEY in your .env file. "
                    "Get a free key at https://console.groq.com"
                )
            self._groq_client = Groq(api_key=GROQ_API_KEY)
        return self._groq_client

    def _call_llm(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        """Make a call to the Groq LLM."""
        try:
            response = self.groq_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=4096,
            )
            return response.choices[0].message.content.strip()
        except ValueError:
            raise  # Re-raise API key errors
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return f"[LLM Error: {str(e)}]"

    def _parse_json_response(self, text: str) -> Optional[Dict]:
        """Safely parse JSON from LLM response, handling code blocks."""
        try:
            cleaned = text.strip()
            # Remove markdown code block wrapper if present
            if cleaned.startswith("```"):
                # Remove opening ```json or ```
                first_newline = cleaned.index("\n")
                cleaned = cleaned[first_newline + 1:]
                # Remove closing ```
                if "```" in cleaned:
                    cleaned = cleaned[:cleaned.rindex("```")]
            cleaned = cleaned.strip()
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError, IndexError):
            return None

    # =========================================================
    # STEP 1: INTENT ANALYSIS
    # =========================================================
    def analyze_intent(self, student_message: str) -> Dict:
        """
        Determine what the student wants to apply for.
        Extract keywords for RAG retrieval.
        """
        system_prompt = """You are an academic intent analyzer for the EdU Revolution platform.
Your job is to analyze a student's message and extract:
1. The specific course/program they are asking about (if any)
2. The type of information they need (prerequisites, documents, deadlines, process, general)
3. Any profile data they've shared (GPA, background, age, qualifications)
4. Search keywords to query the knowledge base

Respond ONLY in this exact JSON format (no extra text):
{
    "course_name": "extracted course name or 'general' if not specified",
    "info_type": ["prerequisites", "documents", "process", "deadlines", "general"],
    "student_profile": {
        "gpa": null,
        "background": null,
        "age": null,
        "qualifications": [],
        "other_details": []
    },
    "search_queries": ["query1", "query2", "query3"],
    "is_vague": true,
    "implicit_path": "the inferred academic path if the question is vague"
}"""

        result = self._call_llm(system_prompt, student_message, temperature=0.1)
        parsed = self._parse_json_response(result)

        if parsed:
            logger.info(f"Intent parsed: course={parsed.get('course_name')}, vague={parsed.get('is_vague')}")
            return parsed

        # Fallback: use the raw message as a search query
        logger.warning(f"Intent parsing failed, using fallback. Raw LLM output: {result[:200]}")
        return {
            "course_name": "general",
            "info_type": ["general"],
            "student_profile": {
                "gpa": None,
                "background": None,
                "age": None,
                "qualifications": [],
                "other_details": [],
            },
            "search_queries": [student_message[:200]],
            "is_vague": True,
            "implicit_path": "unknown",
        }

    # =========================================================
    # STEP 2: RAG RETRIEVAL
    # =========================================================
    def retrieve_context(self, queries: List[str], n_results_per_query: int = 6) -> List[Dict]:
        """
        Perform multi-query RAG retrieval from the vector database.
        Deduplicates results across queries.
        """
        all_results = []
        seen_texts = set()

        for query in queries:
            if not query or not query.strip():
                continue
            results = self.pdf_processor.query(query.strip(), n_results=n_results_per_query)
            for r in results:
                # Deduplicate by text content (using first 100 chars)
                text_key = r["text"][:100]
                if text_key not in seen_texts:
                    seen_texts.add(text_key)
                    all_results.append(r)

        # Sort by relevance score (highest first)
        all_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        logger.info(f"Retrieved {len(all_results)} unique chunks from {len(queries)} queries")
        return all_results

    # =========================================================
    # STEP 3: VERIFICATION GATE (Self-Correction)
    # =========================================================
    def verify_chunks(self, intent: Dict, chunks: List[Dict]) -> Dict:
        """
        Evaluate retrieved chunk quality. Determine if data is sufficient
        or if a refined query is needed. This is the CRITICAL self-correction step.
        """
        if not chunks:
            return {
                "is_sufficient": False,
                "missing_dimensions": ["prerequisites", "documents", "process"],
                "refined_queries": [intent.get("course_name", "admission requirements")],
                "quality_score": 0,
                "flags": ["No relevant content found in knowledge base."],
            }

        # Prepare chunk summary for evaluation
        chunk_texts = "\n---\n".join([
            f"[Source: {c['source']}, Page {c['page_number']}, Score: {c.get('relevance_score', 0):.2f}]\n{c['text'][:300]}"
            for c in chunks[:8]
        ])

        system_prompt = """You are a data quality evaluator for an academic advisory system.
Evaluate whether the retrieved knowledge base chunks contain SUFFICIENT information to answer the student's query.

Check for THREE dimensions:
1. Prerequisites (academic requirements, GPA, scores, age limits)
2. Core Assets (required documents, certificates, IDs, letters)
3. The Funnel (step-by-step application process, deadlines, portal links)

Respond ONLY in this exact JSON format:
{
    "is_sufficient": true,
    "quality_score": 0.8,
    "found_dimensions": ["prerequisites", "documents"],
    "missing_dimensions": ["process"],
    "refined_queries": ["application process steps"],
    "flags": ["deadline information not found"]
}"""

        user_prompt = f"""Student's intent: {json.dumps(intent, default=str)}

Retrieved chunks ({len(chunks)} total):
{chunk_texts}

Evaluate completeness for answering the student's query."""

        result = self._call_llm(system_prompt, user_prompt, temperature=0.1)
        parsed = self._parse_json_response(result)

        if parsed:
            logger.info(f"Verification: sufficient={parsed.get('is_sufficient')}, score={parsed.get('quality_score')}")
            return parsed

        # Default: assume sufficient if we have at least 3 chunks
        return {
            "is_sufficient": len(chunks) >= 3,
            "quality_score": 0.5,
            "found_dimensions": [],
            "missing_dimensions": [],
            "refined_queries": [],
            "flags": [],
        }

    # =========================================================
    # STEP 4 & 5: PROFILE MATCHING + STRUCTURED OUTPUT
    # =========================================================
    def generate_response(
        self,
        student_message: str,
        intent: Dict,
        chunks: List[Dict],
        verification: Dict,
        conversation_history: List[Dict] = None,
    ) -> str:
        """
        Generate the final structured response using the verified context.
        Performs profile matching, gap analysis, and formats output per specification.
        """
        # Build context from retrieved chunks
        context_text = "\n\n---\n\n".join([
            f"[Source: {c['source']}, Page {c['page_number']}]\n{c['text']}"
            for c in chunks[:12]  # Limit context to avoid token overflow
        ])

        if not context_text:
            context_text = "(No relevant context found in knowledge base)"

        # Build verification flags
        flags_text = ""
        if verification.get("flags"):
            flags_text = "\n⚠️ Data Quality Flags:\n" + "\n".join(
                f"- {f}" for f in verification["flags"]
            )
        if verification.get("missing_dimensions"):
            flags_text += "\n⚠️ Missing Information Dimensions:\n" + "\n".join(
                f"- {d}" for d in verification["missing_dimensions"]
            )

        # Build conversation context (condensed)
        history_text = ""
        if conversation_history and len(conversation_history) > 1:
            recent = conversation_history[-6:]  # Last 3 exchanges
            history_text = "\n\nPrevious conversation:\n" + "\n".join([
                f"{'Student' if m['role'] == 'user' else 'Advisor'}: {m['content'][:300]}"
                for m in recent
            ])

        system_prompt = """You are EDURev Advisor, LPU's EDU Revolution academic advisor. The framework grants academic benefits (Course Equivalence, Grade Upgradation, CA/MTT Evaluation, 10% Attendance relaxation, Duty Leave, RPL, transcript value-addition) for achievements beyond the classroom (Projects, Hackathons, Revenue Generation, Internships, NPTEL/Certifications, Community Service).

RULES:
1. GROUNDING: Use ONLY the provided manual context. If a detail is not in it, say exactly: "⚠️ Verify with admissions office — not in my current manual." Never invent numbers, fees, CGPA cut-offs, deadlines or steps.
2. PRECISE & CONCISE: Answer the student's actual question first, in as few words as it takes — usually 1-4 sentences or a short bullet list. Include ONLY what the question needs; do not dump every section. No filler, no restating the question, no repeating context back.
3. SOLVE & GUIDE: If there's a blocker (missing document, low CGPA/attendance, wrong category, a deadline), name it in one line and give the single concrete next step, or the closest eligible alternative from the manual.
4. ASK IF NEEDED: If a key fact is missing (CGPA, attendance, or the specific activity), ask ONE short question instead of guessing.
5. OFFER TO FILE: Once the target benefit + the activity are clear and you've given actionable guidance, end with a single short line inviting them to file their nomination now — then append the EXACT hidden marker `<<OFFER_TO_FILE>>` on its own final line (it is stripped before the student sees it and powers the "File Nomination" button). Do NOT offer (or emit the marker) while essentials are still missing — ask for them first.

STYLE: Minimal Markdown. Prefer short bullets over paragraphs. Use a bold one-line lead only when it helps. Add a heading only if the answer truly has multiple parts. Keep the whole reply under ~150 words unless the student explicitly asks for the full step-by-step detail."""

        user_prompt = f"""MANUAL CONTEXT:
{context_text}
{flags_text}
{history_text}

STUDENT PROFILE SHARED: {json.dumps(intent.get('student_profile', {}), default=str)}

STUDENT'S MESSAGE:
{student_message}

Answer concisely and precisely, grounded ONLY in the manual context above. Only elaborate into steps/checklists if the student asked for the full process."""

        # Build messages with conversation history for multi-turn context
        messages = [{"role": "system", "content": system_prompt}]
        if conversation_history and len(conversation_history) > 1:
            # Include last few exchanges for continuity (but not too many to exceed token limit)
            for msg in conversation_history[-4:]:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"][:500],
                })
        messages.append({"role": "user", "content": user_prompt})

        try:
            response = self.groq_client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,   # lower = more precise, less rambling
                max_tokens=900,    # keep answers tight
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Response generation failed: {e}")
            return (
                f"❌ I encountered an error processing your request: {str(e)}\n\n"
                "Please check your API key and try again. If the issue persists, "
                "the model may be temporarily unavailable."
            )

    # =========================================================
    # READINESS DETECTION + FORM PREFILL (the "help me file" path)
    # =========================================================
    def detect_readiness(self, student_message: str, conversation_history: List[Dict] = None) -> bool:
        """
        Decide whether the student is signalling they want to FILE now (so we
        should open the nomination form) rather than just ask another question.
        Deterministic — no LLM call, so it is fast and works offline.
        """
        msg = (student_message or "").strip().lower()
        if not msg:
            return False

        for pat in _READY_PATTERNS:
            if re.search(pat, msg):
                return True

        # A bare "yes / ready / sure" only counts if the advisor just offered to file.
        normalized = re.sub(r"[^a-z' ]", "", msg).strip()
        if normalized in _AFFIRMATIVES and self._advisor_recently_offered(conversation_history):
            return True
        return False

    @staticmethod
    def _advisor_recently_offered(conversation_history: List[Dict] = None) -> bool:
        """True if the most recent assistant turn invited the student to file."""
        if not conversation_history:
            return False
        for msg in reversed(conversation_history):
            if msg.get("role") == "assistant":
                content = (msg.get("content") or "").lower()
                return (
                    _OFFER_MARKER.lower() in content
                    or "ready to file" in content
                    or "file your nomination" in content
                    or "open & pre-fill" in content
                    or "open and pre-fill" in content
                )
        return False

    def extract_prefill(self, conversation_history: List[Dict] = None, latest_message: str = "") -> Dict:
        """
        Mine the conversation for anything that pre-fills the nomination form
        (name, CGPA, attendance, chosen initiative/benefit, the activity, ...).
        Returns only the fields it is confident about; unknowns are omitted.
        """
        transcript = ""
        if conversation_history:
            transcript = "\n".join(
                f"{'Student' if m.get('role') == 'user' else 'Advisor'}: {str(m.get('content',''))[:400]}"
                for m in conversation_history[-10:]
            )
        if latest_message:
            transcript += f"\nStudent: {latest_message[:400]}"

        if not transcript.strip():
            return {}

        initiative_keys = ", ".join(i["key"] for i in INITIATIVES)
        benefit_keys = ", ".join(b["key"] for b in ACADEMIC_BENEFITS)
        system_prompt = f"""You extract structured data from a chat between a student and an EDU Revolution advisor to pre-fill a nomination form. Return ONLY fields you are confident about; OMIT anything not clearly stated (do not guess).

Respond ONLY as JSON with any subset of these keys:
{{
  "student_name": "", "registration_id": "", "email": "", "phone": "",
  "program": "", "school": "", "year_of_study": "",
  "cgpa": 0.0, "attendance_percent": 0.0,
  "initiative": "one of: {initiative_keys}",
  "academic_benefit": "one of: {benefit_keys}",
  "activity_title": "", "activity_description": "", "supporting_documents": ""
}}"""
        try:
            raw = self._call_llm(system_prompt, transcript, temperature=0.0)
            parsed = self._parse_json_response(raw) or {}
        except Exception as e:
            logger.warning(f"Prefill extraction failed: {e}")
            return {}

        # Keep only non-empty, known keys
        allowed = {
            "student_name", "registration_id", "email", "phone", "program", "school",
            "year_of_study", "cgpa", "attendance_percent", "initiative",
            "academic_benefit", "activity_title", "activity_description", "supporting_documents",
        }
        clean = {}
        for k, v in parsed.items():
            if k in allowed and v not in (None, "", 0, "0", [], {}):
                clean[k] = v
        return clean

    def _build_application_handoff(self, prefill: Dict) -> str:
        """The message shown when we hand the student off to the form."""
        target = ""
        if prefill.get("academic_benefit") or prefill.get("initiative"):
            benefit = str(prefill.get("academic_benefit", "")).replace("_", " ")
            initiative = str(prefill.get("initiative", "")).replace("_", " ")
            bits = " → ".join([p for p in (initiative, benefit) if p])
            if bits:
                target = f" for **{bits}**"
        return (
            f"Awesome — let's file your EDU Revolution nomination{target}. 📝\n\n"
            "I've opened the application form and pre-filled everything I could gather from our chat. "
            "Please review each field, complete anything that's missing, tick the declaration, and hit "
            "**Submit Nomination** — I'll register it and give you a reference number right away."
        )

    # =========================================================
    # MAIN ORCHESTRATOR — The Agentic Reflection Loop
    # =========================================================
    def process_query(
        self,
        student_message: str,
        conversation_history: List[Dict] = None,
    ) -> Dict:
        """
        Execute the full Agentic Reflection Loop:
        1. Intent Analysis
        2. RAG Retrieval
        3. Verification Gate (with self-correction loops)
        4. Profile Matching & Gap Analysis
        5. Structured Response Generation

        Returns a dict with the response and metadata about the loop execution.
        """
        loop_metadata = {
            "iterations": 0,
            "intent": None,
            "verification": None,
            "chunks_retrieved": 0,
            "sources_used": [],
            "action": "answer",
            "offer_application": False,
            "prefill": {},
        }

        logger.info(f"Processing query: '{student_message[:100]}...'")

        # === STEP 0: Readiness gate — is the student asking to FILE now? ===
        if self.detect_readiness(student_message, conversation_history):
            logger.info("Readiness detected — handing off to the nomination form.")
            prefill = self.extract_prefill(conversation_history, student_message)
            loop_metadata["action"] = "start_application"
            loop_metadata["offer_application"] = True
            loop_metadata["prefill"] = prefill
            return {
                "response": self._build_application_handoff(prefill),
                "metadata": loop_metadata,
            }

        # === FAST PATH (default): 1 retrieval + 1 grounded answer = one LLM call ===
        if self.fast_mode:
            query = student_message.strip() or "EDU Revolution"
            chunks = self.retrieve_context([query], n_results_per_query=8)
            verification = {
                "is_sufficient": bool(chunks),
                "flags": [] if chunks else ["No relevant content found in the manual."],
                "missing_dimensions": [],
            }
            loop_metadata["iterations"] = 1
            loop_metadata["verification"] = verification
            loop_metadata["chunks_retrieved"] = len(chunks)
            loop_metadata["sources_used"] = list({c.get("source", "Unknown") for c in chunks})
            response = self.generate_response(
                student_message=student_message,
                intent={"course_name": "general", "info_type": ["general"], "student_profile": {}},
                chunks=chunks,
                verification=verification,
                conversation_history=conversation_history,
            )
            return self._finalize(response, loop_metadata, student_message, conversation_history)

        # === THOROUGH PATH (RAG_FAST_MODE=false): LLM intent + self-correcting loop ===
        # === STEP 1: Intent Analysis ===
        intent = self.analyze_intent(student_message)
        loop_metadata["intent"] = intent

        # === STEP 2 & 3: Retrieval + Verification Loop ===
        queries = intent.get("search_queries", [student_message])
        # Ensure queries is a list of non-empty strings
        queries = [q for q in queries if q and isinstance(q, str) and q.strip()]
        if not queries:
            queries = [student_message[:200]]

        all_chunks = []
        verification = {
            "is_sufficient": False,
            "quality_score": 0,
            "found_dimensions": [],
            "missing_dimensions": [],
            "refined_queries": [],
            "flags": [],
        }

        for iteration in range(self.max_rag_iterations):
            loop_metadata["iterations"] = iteration + 1
            logger.info(f"RAG iteration {iteration + 1}/{self.max_rag_iterations} — queries: {queries}")

            # Retrieve context
            new_chunks = self.retrieve_context(queries)

            # Merge with existing chunks (deduplicate)
            seen = {c["text"][:100] for c in all_chunks}
            for c in new_chunks:
                key = c["text"][:100]
                if key not in seen:
                    seen.add(key)
                    all_chunks.append(c)

            # Verification Gate
            verification = self.verify_chunks(intent, all_chunks)
            loop_metadata["verification"] = verification

            if verification.get("is_sufficient", False):
                logger.info(f"Verification passed on iteration {iteration + 1}")
                break  # Data quality is good, proceed

            # Self-correction: use refined queries for next iteration
            refined = verification.get("refined_queries", [])
            refined = [q for q in refined if q and isinstance(q, str) and q.strip()]
            if refined:
                queries = refined
            else:
                logger.info("No refined queries available, proceeding with current data")
                break  # No more refinements possible

        # Collect source information
        sources = set()
        for c in all_chunks:
            sources.add(c.get("source", "Unknown"))
        loop_metadata["chunks_retrieved"] = len(all_chunks)
        loop_metadata["sources_used"] = list(sources)

        logger.info(f"Loop complete: {loop_metadata['iterations']} iterations, "
                     f"{len(all_chunks)} chunks from {len(sources)} sources")

        # === STEP 4 & 5: Generate Structured Response ===
        response = self.generate_response(
            student_message=student_message,
            intent=intent,
            chunks=all_chunks,
            verification=verification,
            conversation_history=conversation_history,
        )
        return self._finalize(response, loop_metadata, student_message, conversation_history)

    def _finalize(self, response: str, loop_metadata: Dict,
                  student_message: str, conversation_history: List[Dict] = None) -> Dict:
        """
        Post-process a generated answer: the advisor emits a hidden marker when it
        has offered to file — strip it from what the student sees and light up the
        "File Nomination" CTA (pre-filling the form from the conversation).
        """
        if _OFFER_MARKER in response:
            loop_metadata["offer_application"] = True
            loop_metadata["action"] = "offer_application"
            response = response.replace(_OFFER_MARKER, "").rstrip()
            loop_metadata["prefill"] = self.extract_prefill(conversation_history, student_message)
        return {"response": response, "metadata": loop_metadata}

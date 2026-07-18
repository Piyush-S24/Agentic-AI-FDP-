"""
EDURev Advisor — LLM Agent (Groq tool calling).

This is the agentic core: a real LLM **agent loop**. Each turn the Groq model is
given the conversation plus a set of TOOLS; it decides which tools to call, we run
them, feed the results back, and repeat until the model produces its final answer
(ReAct-style, bounded by ``MAX_ROUNDS``).

Design principle — the LLM *orchestrates*, deterministic engines *decide*:
the agent must call `search_manual` to ground policy facts, `check_eligibility` /
`get_benefit_rules` to judge eligibility, and `verify_proof` for authenticity —
so eligibility and fraud are never the model's own opinion, they are tool results.
The agent's job is planning, gathering, explaining, and driving the student to file.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional

from groq import Groq

from config import GROQ_API_KEY, GROQ_MODEL
from rules import RuleEngine, rules_catalog
from verification import ProofVerifier

logger = logging.getLogger("edu_revolution.agent")

MAX_ROUNDS = 6  # max plan→act cycles before we force a final answer
# Tool-calling is most reliable on the OpenAI open models hosted by Groq; override
# with EDUREV_AGENT_MODEL (any Groq tool-use model works). Falls back to GROQ_MODEL.
AGENT_MODEL = os.getenv("EDUREV_AGENT_MODEL", "openai/gpt-oss-20b") or GROQ_MODEL
# Matches Llama's native tool syntax if Groq ever fails to parse it into tool_calls.
_FN_RE = re.compile(r"<function=([A-Za-z_]\w*)\s*(\{.*?\})\s*</function>", re.S)

_rule_engine = RuleEngine()
_verifier = ProofVerifier()


SYSTEM_PROMPT = """You are EDURev Advisor, LPU's EDU Revolution AI agent. The framework grants academic benefits (Course Equivalence, Grade Upgradation, 10% Attendance Benefit, Duty Leave, CA/MTT Evaluation, RPL, transcript value-addition) for achievements beyond the classroom (Projects/Hackathons, Revenue Generation, NPTEL/Certifications, Internships, Community Service, RPL).

You are an AGENT: think, then use tools, then answer. Rules:
1. GROUND EVERYTHING: for any policy fact (eligibility, thresholds, documents, process) you MUST call `search_manual` first and answer only from what it returns. If the tools don't support a claim, say "⚠️ Verify with admissions office — not in my current manual."
1b. NEVER deflect. Do NOT write "see the manual for details", "refer to the manual", or "check the manual" — YOU read the manual for the student. If you're missing a specific (e.g. the exact CPE first-year conditions), call `search_manual` AGAIN with a more specific query and state the actual details. Only if it's truly absent from the results do you use the ⚠️ Verify line above.
2. Never decide eligibility yourself — call `check_eligibility` (and `get_benefit_rules` for the exact thresholds/proofs). Report thresholds EXACTLY as the tools return them — do NOT invent or round CGPA/attendance/amount numbers. If a rule has no CGPA requirement, say there is none rather than guessing one.
3. For proof authenticity, call `verify_proof`.
4. BE PRECISE & CONCISE: 1-4 sentences or a short list. No filler.
5. DRIVE TO ACTION: the moment the student wants to apply / says they're ready to file, immediately call `open_application_form`, passing every field you already know (initiative, benefit, cgpa, attendance, amounts, activity). Do NOT keep asking for the activity title/description in chat — the form itself collects anything missing. Only hold off if you don't yet know which benefit they want.
6. Use `get_application_status` when the student asks about an existing reference number.
7. BE FAST: request every tool you need in ONE step (batch/parallel tool calls), then answer. Don't take more tool rounds than necessary — usually one search (plus a rule/eligibility check) is enough.
8. INITIATIVES vs BENEFITS — don't confuse them. INITIATIVES (pathways, all present in the manual): revenue_generation, project, nptel_mooc_certification, internship_beyond_curriculum, rpl, community_service. BENEFITS (what you earn): course_equivalence, grade_upgradation, attendance_benefit, duty_leave, evaluation_ca_mtt, rpl_recognition. If the student names an INITIATIVE (e.g. "Revenue Generation" / freelancing income), it DOES exist — use search_manual + get_benefit_rules(initiative) to explain which benefits it earns and the thresholds. NEVER say a topic is "not in the manual" when search_manual returned relevant passages, or just because get_benefit_rules/check_eligibility lacked a rule for one specific combo — a missing digitized rule ≠ absent from the manual.
Call tools when they help; otherwise just answer."""


TOOLS = [
    {"type": "function", "function": {
        "name": "search_manual",
        "description": "Retrieve relevant passages from the official EDU Revolution manual (RAG). Use for ANY policy fact before answering.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look up, e.g. 'grade upgradation revenue threshold'"}},
            "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "get_benefit_rules",
        "description": "Get the digitized rules for an INITIATIVE (thresholds, required proofs, benefits it can earn). Pass just the initiative to list every benefit under it; optionally add a benefit to get that one rule.",
        "parameters": {"type": "object", "properties": {
            "initiative": {"type": "string", "description": "one of: revenue_generation, project, nptel_mooc_certification, internship_beyond_curriculum, rpl, community_service"},
            "benefit": {"type": "string", "description": "OPTIONAL. one of: course_equivalence, grade_upgradation, attendance_benefit, duty_leave, evaluation_ca_mtt, rpl_recognition"}},
            "required": ["initiative"]},
    }},
    {"type": "function", "function": {
        "name": "check_eligibility",
        "description": "Run the deterministic rule engine to judge eligibility for a benefit given the student's numbers. Returns eligible + per-check results + reasons.",
        "parameters": {"type": "object", "properties": {
            "initiative": {"type": "string"}, "benefit": {"type": "string"},
            "cgpa": {"type": "number"}, "attendance_percent": {"type": "number"},
            "revenue_amount": {"type": "number", "description": "₹, for Revenue Generation"},
            "stipend_amount": {"type": "number", "description": "₹/month, for Internships"}},
            "required": ["initiative", "benefit"]},
    }},
    {"type": "function", "function": {
        "name": "verify_proof",
        "description": "Extract and format-validate proof identifiers (DOI, patent no., NPTEL/certificate ID, URL) from proof text. Flags if a human check is needed.",
        "parameters": {"type": "object", "properties": {
            "proof_text": {"type": "string"}}, "required": ["proof_text"]},
    }},
    {"type": "function", "function": {
        "name": "open_application_form",
        "description": "Open the pre-filled nomination form for the student to review and submit. Call when they're ready to file. Pass every field you already know.",
        "parameters": {"type": "object", "properties": {
            "initiative": {"type": "string"}, "academic_benefit": {"type": "string"},
            "activity_title": {"type": "string"}, "activity_description": {"type": "string"},
            "cgpa": {"type": "number"}, "attendance_percent": {"type": "number"},
            "revenue_amount": {"type": "number"}, "stipend_amount": {"type": "number"},
            "student_name": {"type": "string"}, "registration_id": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "get_application_status",
        "description": "Look up a filed nomination by its reference id (e.g. EDU-REV-2026-00001).",
        "parameters": {"type": "object", "properties": {
            "reference_id": {"type": "string"}}, "required": ["reference_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_student_record",
        "description": "Fetch the student's AUTHORITATIVE college record (name, program, CGPA, attendance) by registration id, plus the benefit pathways they already qualify for. Use this to auto-fill and to use real CGPA/attendance instead of asking.",
        "parameters": {"type": "object", "properties": {
            "registration_id": {"type": "string"}}, "required": ["registration_id"]},
    }},
]


class EDURevAgent:
    """A Groq tool-calling agent over the EDU Revolution engines."""

    def __init__(self, pdf_processor, application_store):
        self.pdf_processor = pdf_processor
        self.store = application_store
        self.model = AGENT_MODEL
        self._client: Optional[Groq] = None

    @property
    def client(self) -> Groq:
        if self._client is None:
            if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_api_key_here":
                raise ValueError("Groq API key not configured.")
            self._client = Groq(api_key=GROQ_API_KEY)
        return self._client

    def _extra_kwargs(self) -> Dict:
        # gpt-oss models are reasoning models — keep the 'thinking' minimal for speed.
        return {"reasoning_effort": "low"} if "gpt-oss" in self.model else {}

    # ---------------- tool implementations ----------------
    def _tool_search_manual(self, query: str) -> Dict:
        try:
            hits = self.pdf_processor.query(query, n_results=4)
        except Exception as e:
            return {"error": str(e), "passages": []}
        if not hits:
            return {"passages": [], "note": "Manual not loaded or nothing relevant found."}
        return {"passages": [
            {"page": h.get("page_number"), "text": (h.get("text") or "")[:450]}
            for h in hits
        ]}

    def _tool_get_benefit_rules(self, initiative: str, benefit: str = "") -> Dict:
        initiative = (initiative or "").strip()
        benefit = (benefit or "").strip()
        init_keys = {r["initiative"] for r in rules_catalog()}

        rule = _rule_engine.rule_for(initiative, benefit)
        if rule:
            return {"found": True, "rule": rule}

        # The student likely named an INITIATIVE without a specific benefit (or put it in
        # the wrong field). Resolve to the initiative and list the benefits it can earn.
        target = initiative if initiative in init_keys else (benefit if benefit in init_keys else initiative)
        rules = _rule_engine.rules_for_initiative(target)
        if rules:
            return {
                "found": True, "initiative": target,
                "note": f"'{target}' IS a valid EDU Revolution initiative in the manual — it can earn these benefits:",
                "benefits": [{"benefit": r["benefit"], "objective": r["objective"],
                              "min_cgpa": r.get("min_cgpa"), "min_attendance": r.get("min_attendance"),
                              "required_proofs": r.get("required_proofs"), "note": r.get("note")}
                             for r in rules],
            }
        return {"found": False,
                "note": "No rule for that exact combo — the topic may still be in the manual; use search_manual.",
                "valid_initiatives": sorted(init_keys)}

    def _tool_check_eligibility(self, **kw) -> Dict:
        nom = {k: kw.get(k) for k in
               ("initiative", "cgpa", "attendance_percent", "revenue_amount", "stipend_amount")}
        nom["academic_benefit"] = kw.get("benefit")
        result = _rule_engine.evaluate(nom)
        out = {"eligible": result["eligible"], "objective": result["objective"],
               "checks": result["checks"], "reasons": result["reasons"],
               "required_proofs": result["required_proofs"], "mapped_benefit": result["mapped_benefit"],
               "missing_metric": result["missing_metric"], "matched": result["matched"]}
        if not result["matched"]:
            init_keys = {r["initiative"] for r in rules_catalog()}
            if kw.get("initiative") in init_keys:
                out["guidance"] = (f"'{kw.get('initiative')}' is a valid initiative — but pick a specific benefit "
                                   "for it (see get_benefit_rules). Do NOT tell the student it's not in the manual.")
        return out

    def _tool_verify_proof(self, proof_text: str) -> Dict:
        v = _verifier.verify({"supporting_documents": proof_text})
        return {"identifiers": v["identifiers"], "verified_count": v["verified_count"],
                "needs_human": v["needs_human"], "confidence": v["confidence"], "notes": v["notes"]}

    def _tool_get_application_status(self, reference_id: str) -> Dict:
        rec = self.store.get(reference_id)
        if not rec:
            return {"found": False, "note": f"No nomination found with reference {reference_id}."}
        return {"found": True, "reference_id": rec["reference_id"], "status": rec.get("status"),
                "owner": rec.get("current_owner_label"), "decision": (rec.get("decision") or {}).get("outcome"),
                "reason": (rec.get("decision") or {}).get("reason"), "sla_due": rec.get("sla_due")}

    def _tool_get_student_record(self, registration_id: str) -> Dict:
        rec = self.store.directory.get(registration_id)
        if not rec:
            return {"found": False, "note": f"No college record for registration id {registration_id}."}
        from nudge import find_opportunities
        opp = find_opportunities(rec)
        return {"found": True, "record": rec,
                "qualifies_for": [o["benefit_label"] for o in opp["opportunities"]],
                "highlight": opp["highlight"]}

    def _dispatch(self, name: str, args: Dict, ctx: Dict) -> Dict:
        try:
            if name == "search_manual":
                return self._tool_search_manual(args.get("query", ""))
            if name == "get_benefit_rules":
                return self._tool_get_benefit_rules(args.get("initiative", ""), args.get("benefit", ""))
            if name == "check_eligibility":
                return self._tool_check_eligibility(**args)
            if name == "verify_proof":
                return self._tool_verify_proof(args.get("proof_text", ""))
            if name == "get_application_status":
                return self._tool_get_application_status(args.get("reference_id", ""))
            if name == "get_student_record":
                return self._tool_get_student_record(args.get("registration_id", ""))
            if name == "open_application_form":
                # Signal the UI to open the pre-filled form; capture the prefill.
                prefill = {k: v for k, v in args.items() if v not in (None, "", 0)}
                ctx["action"] = "start_application"
                ctx["prefill"] = prefill
                return {"status": "form_opened",
                        "note": "The pre-filled nomination form is now open for the student to review and submit."}
            return {"error": f"unknown tool {name}"}
        except Exception as e:
            logger.warning(f"Tool {name} failed: {e}")
            return {"error": str(e)}

    # ---------------- the agent loop ----------------
    def _complete(self, messages: List[Dict]):
        """
        One model turn. Returns (content, tool_calls) where tool_calls is a list of
        (id, name, arguments_json). Recovers from Groq's occasional 'tool_use_failed'
        by parsing the model's raw ``<function=...>`` output.
        """
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=TOOLS, tool_choice="auto",
                temperature=0.2, max_tokens=700, **self._extra_kwargs(),
            )
            msg = resp.choices[0].message
            calls = [(tc.id, tc.function.name, tc.function.arguments) for tc in (msg.tool_calls or [])]
            return (msg.content or ""), calls
        except Exception as e:
            recovered = self._recover_tool_calls(e)
            if recovered is None:
                raise
            logger.info(f"Recovered {len(recovered)} tool call(s) from a tool_use_failed response.")
            return "", recovered

    @staticmethod
    def _recover_tool_calls(err) -> Optional[List]:
        """Extract tool calls from a Groq tool_use_failed error's failed_generation."""
        body = getattr(err, "body", None)
        fg = ""
        if isinstance(body, dict):
            fg = (body.get("error") or {}).get("failed_generation", "") or ""
        if not fg:
            return None
        calls = []
        for i, m in enumerate(_FN_RE.finditer(fg)):
            calls.append((f"call_{i}", m.group(1), m.group(2)))
        return calls or None

    def run(self, message: str, conversation_history: List[Dict] = None) -> Dict:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in (conversation_history or [])[-8:]:
            role = m.get("role")
            if role in ("user", "assistant") and m.get("content"):
                messages.append({"role": role, "content": str(m["content"])[:1500]})
        messages.append({"role": "user", "content": message})

        ctx: Dict = {"action": "answer", "prefill": {}}
        tools_used: List[str] = []

        for round_i in range(MAX_ROUNDS):
            try:
                content, calls = self._complete(messages)
            except Exception as e:
                logger.error(f"Agent turn failed: {e}")
                return self._plain_answer(messages, ctx, tools_used, round_i + 1)

            if not calls:
                return self._result(content, ctx, tools_used, round_i + 1)

            # Record the assistant's tool-call turn, then execute each tool.
            messages.append({
                "role": "assistant", "content": content,
                "tool_calls": [{"id": cid, "type": "function",
                                "function": {"name": name, "arguments": args}}
                               for (cid, name, args) in calls],
            })
            for (cid, name, args_json) in calls:
                try:
                    args = json.loads(args_json or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tools_used.append(name)
                result = self._dispatch(name, args, ctx)
                messages.append({"role": "tool", "tool_call_id": cid, "name": name,
                                 "content": json.dumps(result, ensure_ascii=False)[:4000]})

        # Ran out of rounds — force a plain final answer.
        return self._plain_answer(messages, ctx, tools_used, MAX_ROUNDS)

    def _plain_answer(self, messages, ctx, tools_used, rounds) -> Dict:
        """Final answer with NO tools (used on tool errors or after MAX_ROUNDS)."""
        try:
            final = self.client.chat.completions.create(
                model=self.model,
                messages=messages + [{"role": "user",
                    "content": "Give your final concise answer to the student now, grounded only in the tool results above. Do not call any tools."}],
                temperature=0.3, max_tokens=700, **self._extra_kwargs(),
            )
            text = final.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"Plain answer failed: {e}")
            text = "⚠️ I hit a temporary error. Please rephrase your question or try again."
        return self._result(text, ctx, tools_used, rounds)

    @staticmethod
    def _result(text: str, ctx: Dict, tools_used: List[str], rounds: int) -> Dict:
        offered = ctx.get("action") == "start_application"
        return {
            "response": text.strip(),
            "metadata": {
                "engine": "agent",
                "action": ctx.get("action", "answer"),
                "offer_application": offered,
                "prefill": ctx.get("prefill", {}),
                "tools_used": tools_used,
                "iterations": rounds,
            },
        }

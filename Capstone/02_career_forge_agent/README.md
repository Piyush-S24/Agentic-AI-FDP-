# CareerForge

A multi-agent evaluation system that compares a candidate against a target role
and produces a personalized, time-bounded career pathway. Built on the **Groq**
Python SDK with **FastAPI**.

## Repository layout

```
02_career_forge_agent/
├── main.py                     # FastAPI endpoints
├── agents.py                   # Profile / Verbal / Technical / Pathway agents
├── state.py                    # Pydantic schemas + session store
├── utils.py                    # Groq wrappers, model routing, resume utils
├── requirements.txt
├── sample_pathway.md           # Example of a generated career pathway
├── static/                     # Web UI (upload resume + 4-step wizard)
└── learning_modules/           # Educational notebooks (concepts)
    ├── 01_groq_basics.ipynb        # Groq client, chat completions, strict JSON
    ├── 02_rag_foundations.ipynb    # Simulated pgvector retrieval + grounding
    └── 03_agentic_workflows.ipynb  # State, validation rules, routing, tools
```

## The pipeline (4 agents, 3 candidate-facing steps)

| Step | Agent | Model | Output |
|------|-------|-------|--------|
| 1 | `ProfileAgent` | `llama-3.3-70b-versatile` | Semantic gap analysis (skills/tools/experience) |
| 2 | `VerbalAgent` | `llama-3.1-8b-instant` | 3 gap-driven questions, graded 0–100 |
| 3 | `TechnicalAgent` | `llama-3.1-8b-instant` | Writing/scenario challenge scored on 4 axes |
| 4 | `PathwayAgent` | `llama-3.3-70b-versatile` | Markdown pathway: scorecard, objectives, weekly plan |

Deep reasoning steps (1 & 4) use the large model; fast interactive rounds
(2 & 3) use the instant model.

## Setup

```powershell
cd 02_career_forge_agent
python -m pip install -r requirements.txt
# copy the env template and add your key:
copy .env.example .env    # then edit GROQ_API_KEY
```

> **Security:** never commit `.env`. Keys are read from `GROQ_API_KEY` at runtime;
> nothing is hardcoded.

## Run

```powershell
cd 02_career_forge_agent
uvicorn main:app --reload
```

Then open the app:
- **http://localhost:8000/** — the interactive web UI (upload a PDF resume and
  click through the 4-step wizard). **Start here.**
- **http://localhost:8000/docs** — raw Swagger API docs (for developers).

## API flow

0. **`POST /api/v1/parse-resume`** — multipart PDF upload; returns the extracted
   `resume_text`. (The web UI calls this when you drop in a PDF.)
1. **`POST /api/v1/start-evaluation`** — send `resume_text`, `job_description`,
   `target_role`. Returns `session_id`, the gap analysis, and the first question.
2. **`POST /api/v1/submit-answer`** — send `session_id` + `answer`. Repeat for each
   question; after the 3rd the response switches to the **writing challenge**.
   Submit your written response to the same endpoint to finish.
3. **`POST /api/v1/final-pathway`** — send `session_id` to receive the Markdown
   career pathway.

### Example

```bash
curl -X POST localhost:8000/api/v1/start-evaluation \
  -H "Content-Type: application/json" \
  -d '{"resume_text":"5y Python...","job_description":"ML Engineer needing Docker + MLOps","target_role":"ML Engineer"}'
```

## Design notes

- **State machine:** `/submit-answer` is multiplexed by session `phase`
  (`verbal` → `writing` → `complete`), mirroring the routing concept taught in
  `03_agentic_workflows.ipynb`.
- **Structured output:** every LLM call that must be machine-readable goes through
  `utils.structured_completion`, which requests JSON mode and validates the result
  against a Pydantic schema — bad output fails fast with `LLMParseError`.
- **Swappable session store:** `state.SessionStore` is in-memory but exposes a
  narrow `create/get/save` interface; back it with Redis/Postgres for production.
- **Explicit errors:** `LLMConfigError` → HTTP 503, `CareerForgeError` → HTTP 502,
  unknown session → HTTP 404, wrong phase → HTTP 409.

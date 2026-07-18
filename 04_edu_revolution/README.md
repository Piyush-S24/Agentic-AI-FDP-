# EDURev Advisor — LPU EDU Revolution Advisor + Nomination Agent

An AI academic advisor for LPU's **EDU Revolution** framework (which converts real-world
achievements — projects, hackathons, internships, revenue, certifications — into academic
benefits like Course Equivalence, Grade Upgradation, Attendance relaxation and Duty Leave).

Students **don't start at a blank chatbot**: the home screen offers **topic cards** for each
EDU Revolution benefit/initiative (Course Equivalence, Grade Upgradation, Attendance Benefit,
Duty Leave, RPL, Revenue Generation, Projects, NPTEL, Internships). Picking one drops the
student into a focused chat where the advisor:

1. **Answers** questions **strictly from the official EDU Revolution manual** using a
   **self-correcting Agentic Reflection Loop** (retrieve → verify context actually answers →
   re-retrieve up to 3× → respond) so it grounds every answer instead of hallucinating.
2. **Solves & guides** — it diagnoses the student's real blocker (a missing document, low
   CGPA/attendance, wrong category, a looming deadline), points to the closest achievable
   path, and steers them toward actually filing.
3. **Files the nomination** — when the student is ready, it asks *"ready to file?"*, opens a
   **pre-filled application form**, checks eligibility against the manual's rules, and
   **directly registers** the nomination — returning a reference number and the official
   next steps.

> **The knowledge base is maintained by the University, not students.** There is no
> student-facing PDF upload — the official manual is loaded/updated by an administrator (see
> *Load the knowledge base* below), so students only ever ask and file.

Built with **FastAPI**, **ChromaDB** (local vector store), **sentence-transformers**
embeddings, **PyPDF2** for extraction, and the **Groq** SDK for the LLM.

## Agentic core — Groq tool-calling agent (`agent.py`)

Chat is driven by a real **LLM agent** (ReAct-style tool-calling loop), the default engine
for `/api/chat`. Each turn the Groq model plans, calls **tools**, reads the results, and
loops (up to `MAX_ROUNDS`) until it answers or acts:

```
user ─▶ [ LLM agent ] ─▶ picks a tool ─▶ run tool ─▶ feed result back ─▶ … ─▶ answer / open form
                          search_manual · get_benefit_rules · check_eligibility
                          verify_proof · open_application_form · get_application_status
```

**Key design — the LLM orchestrates, the deterministic engines decide.** The agent *must*
call `search_manual` (RAG) to ground policy facts and `check_eligibility` / `get_benefit_rules`
(the rule engine) to judge eligibility — so eligibility and fraud are tool results, never the
model's opinion. The agent's job is planning, gathering, explaining, and driving the student
to file (`open_application_form` opens the pre-filled nomination form). The tools it calls are
shown in the chat as a 🛠️ badge, so the agentic reasoning is visible.

- **Model:** `EDUREV_AGENT_MODEL` (default `openai/gpt-oss-20b` — the most reliable tool-caller
  on Groq). Robust to Groq's occasional `tool_use_failed` (it recovers the tool call from the
  raw model output) and falls back to a plain answer on any tool error.
- **Fallback:** set `EDUREV_AGENT_MODE=false` to use the plain RAG reflection loop below instead.

## The RAG Reflection Loop (`rag_engine.py`) — fallback engine

```
student question
      │
      ▼
1. Intent analysis      → what is the student really asking?
2. Multi-pass retrieval → top-k chunks from ChromaDB
3. Verification gate    → "does this context answer it?"  ── no ──┐
      │ yes                                                       │ (re-query, max 3x)
      ▼                                                           │
4. Gap analysis / profile matching  ◀───────────────────────────┘
5. Structured, grounded answer
```

If the knowledge base can't answer, the advisor says so rather than inventing details.

## Guided nomination filing (the agent that registers you)

Beyond Q&A, the advisor moves the student toward action and can file the nomination:

```
answer question ─▶ diagnose blocker & guide ─▶ "Ready to file?" ─▶ pre-filled form ─▶ REGISTER
                                                                                          │
                                                          reference ID + eligibility ◀────┘
                                                          + official next steps
```

- **Readiness detection** (`rag_engine.detect_readiness`) — deterministic (no LLM needed):
  phrases like *"I'm ready to apply"*, *"register me"*, *"fill the form"*, or a plain *"yes"*
  right after the advisor offered, open the form automatically.
- **Auto pre-fill** (`rag_engine.extract_prefill`) — mines the conversation for the student's
  name, CGPA, attendance, chosen initiative/benefit and the activity, so the form opens
  ready-to-submit. A persistent **📝 File Nomination** button is always available too.
- **Eligibility check** (`registration.check_eligibility`) — advisory flags grounded in the
  manual (e.g. 10% Attendance Benefit needs CGPA ≥ 7.5; projects/internships need CGPA ≥ 6.0;
  ≥ 60/65% attendance floors; ETE still mandatory) — guidance only, since the Standing
  Committee's decision is final.
- **Registration** (`registration.ApplicationStore`) — the nomination is validated and
  persisted to `data/applications.jsonl`, returning a reference ID like `EDU-REV-2026-00001`
  plus the official portal path (UMS → LMS → *Edu Revolution* → Apply). There is no external
  UMS API to call, so "registration" = a durable, retrievable record; swap the store for a
  real UMS/LMS integration to go live.

## EDU Revolution 2.0 — automated decisioning

Every filed nomination runs through a **deterministic automation pipeline** (no LLM in the
decision path — eligibility is decided by explicit rules):

```
submit ─▶ Rule Engine ─▶ Verification ─▶ Duplicate/Fraud ─▶ Decision Engine ─▶ status + SLA
          (rules.py)     (verification)   (duplicate.py)     (decision.py)      + audit log
             │                │                 │                  │
       digitized policy   proof IDs +      cross-student      auto_approve /
       thresholds/proofs  format check     proof reuse =      auto_reject /
                          (ext. lookups    fraud signal       escalate → hierarchy
                           are stubs)
```

- **Rule Engine** (`rules.py`) — the policy's objective criteria (CGPA/attendance floors,
  revenue & stipend matrices, required proofs, mapped benefits) are digitized as versioned
  data and surfaced at intake (`GET /api/rules`). Fully rule-decidable vs subjective cases
  are marked.
- **Proof documents** (`proofs.py`) — students **attach their evidence** while filing
  (certificate, revenue statement, stipend letter, patent PDF). Each file is validated
  (type + ≤10MB), **SHA-256 hashed**, and **PDF text is extracted** so the verifier can read
  identifiers *inside* the document. Stored in `data/proofs/` with a server-side index —
  the client only ever passes proof **ids**, so filename/hash/text can't be spoofed.
  *(Note: this is the student's own evidence — distinct from the University-only KB upload.)*
- **Verification / anti-fraud** (`verification.py`) — extracts DOIs, patent numbers,
  NPTEL/certificate IDs and URLs from the typed text **and the uploaded documents**, and
  validates their **format**; live confirmation against issuing sources (patent office, DOI
  resolver, NPTEL) is a documented extension point. Proofs with no verifiable identifier
  route to a human.
- **Duplicate & fraud** (`duplicate.py`) — a proof identifier **or an identical uploaded file
  (SHA-256)** shared with **another** student is a high-risk fraud signal — caught even if the
  title and wording are completely different. A student re-using their own proof across stages
  is recognised as legitimate progression.
- **Decision Engine** (`decision.py`) — combines the three signals into a **confidence-scored**
  outcome: `auto_approve` / `auto_reject` (with a cited reason) / `escalate`, routed into the
  4-level hierarchy (Student → School Coordinator → Standing Committee → Registrar) with an SLA.
- **Achievement Profile** (`registration.AchievementStore`) — one evolving record per real
  achievement, reused across stages so students don't re-type (`GET /api/achievements/{id}`).
- **Audit trail** — every AI/human decision is logged with the rule version and evidence.
- **Staff Console** (`/staff`) — a review queue (pre-scored, fraud-ranked, expandable evidence,
  one-click approve/reject/escalate) plus an analytics dashboard (auto-resolution rate,
  turnaround, fraud flags, category mix).

### Connected to student records (the multiplier)

With a read connection to the college's student data, EDURev decides on **authoritative**
numbers instead of what the student types:

- **Student Directory** (`directory.py`) — the authoritative source for identity + academic
  record (CGPA, attendance, program, year). Ships with a seeded mock directory; swap
  `StudentDirectory` for the real **UMS/LMS API** by overriding `get`/`all_records`/`verify`.
- **Authoritative override + integrity check** — on filing, if the registration id is in the
  directory, the **record's** CGPA/attendance are used for the decision, and any gap with what
  was self-reported is flagged. **A student can no longer inflate their CGPA to clear a
  threshold** — the mismatch is caught and the case is escalated, never auto-approved.
- **Identity verification** (`POST /api/login`) — registration id + a second factor (DOB here;
  UMS SSO in production). The form auto-fills and **locks** CGPA/attendance from the record.
- **Proactive Nudge Agent** (`nudge.py`) — scans the directory for students who already clear a
  benefit's prerequisites and suggests it (`/api/nudges` for staff outreach,
  `/api/nudges/{id}` + the login response for the student). Turns the reactive system proactive.
- The **agent** has a `get_student_record` tool, so in chat it pulls real CGPA/attendance and
  tells the student what they already qualify for — no more asking for numbers it can look up.

**Proof verification** now also **OCRs image proofs** (needs Tesseract; degrades gracefully if
absent) and has a **live external-confirmation** path (`EDUREV_LIVE_VERIFY=true`) with **DOI
resolution implemented** (credential-free) and patent/NPTEL wired the same way.

**Still deferred** (need real infra/credentials): the actual UMS API hookup (interface is ready),
UMS SSO, full multi-tenant DB isolation (a `tenant_id` field is carried), and write-back to LMS.

## Project layout

```
04_edu_revolution/
├── app.py             # FastAPI server + REST endpoints (chat / register / review / analytics …)
├── agent.py           # Groq tool-calling AGENT (default chat engine) over the tools below
├── rag_engine.py      # RAG reflection loop (fallback chat engine) + readiness/pre-fill
├── registration.py    # nomination store + automation pipeline + achievement profile
├── directory.py       # Student Directory connector (authoritative record; real-UMS-ready)
├── nudge.py           # proactive Nudge Agent (who already qualifies for what)
├── proofs.py          # student proof-document store (validate, SHA-256, PDF/OCR text extract)
├── rules.py           # digitized policy Rule Engine (versioned)
├── verification.py    # proof identifier extraction + format validation (anti-fraud)
├── duplicate.py       # duplicate & cross-student fraud detection
├── decision.py        # confidence-scored Decision Engine + escalation hierarchy + SLA
├── pdf_processor.py   # PDF → text → chunks → embeddings → ChromaDB
├── config.py          # env-driven settings (paths anchored to the project, model, chunking)
├── add_manual.py      # one-shot: seed the bundled manual into the KB
├── diagnose.py        # health check — verifies deps, .env, static, imports
├── static/            # student UI (index.html/styles.css/app.js) + staff.html console
├── scripts/           # dev/debug helpers (PDF/DB preview) — not needed to run
├── setup.bat / start.bat
├── requirements.txt
└── 260706155032419_1_EdU Revolution Manual.pdf   # sample KB source
```

> `chroma_db/`, `uploads/` and `data/` are created at runtime and are git-ignored — the
> vector DB is (re)built when you seed/upload, and `data/` holds filed nominations (student
> PII), so none are checked in.

## Setup

```powershell
# 1. install dependencies (torch + chromadb + sentence-transformers — first run is slow)
pip install -r requirements.txt

# 2. configure your key
copy .env.example .env      # then edit GROQ_API_KEY (free at https://console.groq.com/keys)

# 3. (optional) sanity-check the environment
python diagnose.py
```

Windows users can instead just run `setup.bat`.

## Load the knowledge base (administrator)

The manual is University-maintained — there is no student upload. An administrator seeds or
updates it once by ingesting the bundled EDU Revolution Manual (extract → chunk → embed →
store in ChromaDB):

```powershell
python add_manual.py
```

> The upload/document REST endpoints still exist for **administrator** tooling, but they are
> intentionally **not** exposed anywhere in the student UI.

## Run

```powershell
python app.py
#   or:  python -m uvicorn app:app --reload
#   or:  start.bat
```

- **http://localhost:8000** — student app (topic cards + advisor chat + nomination form)
- **http://localhost:8000/staff** — staff console (review queue + analytics)

## Endpoints

| Method | Path                     | Description                              |
|--------|--------------------------|------------------------------------------|
| GET    | `/`                      | Student web UI                           |
| GET    | `/staff`                 | Staff review + analytics console         |
| GET    | `/api/health`            | Deps / KB / API-key status               |
| POST   | `/api/chat`              | Ask the advisor (may hand off to the form) |
| POST   | `/api/chat/reset`        | Clear a session's conversation history   |
| GET    | `/api/initiatives`       | Initiatives / benefits / years for the form dropdowns |
| POST   | `/api/prefill`           | Pre-fill the form from a session's conversation |
| POST   | `/api/proofs`            | **Upload proof documents** (multipart) — validates, hashes, extracts PDF/OCR text; returns proof ids |
| GET    | `/api/proofs/{id}`       | Download a stored proof document (staff review)  |
| POST   | `/api/login`             | **Verify identity** against college records → authoritative profile + nudges |
| GET    | `/api/student/{regId}`   | Authoritative academic record                    |
| GET    | `/api/nudges/{regId}`    | Benefits a student already qualifies for         |
| GET    | `/api/nudges`            | Directory-wide nudge scan (staff outreach)       |
| POST   | `/api/register`          | **File a nomination** — validate, run the automation pipeline, persist, return the decision |
| GET    | `/api/applications[/{ref}]` | List / fetch filed nominations        |
| GET    | `/api/rules`             | Digitized policy rules + hierarchy (2.0) |
| GET    | `/api/review/queue`      | Staff review queue (pre-scored, fraud-ranked) |
| POST   | `/api/review/{ref}/decision` | Record a staff decision (+ audit log) |
| GET    | `/api/analytics`         | Aggregate metrics (auto-resolution, fraud, turnaround) |
| GET    | `/api/achievements/{regId}` | A student's persistent Achievement Profile |
| POST   | `/api/upload` · GET `/api/documents` · DELETE `/api/documents/{id}` | Admin KB tooling (not in student UI) |

## Configuration (`.env`)

| Variable         | Default                     | Purpose                          |
|------------------|-----------------------------|----------------------------------|
| `GROQ_API_KEY`   | —                           | Groq LLM access (required to chat) |
| `GROQ_MODEL`     | `llama-3.3-70b-versatile`   | Chat/reasoning model (set `llama-3.1-8b-instant` for ~2× faster replies) |
| `RAG_FAST_MODE`  | `true`                      | `true` = 1 retrieval + 1 grounded answer (one LLM call, ~1.5 s). `false` = multi-call self-correcting reflection loop (slower, more thorough) |
| `CHROMA_DB_PATH` | `./chroma_db`               | Vector store (relative → resolved inside the project) |
| `UPLOAD_DIR`     | `./uploads`                 | Where uploaded PDFs are saved     |

### Speed

Each question is **one Groq call** in the default fast mode (down from 3–5), and the
concise-answer prompt keeps outputs short — warm latency is ~1.5 s (or ~0.6 s on
`llama-3.1-8b-instant`). The very first question after startup is slower (~10–15 s) while
Groq warms the model; subsequent ones are fast.

Embeddings use `all-MiniLM-L6-v2` (384-dim); text is split at 1000 chars / 200 overlap
(`config.py`).

## Notes

- The server boots even **without** a key or documents — the UI then prompts you to add
  them (chat replies with a friendly "configure your key / upload a PDF" message).
- Replace the bundled manual with your institution's real PDFs to make it your own.

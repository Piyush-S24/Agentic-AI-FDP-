# Capstone — Modern AI, GenAI & Agentic Systems

A single, organized capstone repository: one **learning module** that teaches the concepts
day by day, followed by **three self-contained projects** that put those concepts into
production. Everything is numbered so you can read it in order.

```
Capstone/
├── 01_learning/            12-day programme — a README + notebook per day
├── 02_career_forge_agent/  multi-agent career evaluator (Groq + FastAPI)
├── 03_graphagent_studio/   interactive LangGraph playground (planner/critique loops)
└── 04_edu_revolution/      EDURev Advisor — LPU EDU Revolution advisor + nomination agent
```

## Learning module

**[`01_learning/`](01_learning/README.md)** — a 12-day path from classical ML and
transformers through LLM APIs, tool calling, RAG, ReAct agents, and LangGraph
multi-agent orchestration. Each day has a written walkthrough **and** a hands-on notebook.

## Projects

| # | Project | What it is | Stack | Run |
|---|---------|------------|-------|-----|
| 02 | [**CareerForge**](02_career_forge_agent/README.md) | 4-agent pipeline that compares a résumé to a target role and produces a personalized career pathway | Groq SDK · FastAPI · Pydantic | `uvicorn main:app --reload` |
| 03 | [**GraphAgent Studio**](03_graphagent_studio/README.md) | Visual LangGraph playground: planner/executor, critique loops, human-in-the-loop | LangGraph · LangChain · FastAPI | `python app.py` |
| 04 | [**EDURev Advisor**](04_edu_revolution/README.md) | LPU EDU Revolution advisor: answers from the official manual, guides the student, and **files their nomination** | ChromaDB · sentence-transformers · Groq · FastAPI | `python app.py` |

Each project folder is **self-contained**: its own `README.md`, `requirements.txt`,
`.env.example`, and `.gitignore`.

## Getting started (any project)

```powershell
cd <project_folder>            # e.g. cd 04_edu_revolution
python -m pip install -r requirements.txt
copy .env.example .env         # then paste your GROQ_API_KEY
python app.py                  # (CareerForge uses: uvicorn main:app --reload)
```

Get a free Groq key at <https://console.groq.com/keys>. GraphAgent Studio (03) also runs
in a no-key **Simulation Mode**. For EDURev Advisor (04), an administrator seeds the
official manual once with `python add_manual.py` (run it **from inside** the project folder).

## Security

No secrets are committed. Real API keys were removed during setup — each project ships
only a placeholder `.env.example`, and every `.gitignore` excludes `.env`. Add your own
key locally in a `.env` file (never commit it).

# GraphAgent Studio — Interactive LangGraph Playground

GraphAgent Studio is a premium web-based playground built using **FastAPI** and **LangGraph** (underpinned by LangChain). It demonstrates how to model agentic workflows as state machines (nodes, edges, and state memory) rather than handwritten procedural loops.

It provides a rich visual dashboard that showcases:
1. **Planner-Executor Pattern**: Splits the thinking process (creating a structured plan) from execution (running workers).
2. **State Management**: Uses a centralized `TypedDict` clipboard passed between steps, merging incremental updates.
3. **Cyclic Loops & Self-Correction**: Implements critique-driven rewrite loops (automatically loops back if draft score is low).
4. **State Persistence**: Uses LangGraph's checkpointer (`InMemorySaver`) to save state checkpoints using `thread_id` keys.
5. **Human-in-the-Loop Interrupts**: Pauses execution before entering nodes requiring human validation (borderline draft ratings), allowing manual reviews, custom instructions, and resumption.
6. **Simulation Mode Fallback**: Runs 100% interactively without requiring any external keys using a built-in mock LLM emulator.

---

## 🛠️ Tech Stack
- **Backend**: Python 3.9+, FastAPI, LangGraph, LangChain, Pydantic
- **Frontend**: HTML5, Vanilla CSS3 (dark theme, glassmorphism, responsive grid), Vanilla JavaScript (SVG nodes/edges visual mapping, State Inspector, history step selection).

---

## ⚙️ Installation & Setup

1. **Clone/Navigate to this folder**:
   ```bash
   cd 05_graphagent_studio
   ```

2. **Install Dependencies**:
   It is recommended to use `uv` for extremely fast installations, or standard `pip`:
   ```bash
   # Option A: Fast installation with uv
   uv pip install -r requirements.txt
   
   # Option B: Standard pip
   pip install -r requirements.txt
   ```

3. **Configure API Key (Optional)**:
   - Create a `.env` file from the template:
     ```bash
     copy .env.example .env
     ```
   - Edit `.env` and fill in your Groq API Key: `GROQ_API_KEY=gsk_...`.
   - *Note: If no API key is specified, the application launches in **Simulation Mode** (perfect for debugging the graph layout, loops, and human checkpointing instantly).*
   - Alternatively, you can copy-paste your key directly into the settings panel on the web page interface!

4. **Run the Application**:
   ```bash
   python app.py
   ```

5. **Open the browser**:
   Navigate to [http://localhost:8000](http://localhost:8000).

---

## 🗺️ How to Use & Debug GraphAgent Studio

1. **Submit a Topic**: Enter a request like `"Compare the populations of Ludhiana and Amritsar, then say which is bigger"` or choose one of the suggestion tags.
2. **Watch the Nodes Glow**: As the graph executes, you'll see the active node glow in purple.
   - **START** -> **Planner** (creates tasks) -> **Executor** (iterates over steps one-by-one) -> **Synthesizer** (merges results) -> **Critique** (reviews final draft).
3. **Trigger the Self-Correction Cycle**:
   - The Critique node rates the draft quality. 
   - If the score is `< 6.0`, it triggers `prepare_rewrite` and loops back to **Executor** to rewrite.
4. **Trigger Human Interrupt**:
   - If the score is in the borderline zone (`6.0` to `8.0`), the LangGraph checkpointer will **interrupt** execution before entering **Human Approval**.
   - The UI will reveal an orange **Human Intervention Box**.
   - You can review the draft, type revision instructions, and click **Request Rewrite** (loops back to Executor) or **Approve Draft** (completes run).
5. **Inspect State History Diffs**:
   - Check the left sidebar's **State History** panel. Because checkpoints are saved under a `thread_id`, you can click past checkpoints (e.g. *Planner Checkpoint*, *Critique Checkpoint*) to view exactly what the draft, plans, and variables looked like at that moment!

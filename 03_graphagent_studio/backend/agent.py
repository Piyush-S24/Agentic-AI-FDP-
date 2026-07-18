import datetime
from typing import Dict, Any, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from backend.models import AgentState, Plan, Critique
from backend.llm import get_llm

# Helper to create a log entry
def create_log(node_name: str, status: str, message: str) -> dict:
    return {
        "node": node_name,
        "status": status,
        "message": message,
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
    }

# --- NODES ---

def node_planner(state: AgentState, config: Dict[str, Any] = None):
    api_key = config.get("configurable", {}).get("api_key") if config else None
    llm = get_llm(api_key)
    
    question = state["question"]
    prompt = f"Break this request into 2-4 simple sub-tasks. Request: {question}"
    
    # We use structured output to get a typed list of steps
    planner_llm = llm.with_structured_output(Plan)
    plan_obj = planner_llm.invoke(prompt)
    
    steps = plan_obj.steps if plan_obj and plan_obj.steps else ["Draft the initial content"]
    
    steps_list_str = ", ".join([f"'{s}'" for s in steps])
    log_msg = f"Generated writing plan with {len(steps)} steps: {steps_list_str}"
    
    return {
        "plan": steps,
        "current_step_index": 0,
        "worker_results": [],
        "tries": state.get("tries", 0),
        "approved": False,
        "awaiting_approval": False,
        "logs": state.get("logs", []) + [create_log("Planner", "success", log_msg)]
    }

def node_executor(state: AgentState, config: Dict[str, Any] = None):
    api_key = config.get("configurable", {}).get("api_key") if config else None
    llm = get_llm(api_key)
    
    plan = state["plan"]
    idx = state["current_step_index"]
    results = list(state.get("worker_results", []))
    
    subtask = plan[idx]
    
    # Customize prompt if we are doing a revision based on previous critique
    critique_context = ""
    if state.get("critique") and idx == 0:
        critique_context = f"\nNote: The previous draft was reviewed and critiqued. Feedback: {state['critique']}. Please improve details accordingly."
        
    prompt = f"Complete this single task concisely: {subtask}{critique_context}"
    response = llm.invoke(prompt)
    answer = response.content
    
    results.append(answer)
    next_idx = idx + 1
    
    log_msg = f"Completed sub-task {next_idx}/{len(plan)}: '{subtask}'."
    
    return {
        "worker_results": results,
        "current_step_index": next_idx,
        "logs": state.get("logs", []) + [create_log("Executor", "success", log_msg)]
    }

def node_synthesizer(state: AgentState, config: Dict[str, Any] = None):
    api_key = config.get("configurable", {}).get("api_key") if config else None
    llm = get_llm(api_key)
    
    question = state["question"]
    worker_results = state["worker_results"]
    plan = state["plan"]
    
    # Combine sub-task responses for context
    combined_steps = []
    for step, res in zip(plan, worker_results):
        combined_steps.append(f"Sub-task: {step}\nResult:\n{res}")
    
    context_str = "\n\n".join(combined_steps)
    prompt = (
        f"Using these sub-results, compile a final, comprehensive, and well-structured answer to '{question}'. "
        f"Ensure it flows logically, uses Markdown headers/bullet points, and is engaging.\n\n"
        f"Sub-results:\n{context_str}"
    )
    
    response = llm.invoke(prompt)
    draft = response.content
    
    log_msg = f"Synthesized sub-results into final draft ({len(draft)} characters)."
    
    return {
        "draft": draft,
        "logs": state.get("logs", []) + [create_log("Synthesizer", "success", log_msg)]
    }

def node_critique(state: AgentState, config: Dict[str, Any] = None):
    api_key = config.get("configurable", {}).get("api_key") if config else None
    llm = get_llm(api_key)
    
    draft = state["draft"]
    prompt = (
        f"Review the draft content against the original request: '{state['question']}'.\n\n"
        f"Draft to review:\n{draft}\n\n"
        f"Provide a quality score (0.0 to 10.0) and detailed feedback. "
        f"Use this strict grading rubric:\n"
        f"- **9.0 to 10.0**: Outstanding. Publication-ready, perfectly structured with markdown headers, bold terms, lists, and deep factual content.\n"
        f"- **8.0 to 8.9**: Good content, but missing minor details or could have slightly better flow.\n"
        f"- **6.0 to 7.9**: Acceptable information, but lacks rich markdown formatting, has no bullet points/headers, or feels plain. (Borderline - needs human touch)\n"
        f"- **Below 6.0**: Incomplete, too short, or lacks correct factual answers.\n\n"
        f"Be highly critical. Do not award a score of 8.0 or higher if the draft lacks structured formatting (headers, bullet points) or is a single plain block of text."
    )
    
    critic_llm = llm.with_structured_output(Critique)
    critique_obj = critic_llm.invoke(prompt)
    
    score = critique_obj.score if critique_obj else 5.0
    feedback = critique_obj.feedback if critique_obj else "Review failed."
    tries = state.get("tries", 0) + 1
    
    log_msg = f"Critique complete. Score: {score}/10. Feedback: '{feedback}' (Try {tries})"
    
    return {
        "score": score,
        "critique": feedback,
        "tries": tries,
        "logs": state.get("logs", []) + [create_log("Critique", "success", log_msg)]
    }

def node_prepare_rewrite(state: AgentState):
    # Clears executor parameters to trigger a fresh cycle through plan steps
    log_msg = f"Self-correction loop triggered. Re-running plan steps with critique feedback."
    return {
        "current_step_index": 0,
        "worker_results": [],
        "logs": state.get("logs", []) + [create_log("PrepareRewrite", "success", log_msg)]
    }

def node_human_approval(state: AgentState):
    # This node executes when the graph resumes from the interrupt
    feedback = state.get("human_feedback", "")
    approved = state.get("approved", False)
    
    if approved:
        status_msg = "Human approved the draft."
    else:
        status_msg = f"Human rejected the draft. Feedback: '{feedback}'"
        
    return {
        "awaiting_approval": False,
        "logs": state.get("logs", []) + [create_log("HumanApproval", "success", status_msg)]
    }

def node_done(state: AgentState):
    log_msg = "Graph execution completed successfully."
    return {
        "approved": True,
        "awaiting_approval": False,
        "logs": state.get("logs", []) + [create_log("Done", "success", log_msg)]
    }


# --- ROUTERS ---

def route_executor(state: AgentState) -> Literal["executor", "synthesizer"]:
    # Loop over execution steps until all tasks in the plan are completed
    if state["current_step_index"] < len(state["plan"]):
        return "executor"
    return "synthesizer"

def route_critique(state: AgentState) -> Literal["done", "prepare_rewrite", "human_approval"]:
    # Auto-approve if score is 8.0+ or we reached the loop guard safety limit
    if state["score"] >= 8.0 or state.get("tries", 0) >= 3:
        return "done"
    # Auto-rewrite if score is really low
    elif state["score"] < 6.0:
        return "prepare_rewrite"
    # Pauses for Human-in-the-Loop review if borderline (6.0 to 8.0)
    else:
        return "human_approval"

def route_human(state: AgentState) -> Literal["done", "prepare_rewrite"]:
    # Decides routing based on the human review input after resuming
    if state.get("approved"):
        return "done"
    return "prepare_rewrite"


# --- BUILD GRAPH ---

def create_agent_graph():
    builder = StateGraph(AgentState)
    
    # Register Nodes
    builder.add_node("planner", node_planner)
    builder.add_node("executor", node_executor)
    builder.add_node("synthesizer", node_synthesizer)
    builder.add_node("critique", node_critique)
    builder.add_node("prepare_rewrite", node_prepare_rewrite)
    builder.add_node("human_approval", node_human_approval)
    builder.add_node("done", node_done)
    
    # Wire Edges
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "executor")
    
    # Conditional edge after executor: loops through steps or goes to synthesis
    builder.add_conditional_edges(
        "executor",
        route_executor,
        {
            "executor": "executor",
            "synthesizer": "synthesizer"
        }
    )
    
    builder.add_edge("synthesizer", "critique")
    
    # Conditional edge after critique: done, auto-rewrite, or pause for human approval
    builder.add_conditional_edges(
        "critique",
        route_critique,
        {
            "done": "done",
            "prepare_rewrite": "prepare_rewrite",
            "human_approval": "human_approval"
        }
    )
    
    builder.add_edge("prepare_rewrite", "executor")
    
    # Conditional edge after human approval (post-resume)
    builder.add_conditional_edges(
        "human_approval",
        route_human,
        {
            "done": "done",
            "prepare_rewrite": "prepare_rewrite"
        }
    )
    
    builder.add_edge("done", END)
    
    # Persistence checkpointer
    memory = InMemorySaver()
    
    # Compile the graph with interrupt BEFORE entering the human_approval node
    # This automatically pauses and saves graph state when transitioning to human_approval
    graph = builder.compile(
        checkpointer=memory,
        interrupt_before=["human_approval"]
    )
    
    return graph

# Expose compiled graph instance
compiled_graph = create_agent_graph()

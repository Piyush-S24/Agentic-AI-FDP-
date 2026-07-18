from typing import TypedDict, List, Dict, Any
from pydantic import BaseModel, Field

# Pydantic model for Structured Output of the Planner node
class Plan(BaseModel):
    steps: List[str] = Field(description="Ordered list of 2-4 sub-tasks/sections to write to answer the query")

# Pydantic model for Structured Output of the Critique node
class Critique(BaseModel):
    score: float = Field(description="Score between 0.0 and 10.0 indicating draft quality. 8.0+ is excellent.")
    feedback: str = Field(description="Detailed critique of what is missing, what is good, and how to improve it.")

# The LangGraph Shared State (TypedDict) passed between nodes
class AgentState(TypedDict):
    question: str
    plan: List[str]
    current_step_index: int
    worker_results: List[str]
    draft: str
    critique: str
    score: float
    tries: int
    approved: bool
    awaiting_approval: bool
    human_feedback: str
    logs: List[Dict[str, Any]]  # Log of step events to show on the UI

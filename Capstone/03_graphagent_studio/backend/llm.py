import os
import random
import time
from typing import Type, TypeVar, Any, Optional
from pydantic import BaseModel
from langchain_core.messages import AIMessage
from backend.models import Plan, Critique

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)

class MockStructuredLLM:
    """Simulates an LLM with structured output capability."""
    def __init__(self, output_schema: Type[BaseModel]):
        self.output_schema = output_schema

    def invoke(self, prompt: str) -> BaseModel:
        # Simulate minor delay
        time.sleep(1.0)
        prompt_lower = prompt.lower()
        
        if self.output_schema == Plan:
            # Generate plans based on common topics
            if "ludhiana" in prompt_lower or "amritsar" in prompt_lower:
                return Plan(steps=[
                    "Find population of Ludhiana, Punjab",
                    "Find population of Amritsar, Punjab",
                    "Compare the two populations and determine which is larger"
                ])
            elif "langgraph" in prompt_lower or "state machine" in prompt_lower:
                return Plan(steps=[
                    "Explain what LangGraph is and its core philosophy",
                    "List and define the four main pillars: State, Nodes, Edges, and Checkpointers",
                    "Summarize the main benefit of graphs over simple chains"
                ])
            elif "dharamshala" in prompt_lower or "lpu" in prompt_lower:
                return Plan(steps=[
                    "Calculate the walking distance from LPU (Jalandhar) to Dharamshala",
                    "Determine walking time based on average walking speed and breaks",
                    "Outline the major landmarks/cities along the walking route"
                ])
            else:
                # Default fallback plan for any generic query
                return Plan(steps=[
                    f"Research the core aspects of: {prompt[:40]}...",
                    "Draft a detailed summary highlighting key findings",
                    "Provide a critical comparison or concluding analysis"
                ])
                
        elif self.output_schema == Critique:
            # Simulate cyclic correction by checking if it's already been tried
            # We look for indications in the prompt of a previous draft or tries
            is_revision = "improve" in prompt_lower or "revised" in prompt_lower or "previous" in prompt_lower
            
            if is_revision:
                # High score on rewrite to exit loop
                return Critique(
                    score=8.7,
                    feedback="Much better! The draft is now highly detailed, well-structured, and easy to understand."
                )
            else:
                # Give a mediocre score on first pass to trigger conditional edge routing
                # Let's target a score around 6.5 to trigger human-in-the-loop review
                return Critique(
                    score=6.5,
                    feedback="The response is accurate, but it feels a bit plain and lacks formatting. It should use bullet points for the key details."
                )
                
        # Default fallback
        return self.output_schema()

class MockLLM:
    """Simulates standard LLM invocation responses."""
    def invoke(self, prompt: str) -> AIMessage:
        time.sleep(1.0)
        prompt_lower = prompt.lower()
        
        # Worker simulations
        if "population of ludhiana" in prompt_lower:
            content = "Ludhiana is the largest city in the Indian state of Punjab, with an estimated metropolitan population of 1,618,879 as per census records. It is a major industrial hub."
        elif "population of amritsar" in prompt_lower:
            content = "Amritsar is the second-largest city in Punjab, with a population of approximately 1,132,383. It is home to the Golden Temple, the spiritual center of Sikhism."
        elif "compare the two populations" in prompt_lower or "determine which is larger" in prompt_lower:
            content = "Comparing the populations: Ludhiana (1.61M) is larger than Amritsar (1.13M) by approximately 486,000 people. Ludhiana is the more populous metropolitan region."
            
        elif "explain what langgraph is" in prompt_lower:
            content = "LangGraph is a library developed by LangChain designed to build stateful, multi-actor applications with LLMs. Unlike simple linear chains, it allows you to define loops, cyclic steps, and complex branching routing using a graph-based mental model (nodes and edges)."
        elif "define the four main pillars" in prompt_lower:
            content = "The four pillars of LangGraph are: 1. State (shared memory passed between steps), 2. Nodes (functions executing specific logic), 3. Edges (conditional or fixed paths directing state flow), and 4. Checkpointers (persistent memory slots allowing runs to pause and resume)."
        elif "benefit of graphs over simple chains" in prompt_lower:
            content = "While chains are linear and rigid (A -> B -> C), graphs allow cycles (loops back to A for correction or editing) and human-in-the-loop steps. This supports complex agent flows like agentic coding, writing loops, and multi-agent systems."
            
        elif "lpu (jalandhar) to dharamshala" in prompt_lower or "walking distance" in prompt_lower:
            content = "The road distance from Lovely Professional University (LPU) near Jalandhar/Phagwara to Dharamshala is approximately 150 km (93 miles) routing through Hoshiarpur."
        elif "average walking speed" in prompt_lower or "walking time" in prompt_lower:
            content = "Walking at an average human speed of 5 km/h, covering 150 km continuously takes 30 hours. Assuming 8 hours of walking per day, it translates to roughly 3.75 to 4 days."
        elif "landmarks" in prompt_lower or "walking route" in prompt_lower:
            content = "The walking route runs from Jalandhar -> Hoshiarpur -> Gagret -> Ranital -> Kangra -> Dharamshala. It shifts from flat plains to hilly climbs once you pass Gagret."

        # Final synthesis simulations
        elif "using these sub-results" in prompt_lower:
            if "ludhiana" in prompt_lower:
                content = (
                    "### Population Comparison: Ludhiana vs. Amritsar\n\n"
                    "Based on demographic statistics:\n"
                    "- **Ludhiana**: ~1,618,879 residents (major industrial center)\n"
                    "- **Amritsar**: ~1,132,383 residents (spiritual center)\n\n"
                    "**Conclusion**: **Ludhiana is larger** than Amritsar by roughly 486,000 people."
                )
            elif "langgraph" in prompt_lower:
                content = (
                    "### Introduction to LangGraph\n\n"
                    "LangGraph is a framework for creating stateful, multi-agent systems. Its key components include:\n"
                    "1. **State**: The shared memory.\n"
                    "2. **Nodes**: Discrete execution steps.\n"
                    "3. **Edges**: Routing rules.\n"
                    "4. **Checkpointers**: Pause/resume mechanisms.\n\n"
                    "Graphs are superior to chains for non-linear, cyclic, and human-in-the-loop operations."
                )
            elif "dharamshala" in prompt_lower or "lpu" in prompt_lower:
                content = (
                    "### Walking from LPU to Dharamshala\n\n"
                    "If you are walking from **Lovely Professional University (LPU)** to **Dharamshala**, here is your complete journey breakdown:\n\n"
                    "- **Total Distance**: Approximately **150 km (93 miles)** via Hoshiarpur and NH503.\n"
                    "- **Walking Time**: Approximately **30 hours of net walking** at 5 km/h.\n"
                    "- **Total Duration**: **4 days** if walking a healthy pace of **8 hours per day** with standard rests.\n\n"
                    "#### Recommended Route & Pitstops\n"
                    "1. **Jalandhar/LPU to Hoshiarpur** (~45 km): Flat terrain, high road traffic. Good for day 1.\n"
                    "2. **Hoshiarpur to Gagret** (~30 km): Incline starts, crossing the Punjab-Himachal border. Good for day 2.\n"
                    "3. **Gagret to Kangra** (~55 km): Hill roads, very scenic, moderate slopes. Good for day 3.\n"
                    "4. **Kangra to Dharamshala** (~20 km): High incline uphill climb. Good for day 4.\n\n"
                    "*Ensure you pack lightweight gear, carry rain protection, and stay well hydrated.*"
                )
            else:
                content = "### Final Synthesized Answer\n\nHere is the synthesized response combining all execution workers."
                
        # Fallbacks
        elif "improve this answer" in prompt_lower:
            content = (
                "### Enhanced Analysis & Response\n\n"
                "Here is the revised draft incorporating feedback:\n"
                "- **Detailed Overview**: Provides a thorough breakdown of the topic.\n"
                "- **Structured Breakdown**: Explains each sub-task systematically.\n"
                "- **Final Assessment**: Synthesizes the information concisely for better reading."
            )
        else:
            content = f"Draft answer generated for the task: '{prompt[:60]}...'. It contains structured insights and concise wording."
            
        return AIMessage(content=content)

    def with_structured_output(self, output_schema: Type[BaseModel]) -> MockStructuredLLM:
        return MockStructuredLLM(output_schema)


def get_llm(api_key: Optional[str] = None):
    """
    Returns an LLM instance. If API_KEY is provided or exists in the environment,
    returns ChatGroq. Otherwise, falls back to the MockLLM simulation.
    """
    effective_key = api_key or os.environ.get("GROQ_API_KEY")
    
    if effective_key and not effective_key.startswith("gsk_your_key"):
        try:
            from langchain_groq import ChatGroq
            # We use llama-3.3-70b-versatile or a fallback model
            return ChatGroq(
                model="llama-3.3-70b-versatile",
                api_key=effective_key,
                temperature=0,
            )
        except Exception as e:
            print(f"⚠️ Error initializing ChatGroq: {e}. Falling back to simulation.")
            return MockLLM()
    else:
        print("ℹ️ No Groq API Key found. Operating in Simulation Mode.")
        return MockLLM()

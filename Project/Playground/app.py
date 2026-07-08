import os
import uuid
from typing import Optional
from dotenv import load_dotenv

# Load environment variables at the very beginning
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agent import compiled_graph

app = FastAPI(title="GraphAgent Studio API")

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Request Schemas
class RunRequest(BaseModel):
    question: str
    api_key: Optional[str] = None
    thread_id: Optional[str] = None

class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool
    feedback: str
    api_key: Optional[str] = None

# API Endpoints

@app.post("/api/run")
async def run_agent(req: RunRequest):
    try:
        thread_id = req.thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id, "api_key": req.api_key}}
        
        # Initial state setup
        initial_state = {
            "question": req.question,
            "plan": [],
            "current_step_index": 0,
            "worker_results": [],
            "draft": "",
            "critique": "",
            "score": 0.0,
            "tries": 0,
            "approved": False,
            "awaiting_approval": False,
            "logs": [],
            "human_feedback": ""
        }
        
        # Invoke graph. It will run until completion or pause before human_approval
        result = compiled_graph.invoke(initial_state, config)
        
        # Inspect state after run
        state_info = compiled_graph.get_state(config)
        is_paused = "human_approval" in state_info.next
        
        # Update awaiting_approval flag in state if paused
        if is_paused:
            compiled_graph.update_state(config, {"awaiting_approval": True})
            # Re-read state
            state_info = compiled_graph.get_state(config)
            
        return {
            "thread_id": thread_id,
            "state": state_info.values,
            "next_node": list(state_info.next),
            "is_paused": is_paused
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/resume")
async def resume_agent(req: ResumeRequest):
    try:
        config = {"configurable": {"thread_id": req.thread_id, "api_key": req.api_key}}
        
        # Fetch current state to ensure thread exists
        state_info = compiled_graph.get_state(config)
        if not state_info or not state_info.values:
            raise HTTPException(status_code=404, detail="Thread not found")
            
        # Update state with human decisions
        # We explicitly set approved and feedback, and turn off awaiting_approval
        compiled_graph.update_state(
            config, 
            {
                "approved": req.approved, 
                "human_feedback": req.feedback,
                "awaiting_approval": False
            }
        )
        
        # Resume graph execution (invoking with None resumes from where it stopped)
        compiled_graph.invoke(None, config)
        
        # Fetch updated state
        updated_state_info = compiled_graph.get_state(config)
        is_paused = "human_approval" in updated_state_info.next
        
        # If it paused again (e.g. another critique threshold check), set flag
        if is_paused:
            compiled_graph.update_state(config, {"awaiting_approval": True})
            updated_state_info = compiled_graph.get_state(config)
            
        return {
            "thread_id": req.thread_id,
            "state": updated_state_info.values,
            "next_node": list(updated_state_info.next),
            "is_paused": is_paused
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/state/{thread_id}")
async def get_state(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    state_info = compiled_graph.get_state(config)
    
    if not state_info or not state_info.values:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    return {
        "thread_id": thread_id,
        "state": state_info.values,
        "next_node": list(state_info.next),
        "is_paused": "human_approval" in state_info.next
    }

@app.get("/api/history/{thread_id}")
async def get_history(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    history_events = []
    
    try:
        # Fetch historical transitions of state
        for state in compiled_graph.get_state_history(config):
            # Format history event
            history_events.append({
                "values": state.values,
                "next": list(state.next),
                "created_at": state.metadata.get("created_at") if state.metadata else None,
                "step": state.metadata.get("step") if state.metadata else None
            })
        
        return {"thread_id": thread_id, "history": history_events}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mount frontend files
# Ensure the directory exists
os.makedirs("frontend", exist_ok=True)

# Serve static files from the 'frontend' folder
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")

# Run FastAPI server
if __name__ == "__main__":
    import uvicorn
    import sys
    
    # Ensure current directory is in python path for uvicorn
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
        
    # Load dotenv if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
        
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 GraphAgent Studio starting on http://127.0.0.1:{port}")
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)

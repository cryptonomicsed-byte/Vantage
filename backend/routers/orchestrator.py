"""Orchestrator API router — integrates the multi-agent coordination layer into Vantage."""
import json, os, sys
sys.path.insert(0, "/opt/ares")

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.deps import get_agent
from ares_orchestrator import Orchestrator

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])
orch = Orchestrator()

class DebateRequest(BaseModel):
    topic: str
    agent_names: Optional[list[str]] = None

class PipelineRequest(BaseModel):
    name: str
    topic: str
    goal: str
    context: str = ""
    steps: list[dict] = []

@router.post("/debate")
async def run_debate(req: DebateRequest, agent: dict = Depends(get_agent)):
    result = orch.debate_and_decide(req.topic)
    return result

@router.post("/pipeline")
async def run_pipeline(req: PipelineRequest, agent: dict = Depends(get_agent)):
    result = orch.run_pipeline({
        "name": req.name,
        "topic": req.topic,
        "goal": req.goal,
        "context": req.context,
        "steps": req.steps,
    })
    return result

@router.get("/status")
async def orchestrator_status(agent: dict = Depends(get_agent)):
    return {
        "orchestrator": "v1",
        "components": ["inference_router", "stratified_engine", "parliament", "event_bus", "koodu_gate", "rag_retriever"],
        "strategies": ["direct", "chain_of_thought", "chain_of_verification", "self_consistency"],
        "agents_available": ["TechnoMancer", "ChainWatch", "SentimentBot", "RiskGuard"],
    }

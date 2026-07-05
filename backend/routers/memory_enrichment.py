"""
Memory enrichment endpoints — bridge between Vantage agents and the Julia memory service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config import settings
from backend.deps import get_agent
from backend.memory_enrichment import MemoryIntelligence

router = APIRouter(prefix="/api/agents/{name}/memory", tags=["memory-enrichment"])


def _intel() -> MemoryIntelligence:
    return MemoryIntelligence(settings.JULIA_MEMORY_URL)


class SimilarRequest(BaseModel):
    content: str
    top_k: int = 5


class ValidateRequest(BaseModel):
    trace_data: str


@router.get("/predict")
async def predict_next_activity(name: str) -> dict:
    return await _intel().predict_next_activity(name)


@router.post("/similar")
async def find_similar(name: str, body: SimilarRequest, agent: dict = Depends(get_agent)) -> list:
    return await _intel().find_similar(body.content, body.top_k)


@router.get("/patterns")
async def mine_patterns(name: str) -> list:
    return await _intel().mine_patterns(f"agent:{name}")


@router.post("/validate")
async def validate_entropy(name: str, body: ValidateRequest, agent: dict = Depends(get_agent)) -> dict:
    return await _intel().validate_trace_entropy(body.trace_data)

"""
Shared Pydantic schema for NYC First Responder Dispatch.
This is the single source of truth for the API contract.
All three tracks (C++/Systems, ML/Python, Frontend) code against this schema.
"""

from typing import Optional
from pydantic import BaseModel, Field


class SimilarIncident(BaseModel):
    id: str
    complaint_type: str
    borough: str
    date: str = ""
    resolution_days: float = -1.0
    source: str = ""


class TriageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="Incident description text")
    image_b64: Optional[str] = Field(None, max_length=10_000_000, description="Base64-encoded image for vision analysis")
    borough: Optional[str] = Field(None, description="NYC borough: Manhattan, Brooklyn, Queens, Bronx, Staten Island, or All")


class TriageResponse(BaseModel):
    category: str = Field(..., description="Specific incident type, e.g. Structure Fire, Medical Emergency")
    severity: int = Field(..., ge=1, le=5, description="1=Low, 2=Moderate, 3=Urgent, 4=Critical, 5=Life-Threatening")
    agency: str = Field(..., description="NYPD | FDNY | EMS | Sanitation | Buildings | Housing | Multi")
    summary: str = Field(..., description="Plain-English brief for first responder, 2-3 sentences")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score")
    vision_context: Optional[str] = Field(None, description="LLaVA scene description if image provided")
    similar_incidents: list[SimilarIncident] = Field(default_factory=list, description="Top 3 similar past NYC incidents")
    inference_ms: int = Field(0, description="LLM inference time in milliseconds")
    search_ms: int = Field(0, description="ChromaDB + DuckDB search time in milliseconds")
    total_ms: int = Field(0, description="Total pipeline latency in milliseconds")


class HealthResponse(BaseModel):
    status: str
    models: dict[str, bool]
    memory: dict[str, float]
    gpu: str = "unknown"
    demo_mode: bool = False

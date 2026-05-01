from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from datetime import datetime
import uuid

class PredictionTelemetry(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model_version: str
    segment: str
    features: Dict[str, Any]
    prediction_score: float
    prediction_class: int
    latency_ms: float

class GroundTruth(BaseModel):
    request_id: str
    timestamp_arrived: datetime = Field(default_factory=datetime.utcnow)
    actual_label: int

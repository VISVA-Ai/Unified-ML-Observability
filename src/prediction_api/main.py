import time
import random
import json
import redis

from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

from shared.models import PredictionTelemetry
from shared.kafka_client import get_kafka_producer, KAFKA_BROKER_DOCKER, produce_json

app = FastAPI(
    title="Prediction Gateway",
    description="Fraud detection inference API with integrated telemetry emission.",
    version="1.0.0",
)

producer = get_kafka_producer(KAFKA_BROKER_DOCKER)

# Redis client — used to queue pending predictions for the Label Simulator.
# The label_simulator pops from this list to simulate delayed ground truth.
redis_client = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)


class FraudRequest(BaseModel):
    user_id: str
    amount: float
    merchant_category: str
    country: str
    is_weekend: bool
    device_type: str


def _emit_telemetry(telemetry: PredictionTelemetry):
    """Produce telemetry to Kafka and push to the pending-labels Redis queue."""
    produce_json(
        producer=producer,
        topic="ml-telemetry",
        key=telemetry.request_id,
        value=telemetry.model_dump(mode="json"),
    )
    # Queue this prediction for the label simulator so ground truth labels
    # will map back to real request_ids instead of random UUIDs.
    redis_client.rpush(
        "pending_labels",
        json.dumps(
            {
                "request_id": telemetry.request_id,
                "prediction_score": telemetry.prediction_score,
            }
        ),
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict")
async def predict(req: FraudRequest, background_tasks: BackgroundTasks):
    start_time = time.time()

    # ------------------------------------------------------------------
    # Simulated XGBoost model — rule-based scoring for demonstration.
    # Real deployments would load a serialised model from a Model Registry.
    # ------------------------------------------------------------------
    risk_score = 0.05

    if req.amount > 500:
        risk_score += 0.30
    if req.country in ["NG", "RU", "KP"]:
        risk_score += 0.40
    if req.is_weekend and req.amount > 200:
        risk_score += 0.20
    if req.device_type == "unknown":
        risk_score += 0.15

    risk_score += random.uniform(-0.05, 0.05)
    risk_score = max(0.0, min(1.0, risk_score))

    prediction_class = 1 if risk_score > 0.6 else 0

    # Simulate realistic inference latency (10–60 ms)
    time.sleep(random.uniform(0.01, 0.06))
    latency_ms = (time.time() - start_time) * 1000.0

    telemetry = PredictionTelemetry(
        model_version="xgboost-fraud-v1.0",
        segment=req.country,
        features={
            "amount": float(req.amount),
            "is_weekend": float(req.is_weekend),
            "merchant_category": req.merchant_category,
            "country": req.country,
            "device_type": req.device_type,
        },
        prediction_score=risk_score,
        prediction_class=prediction_class,
        latency_ms=latency_ms,
    )

    # Non-blocking: emit telemetry + queue label in the background
    background_tasks.add_task(_emit_telemetry, telemetry)

    return {
        "request_id": telemetry.request_id,
        "fraud_probability": round(risk_score, 4),
        "is_fraud": bool(prediction_class),
        "latency_ms": round(latency_ms, 2),
    }

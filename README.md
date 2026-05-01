# Unified ML Observability Platform

An enterprise-grade ML Observability, Drift Detection, and Alerting system built on a production-style microservices architecture.

This project demonstrates a complete end-to-end observability pipeline for a production fraud detection model — from real-time inference telemetry, through statistical drift detection, to a live monitoring dashboard.

---

## Architecture Overview

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                         Client Traffic                           │
 └──────────────────────┬───────────────────────────────────────────┘
                        │  POST /predict
                        ▼
              ┌─────────────────────┐
              │   Prediction API    │  FastAPI · port 8000
              │  (xgboost-fraud)    │
              └────────┬────────────┘
                       │  Kafka: ml-telemetry        Redis: pending_labels
                       ▼                                     ▼
              ┌─────────────────────┐          ┌─────────────────────┐
              │  Ingestion Service  │          │   Label Simulator   │
              │  (Kafka Consumer)   │          │ (Delayed GndTruth)  │
              └────────┬────────────┘          └────────┬────────────┘
                       │                               │ Kafka: ml-labels
                       ▼                               ▼
              ┌─────────────────────────────────────────────┐
              │              ClickHouse (OLAP)              │
              │   predictions <--- INNER JOIN ---> labels   │
              └────────────────────┬────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      Drift Worker (Celery)  │
                    │  * Service Health (15s)     │
                    │  * Concept Drift (30s)      │
                    │  * PSI Data Drift (30s)     │
                    └──────────────┬──────────────┘
                                   │  Alerts
                                   ▼
                    ┌──────────────────────────────┐
                    │   PostgreSQL (Control Plane) │
                    │   alerts · model_thresholds  │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       Control API           │  FastAPI · port 8001
                    │  /health  /alerts           │
                    │  /metrics/performance       │
                    │  /metrics/drift             │
                    │  /thresholds/{model}        │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     Streamlit Dashboard     │  port 8501
                    │  KPIs · Charts · Incidents  │
                    └──────────────────────────────┘
```

## Key Features

| Feature | Implementation |
|---|---|
| **Real-time telemetry** | Every inference emits a JSON event to Kafka (Redpanda) |
| **Delayed ground truth** | Label Simulator uses Redis to match labels back to real prediction IDs |
| **PSI Drift Detection** | Population Stability Index calculated every 30s against a rolling baseline |
| **Concept Drift Detection** | Rolling average risk score monitored for distribution shift |
| **Structured Alerting** | `LATENCY_SPIKE`, `CONCEPT_DRIFT`, `DATA_DRIFT` alerts written to Postgres |
| **Performance Metrics API** | Accuracy computed by joining predictions to labels on `request_id` |
| **Live Dashboard** | Streamlit with KPI cards, latency charts, risk histogram, incident table |

## Tech Stack

| Layer | Technology |
|---|---|
| Inference API | Python, FastAPI, Uvicorn |
| Streaming | Redpanda (Kafka-compatible) |
| OLAP Storage | ClickHouse |
| Metadata / Alerts | PostgreSQL |
| Cache / Task Broker | Redis |
| Drift Detection | Celery Beat, NumPy |
| Dashboard | Streamlit, Plotly |
| Containerisation | Docker, Docker Compose |

---

## Getting Started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with the Docker Engine running)

### 1. Start all services

```bash
docker-compose up --build
```

Wait for all containers to reach a healthy state (~60 seconds on first build).

### 2. Generate traffic

In a separate terminal:

```bash
pip install requests
python scripts/load_gen.py
```

This sends **20 normal requests** followed by **20 high-risk (drift) requests** to the Prediction API.

### 3. Open the dashboard

Navigate to [http://localhost:8501](http://localhost:8501) in your browser.

Hit **Refresh Dashboard** after ~30 seconds to see:
- KPI cards populate with prediction counts and accuracy.
- Risk score histogram split by fraud vs. legitimate traffic.
- `CONCEPT_DRIFT` and `DATA_DRIFT` alerts appear in the incidents table.

### 4. Explore the APIs

| Service | URL |
|---|---|
| Prediction API (Swagger) | [http://localhost:8000/docs](http://localhost:8000/docs) |
| Control API (Swagger) | [http://localhost:8001/docs](http://localhost:8001/docs) |

---

## Project Structure

```text
.
├── docker-compose.yml
├── requirements.txt
├── scripts/
│   └── load_gen.py              # Traffic load generator (normal + drift)
└── src/
    ├── shared/                  # Shared DB clients and Pydantic models
    │   ├── models.py
    │   ├── kafka_client.py
    │   ├── db_clickhouse.py
    │   └── db_postgres.py
    ├── prediction_api/          # FastAPI inference gateway
    │   ├── main.py
    │   ├── label_simulator.py   # Simulates delayed ground truth via Redis
    │   └── requirements.txt     
    ├── ingestion_service/       # Kafka consumer -> ClickHouse writer
    │   ├── main.py
    │   └── requirements.txt
    ├── drift_worker/            # Celery Beat drift detection tasks (PSI)
    │   ├── tasks.py
    │   └── requirements.txt
    ├── control_api/             # FastAPI control plane (metrics, alerts, config)
    │   ├── main.py
    │   └── requirements.txt
    └── dashboard/               # Streamlit monitoring UI
        ├── app.py
        └── requirements.txt
```

---

## Drift Detection Strategy

The `drift_worker` runs three scheduled Celery tasks:

### 1. Service Health (every 15s)
Checks average latency over the last minute. Fires a `LATENCY_SPIKE / WARNING` alert if latency exceeds 50ms.

### 2. Concept Drift (every 30s)
Monitors the rolling average prediction score. A sustained spike above 0.30 (baseline ~0.10) indicates the model is scoring a materially different input population — fires `CONCEPT_DRIFT`.

### 3. PSI Data Drift (every 30s)
Computes Population Stability Index (PSI) between a 60-minute baseline window and the current 2-minute window:

| PSI Value | Interpretation |
|---|---|
| < 0.10 | No significant change |
| 0.10 – 0.20 | Moderate shift -> WARNING |
| > 0.20 | Significant shift -> CRITICAL |

---

## Ground Truth Pipeline

A key challenge in fraud detection is that labels arrive days to weeks after the prediction (e.g., chargebacks). This system simulates that via:

1. `prediction_api` pushes `{request_id, prediction_score}` to a Redis list after every inference.
2. `label_simulator` pops from that list, waits 3–15 seconds (simulating real-world delay), then emits a correlated ground truth label to the `ml-labels` Kafka topic.
3. `ingestion_service` consumes both topics and writes to ClickHouse.
4. `control_api` joins predictions and labels on `request_id` to compute accuracy metrics.

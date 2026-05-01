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

---

## Detailed Technical Explanation

This section is a deep-dive into the engineering decisions, data flows, and internal mechanics of each service. It is intended for anyone who wants to understand the project beyond the surface level — whether that is a recruiter, a collaborator, or a future maintainer.

---

### Problem Statement

Modern machine learning teams face a unique operational challenge: a model that scored 98% accuracy during training can silently degrade in production for days or weeks before anyone notices. This happens because:

- **Data drift**: the statistical distribution of incoming features shifts over time (e.g., new fraud patterns, seasonal changes in transaction amounts).
- **Concept drift**: the relationship between input features and the correct output changes (e.g., fraudsters adopt new tactics that the original training data did not capture).
- **Delayed ground truth**: in fraud detection, a label (fraudulent or legitimate) is only confirmed days to weeks later via a chargeback. There is no immediate feedback loop.

This project builds a complete, production-style observability system that monitors for all three failure modes in real time.

---

### Service-by-Service Breakdown

#### 1. Prediction API (`src/prediction_api/`)

The entry point for all inference traffic. Built with **FastAPI** and served by **Uvicorn**.

When a request arrives at `POST /predict`, the service:
1. Constructs a feature vector from the request payload (amount, country, device type, merchant category, etc.).
2. Runs the feature vector through a lightweight XGBoost-style scoring function. In production, this would be replaced with an `mlflow.pyfunc.load_model()` call pointing to a registered model artifact.
3. Returns `{ request_id, fraud_probability, is_fraud, latency_ms }` to the caller.
4. In a background task (non-blocking, using FastAPI's `BackgroundTasks`), it simultaneously emits a full telemetry event to the `ml-telemetry` Kafka topic and pushes the `request_id` to a **Redis list** (`pending_labels`) for the label simulator.

The design ensures that HTTP response latency is never inflated by Kafka or Redis write times.

#### 2. Label Simulator (`src/prediction_api/label_simulator.py`)

This service solves the delayed ground truth problem. It runs as a background thread inside the `prediction_api` container and works via a blocking `BLPOP` call on the Redis `pending_labels` list.

The moment a new `request_id` arrives, the simulator:
1. Pops the item from Redis.
2. Waits a random delay between 3 and 15 seconds, simulating the real-world lag of chargeback confirmation.
3. Generates a ground truth label based on a probabilistic rule applied to the original prediction score.
4. Emits a `{ request_id, ground_truth_label }` event to the `ml-labels` Kafka topic.

This design is critical because it ensures labels are always correlated back to real prediction `request_id`s. Naive simulators that generate random UUIDs produce labels that can never be joined back to predictions, making accuracy computation impossible.

#### 3. Ingestion Service (`src/ingestion_service/`)

A pure **Kafka consumer** written as a long-running Python process. It subscribes to both `ml-telemetry` and `ml-labels` topics and writes records into **ClickHouse**.

Key design decisions:
- Commits Kafka offsets only after a successful ClickHouse write, guaranteeing at-least-once delivery semantics. If the service restarts mid-write, no data is silently lost.
- Initialises all database schemas on startup using `IF NOT EXISTS`, so the service can restart safely without manual intervention or migration scripts.

#### 4. Drift Worker (`src/drift_worker/`)

The core analytics engine. Built on **Celery Beat**, a distributed task scheduler that runs periodic background jobs. It runs three distinct tasks:

**Task A: Service Health Check (every 15 seconds)**
Queries ClickHouse for the average inference latency over the last 60 seconds. If the average exceeds 50ms, it writes a `LATENCY_SPIKE / WARNING` alert to PostgreSQL. This is analogous to an SLO (Service Level Objective) violation detector.

**Task B: Concept Drift Detection (every 30 seconds)**
Queries ClickHouse for the mean prediction score over the last 2 minutes and compares it to a known stable baseline (approximately 0.10 for a healthy low-risk population). A sustained deviation above 0.30 indicates the model is encountering a materially different input population. This fires a `CONCEPT_DRIFT / CRITICAL` alert.

Concept drift is the most dangerous failure mode because it often means the model's decision boundary is no longer valid — not just that inputs changed, but that what constitutes fraud itself has changed.

**Task C: PSI Data Drift Detection (every 30 seconds)**
Implements a full **Population Stability Index (PSI)** calculation using NumPy. PSI measures how much a distribution has shifted between a reference window (the last 60 minutes) and a current window (the last 2 minutes).

The PSI formula is:

```
PSI = sum( (Actual% - Expected%) * ln(Actual% / Expected%) )
```

This is computed across 10 equal-width bins spanning the 0–1 prediction score range. The result is a single scalar that quantifies the magnitude of distribution shift:

| PSI Range | Interpretation |
|---|---|
| < 0.10 | No significant change. Model is stable. |
| 0.10 – 0.20 | Moderate shift. Investigate upstream feature pipelines. |
| > 0.20 | Significant shift. Model retraining is likely required. |

Alerts include the exact PSI score, giving engineers a quantified measure of severity rather than a binary alarm.

#### 5. Control API (`src/control_api/`)

A **FastAPI** service that acts as the single source of truth for all dashboard data. The dashboard never queries ClickHouse or PostgreSQL directly — it always goes through the Control API.

This separation of concerns means rate limiting, caching, and access control can be added to the Control API without touching the dashboard, and the API can be exposed to external consumers (e.g., a PagerDuty webhook, a Grafana data source plugin) independently.

Key endpoints:

| Endpoint | Description |
|---|---|
| `GET /health` | Returns health status of all downstream services. |
| `GET /alerts?limit=N` | Returns the N most recent active alerts from PostgreSQL. |
| `GET /metrics/performance` | Returns prediction count, label count, accuracy, and average latency for the last hour. Accuracy is computed via a SQL `INNER JOIN` on `request_id` between the predictions and labels tables. |
| `GET /metrics/drift` | Returns the most recent PSI score and drift severity. |
| `GET /thresholds/{model_version}` | Returns configured alert thresholds for a given model version from the `model_thresholds` table. |

#### 6. Dashboard (`src/dashboard/`)

Built with **Streamlit** and **Plotly**. The dashboard is a stateless UI layer that fetches all data from the Control API on each render cycle.

Key UI components:
- **KPI Cards**: Four metric cards showing total predictions, labels received with coverage percentage, model accuracy, and average latency with a spike indicator.
- **Latency Scatter Plot**: Every individual inference request plotted as a point, coloured by prediction class. Latency outliers and class-level patterns are immediately visible.
- **Risk Score Histogram**: The full distribution of `fraud_probability` scores overlaid by class, with a vertical dashed line marking the decision threshold at 0.6. A bimodal distribution (two separate humps) is the signature of a healthy model. A distribution that collapses into a single hump is a strong visual indicator of drift.
- **Prediction Volume Bar Chart**: One-minute time buckets stacked by class, showing the rate and composition of traffic over time.
- **Incidents Table**: All active alerts from the Control API, colour-coded by severity, with timestamps, drift scores, and model versions attached.

---

### Data Flow Summary

The complete lifecycle of a single transaction through the system:

```
1.  Client sends POST /predict to Prediction API
2.  Prediction API scores the transaction and responds (< 50ms)
3.  Background task emits telemetry event to Kafka (ml-telemetry)
4.  Background task pushes request_id to Redis (pending_labels)
5.  Ingestion Service consumes ml-telemetry, writes row to ClickHouse (predictions table)
6.  Label Simulator pops request_id from Redis, waits 3–15s
7.  Label Simulator emits ground truth to Kafka (ml-labels)
8.  Ingestion Service consumes ml-labels, writes row to ClickHouse (labels table)
9.  Drift Worker (every 30s) queries ClickHouse, computes PSI and concept drift scores
10. Drift Worker writes alerts to PostgreSQL if thresholds are breached
11. Dashboard fetches from Control API, which JOINs ClickHouse + reads PostgreSQL
12. User sees live KPIs, charts, and incidents in the browser
```

---

### Why These Technology Choices?

| Technology | Rationale |
|---|---|
| **Redpanda (Kafka-compatible)** | Decouples the inference path from storage writes. If ClickHouse is slow or temporarily unavailable, predictions keep serving without data loss. Redpanda was chosen over standard Kafka because it requires no ZooKeeper and has a significantly smaller Docker footprint. |
| **ClickHouse** | An OLAP (Online Analytical Processing) database optimised for fast aggregation queries over large datasets. Querying the average latency of the last 10,000 rows takes microseconds. A standard PostgreSQL instance would degrade significantly at this scale. |
| **PostgreSQL** | Used for the control plane (alerts, thresholds) because this data is transactional and relational. It benefits from ACID guarantees and row-level locking that ClickHouse does not provide. |
| **Redis** | A lightweight, in-memory queue for the label correlation pipeline. A `LPUSH` / `BLPOP` pair is faster and simpler than adding a dedicated message queue for this single use case. |
| **Celery Beat** | A battle-tested Python task scheduler with native Redis broker support. Provides clear separation between task definition and scheduling configuration, with built-in support for distributed workers if horizontal scaling is needed. |
| **FastAPI** | Chosen over Flask for its native async support, automatic OpenAPI documentation generation, and Pydantic-based request validation. The auto-generated `/docs` page is immediately useful for debugging and API exploration. |

---

### Future Improvements

This project is structured to be extended. Logical next steps include:

- **Real model integration**: Replace the scoring function in `prediction_api` with an `mlflow.pyfunc.load_model()` call to serve a registered model artifact from an MLflow tracking server.
- **Per-feature drift detection**: Extend the PSI worker to compute PSI per input feature (amount, country, device type, etc.), not just on the output score. This identifies which specific features are drifting upstream.
- **Automated retraining trigger**: Add a webhook endpoint to the Control API that fires a CI/CD pipeline event when PSI exceeds a critical threshold, initiating an automated model retraining job.
- **External alerting**: Connect the Control API to PagerDuty, Slack, or email when CRITICAL alerts are written to PostgreSQL.
- **Authentication**: Add OAuth2 or API key authentication to the Control API before exposing it outside the Docker network.
- **Kubernetes deployment**: Convert `docker-compose.yml` into Helm chart manifests for deployment to a managed Kubernetes cluster (AWS EKS, Google GKE, Azure AKS).

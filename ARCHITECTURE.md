# Unified Observability: Platform Architecture Blueprint

## 1. Project Overview
Unified Observability is an enterprise-grade ML Observability, Drift Detection, and Auto-Retrain Platform designed to monitor machine learning models in production. Moving beyond simple logging, the platform tracks feature drift, concept drift, data quality regressions, fairness metrics, calibration, and anomaly spikes. It supports both high-throughput synchronous inference systems and asynchronous batch predictions. 

We anchor this system design using **Fraud Detection** as our primary deep-dive use case. Fraud detection provides an ideal rigorous environment: it requires low-latency inference, deals with highly imbalanced classes, faces adversarial actors causing continuous concept drift, and suffers from delayed ground truth (chargebacks arriving weeks later).

## 2. Why This Matters in Industry
In industry, machine learning models degrade silently. When an application crashes, an alert fires immediately. When a model's false positive rate climbs by 3% due to data drift, it might go unnoticed for weeks, costing the business millions in lost revenue or increased operational burden for manual reviewers.

A simple monitoring script checks if inputs are null. An **enterprise observability platform** maps technical metrics (e.g., Population Stability Index, KL Divergence, PR-AUC) to business impact (e.g., projected dollar loss due to false positives vs. false negatives), suppresses alert storms using historical context, and orchestrates automated, safe retraining pipelines with shadow deployment gates.

## 3. Full System Architecture

The architecture is entirely domain-agnostic, built on a robust, decoupled microservices pattern.

*   **Inference Layer**: Applications (e.g., payment gateways) call the Model Inference API (FastAPI) which retrieves online features from a Feature Store (Redis) and returns a prediction.
*   **Telemetry Ingestion**: The Inference API emits non-blocking JSON telemetry (features, predictions, probabilities, latency, model version, correlation IDs) to an Event Bus (Kafka / Redpanda).
*   **Data Plane & Storage**: 
    *   **Hot Storage (Time-Series)**: A streaming framework (Flink or Benthos) consumes the telemetry and writes aggregated window metrics to a real-time OLAP database (ClickHouse).
    *   **Cold Storage (Data Lake)**: Raw telemetry is batched and saved to Object Storage (S3 / MinIO) for historical backfilling and offline retraining.
*   **Ground Truth Pipeline**: External business systems (e.g., manual review queues, chargeback processors) emit ground truth labels back to the Event Bus, which are joined asynchronously with the original predictions in ClickHouse via the correlation IDs.
*   **Observability & Drift Workers**: Distributed workers (Celery or Temporal) periodically execute drift and anomaly detection algorithms against ClickHouse time windows and compare against the training baselines stored in a Model Registry (MLflow).
*   **Control Plane & Alerting Service**: A FastAPI backend manages the declarative configurations (thresholds, segment definitions). When an anomaly is detected, the Alert Service deduplicates it, calculates business impact, and routes it to incident management (PagerDuty, Slack).
*   **Retraining Orchestrator**: Airflow or Prefect listens to significant drift events, pulling raw data from S3 to train a challenger model. It orchestrates CI/CD evaluation gates before deployment.

## 4. Data Flow

```text
1. Transaction Originates -> 2. Model Inference API
                                  |
               (Fetches Features) |
                                  v
                            Feature Store (Redis)
                                  |
                 (Emits JSON prediction + input features)
                                  |
                                  v
                        Event Bus (Kafka Topic: `ml-telemetry`)
                                  |
          +-----------------------+-----------------------+
          |                                               |
  (Streaming Metrics & Windows)                  (Batch Raw Archiving)
          v                                               v
  Real-Time OLAP (ClickHouse) <======(Joins)========== Data Lake (S3)
          ^                                               ^
          |                                               |
          +---- Ground Truth Arrives (Kafka: `labels`) ---+
          
          |
  (Periodic Queries via Celery/Temporal Workers)
          v
  Drift & Anomaly Evaluators
          |
  (If Threshold Breached)
          v
  Alerting Engine -> PagerDuty / Retraining Pipeline Handler (Airflow)
```

## 5. Modules and Responsibilities
*   **Telemetry Ingestion Service**: Validates schema and routes data to hot/cold storage. Handles high throughput without blocking the inference path.
*   **Metrics Aggregator**: Computes moving averages of latency, error rates, and basic statistics over configurable time windows.
*   **Drift Analyzer**: Computes statistical distances (PSI, JS Divergence, Wasserstein) for numerical and categorical features.
*   **Evaluation Engine**: Joins delayed partial labels to calculate aggregate performance metrics (ROC-AUC, PR-AUC, F1, Log Loss, Calibration Error).
*   **Alert Router**: Deduplicates firing alerts, attaches historical RCA context, and executes escalation policies.
*   **Dashboard API**: Serves real-time views to the frontend UI.
*   **Retraining Controller**: Acts as the interface between the Observability platform and the CI/CD deployment pipelines.

## 6. Model Monitoring Strategy (Fraud Detection Focus)
*   **Service Health**: P95/P99 latency, RPS (Requests Per Second), timeout rates, 5xx errors.
*   **Performance Metrics (Delayed Labels)**: Since fraud chargebacks take up to 45 days, we monitor short-term proxy signals (like manual reviewer overrides) as early indicators, and calculate true PR-AUC on a rolling 45-day window.
*   **Prediction Distributions**: Monitor the daily ratio of "High Risk" vs "Low Risk" classifications. A sudden spike in "High Risk" predictions triggers a 'Prediction Drift' alert long before labels arrive.
*   **Slicing & Dicing**: Metrics are tagged by segment (e.g., Country, Device Type, Payment Method). A model may perform well globally but silently fail for Mobile/iOS users due to a broken upstream SDK update.

## 7. Drift Detection Strategy
*   **Data Drift (Covariate Shift)**: Track distribution shifts in input features. 
    *   *Method*: Population Stability Index (PSI) for categorical/binned features, Jensen-Shannon Divergence for continuous distributions.
    *   *Fraud Example*: Sudden influx of IP addresses from a new region (could be a marketing campaign or an attack).
*   **Concept Drift**: The relationship between X and Y changes.
    *   *Method*: Monitor rolling model error rate against a persistent holdout evaluation set, or track changes in feature importance (SHAP value distributions) over time.
    *   *Fraud Example*: Fraudsters figure out that low-velocity transactions avoid the model's rules, changing the prior relationship between transaction speed and fraud probability.
*   **Data Quality Monitor**: Schema validation, missing value spikes, fresh vs. stale feature tracking (online-offline skew).

## 8. Alerting and Incident Workflow
1.  **Detection**: A 6-hour time window shows the `transaction_amount` feature PSI breached 0.2 (High Drift).
2.  **Severity Scoring**: System checks business impact. Is the model's uncalibrated output score also shifting? Has the overall volume changed? If yes -> Critical. If no -> Warning.
3.  **Deduplication**: Suppress identical alerts for the next 24 hours.
4.  **Root Cause Analysis (RCA) Context**: The alert payload automatically includes: "Feature `transaction_amount` drifted. This feature constitutes 15% of SHAP importance. Associated segment: `Country=US`."
5.  **Escalation**: Create Jira ticket, ping Slack channel. If severe performance degradation is measured, trigger fallback heuristics.

## 9. Retraining and Deployment Strategy
1.  **Trigger**: Airflow DAG receives webhook from the Alerting Service indicating sustained concept drift.
2.  **Data Preparation**: Pulls recent 3 months of S3 data (excluding the anomaly spike days, if categorized as transient noise).
3.  **Model Training**: Trains the Challenger Model tracking in MLflow.
4.  **Offline Verification**: Challenger must exceed Champion's PR-AUC by 2% on the validation set, and pass fairness invariant tests (e.g., equal false positive rates across age demographics).
5.  **Shadow Deployment**: Challenger is deployed alongside Champion, scoring live traffic silently for 48 hours to ensure latency SLAs and calculate live output distributions.
6.  **Canary Rollout**: Switches 10% of traffic, monitors for 2 hours, then 100%.

## 10. Database and API Design

**Database Schema (ClickHouse / PostgreSQL Hybrid)**
*   `predictions` (ClickHouse): `req_id`, `timestamp`, `model_version`, `features_json`, `prediction_prob`.
*   `labels` (ClickHouse): `req_id`, `timestamp_arrived`, `actual_label`.
*   `metric_windows` (PostgreSQL): `window_id`, `start_time`, `end_time`, `model_id`, `metric_name`, `value`, `segment`.
*   `alerts` (PostgreSQL): `alert_id`, `severity`, `status`, `triggered_at`, `resolved_at`, `rca_json`.

**REST API Map (FastAPI)**
*   `POST /api/v1/telemetry`: High-throughput ingestion endpoint (if not bypassing directly to Kafka).
*   `POST /api/v1/labels`: Ingestion for delayed ground truth.
*   `GET /api/v1/metrics/performance`: Retrieve time-series accuracy/AUC data.
*   `GET /api/v1/metrics/drift`: Retrieve feature drift scores.
*   `POST /api/v1/config/thresholds`: Set dynamic alerting bounds.
*   `GET /api/v1/alerts`: Incident management list.

## 11. Dashboard Design
*   **Global Overview**: Traffic light status (Red/Yellow/Green) for all active models. Top line metrics (RPS, overall PR-AUC, active alerts).
*   **Model Deep Dive Page**: 
    *   *Top left*: Line chart of aggregate predictions over time.
    *   *Top right*: Line chart of latency and error rates.
    *   *Bottom*: Feature Drift heat map (Rows: Features, Columns: Days, Colors: PSI values).
*   **Data Quality Page**: Bar charts of null rates, out-of-range bounds, and schema violations.
*   **Fairness Page**: Disparate impact ratio charting across protected classes or critical business segments.

## 12. Security and Governance
*   **RBAC**: Role-Based Access Control. Data Scientists can configure thresholds; only MLOps Engineers can trigger manual rollbacks.
*   **PII Masking**: Sensitive fields (e.g., SSN, plain text names) are stripped at the Inference layer before Kafka ingestion, replaced by salted hashes for cardinality keeping.
*   **Audit Logging**: Every threshold change, forced retraining, and alert resolution is logged to a write-once database table with user identity and timestamp.

## 13. Testing and Validation
*   **Unit Tests**: Validate drift algorithms against known datasets (e.g., ensure PSI correctly calculates to 0.5 on a synthetically shifted NumPy array).
*   **Integration Tests**: Spin up Docker Compose (Kafka + ClickHouse + API), push 10,000 synthetic JSON payloads, and assert the metric window outputs.
*   **Chaos Engineering**: Simulate delayed label streams arriving out of order. Simulate partial schema drops to ensure the pipeline doesn't crash but flags the `Data Quality` monitor.

## 14. Deployment Plan
*   **Infrastructure**: Kubernetes (EKS / GKE) using Helm charts.
*   **Stateful Services**: Managed Kafka (Confluent/MSK), Managed ClickHouse or Snowflake, Managed PostgreSQL (RDS) for the control plane.
*   **CI/CD**: GitHub Actions to build Docker images, run unit/integration tests, and update Helm configurations via ArgoCD.

## 15. Roadmap from MVP to Enterprise
*   **Phase 1 (MVP)**: FastAPI backend, PostgreSQL, batch drift analysis script running daily. Simple Slack webhooks.
*   **Phase 2 (Scale)**: Introduce Kafka and ClickHouse. Enable real-time metric windows. Implement shadow deployments.
*   **Phase 3 (Enterprise)**: Multi-tenant support, ROI-based business alerting layers, automated CI/CD retraining orchestration via Airflow, fairness/bias deep-dive modules.

## 16. Optional Stretch Goals
*   **Explainability (SHAP) Monitoring**: Track not just the inputs, but the *importance* of the inputs over time to detect subtle conceptual shifts.
*   **Auto-Calibration**: A feedback loop that dynamically adjusts the classification threshold (e.g., from 0.5 to 0.55) to maintain a strict Precision SLA as the underlying distribution shifts, preventing alert floods while the model is retraining.
*   **Cost-Aware Evaluations**: Weight false positives and false negatives by actual dollar amounts dynamically fetched from the transaction payload.

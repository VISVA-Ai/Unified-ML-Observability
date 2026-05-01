from contextlib import asynccontextmanager

import clickhouse_connect
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.db_postgres import get_postgres_connection, init_postgres_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_postgres_schema()
    except Exception:
        pass  # Schema already exists — safe to ignore
    yield


app = FastAPI(
    title="Observability Control API",
    description="Control plane for configuring thresholds and retrieving observability metrics.",
    version="1.0.0",
    lifespan=lifespan,
)

# ClickHouse client for performance metrics queries
_ch_client = None

def get_ch():
    global _ch_client
    if _ch_client is None:
        _ch_client = clickhouse_connect.get_client(
            host="clickhouse", port=8123, username="default", password=""
        )
    return _ch_client


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class ThresholdConfig(BaseModel):
    psi_warning_threshold: float = 0.10
    psi_critical_threshold: float = 0.20
    latency_p95_threshold_ms: float = 50.0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health():
    """Liveness probe — returns OK when the service is ready."""
    return {"status": "ok"}


@app.get("/alerts", tags=["Incidents"])
def get_alerts(limit: int = 50):
    """Return the most recent alerts ordered by trigger time."""
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, alert_type, severity, message, model_version,
               drift_score, triggered_at
        FROM alerts
        ORDER BY triggered_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return {"alerts": rows}


@app.get("/metrics/performance", tags=["Metrics"])
def get_performance_metrics():
    """
    Aggregate performance summary calculated by joining predictions with
    the delayed ground truth labels received so far.
    """
    try:
        ch = get_ch()

        # Total predictions in the last hour
        total_res = ch.query(
            "SELECT count(), avg(latency_ms), avg(prediction_score) "
            "FROM predictions WHERE timestamp > now() - INTERVAL 1 HOUR"
        )
        total_count, avg_latency, avg_score = total_res.result_rows[0]

        # Labeled predictions — inner join on request_id
        joined_res = ch.query(
            """
            SELECT count(),
                   countIf(p.prediction_class = l.actual_label) AS correct
            FROM predictions p
            INNER JOIN labels l ON p.request_id = l.request_id
            WHERE p.timestamp > now() - INTERVAL 1 HOUR
            """
        )
        labeled_count, correct = joined_res.result_rows[0]
        accuracy = round(correct / labeled_count, 4) if labeled_count > 0 else None

        return {
            "window": "last_1_hour",
            "total_predictions": int(total_count or 0),
            "labeled_count": int(labeled_count or 0),
            "label_coverage_pct": round((labeled_count / total_count * 100), 1) if total_count else 0,
            "accuracy": accuracy,
            "avg_prediction_score": round(float(avg_score or 0), 4),
            "avg_latency_ms": round(float(avg_latency or 0), 2),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics/drift", tags=["Metrics"])
def get_drift_metrics():
    """Return the last 10 drift-related alerts (DATA_DRIFT and CONCEPT_DRIFT)."""
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, alert_type, severity, drift_score, model_version, triggered_at
        FROM alerts
        WHERE alert_type IN ('DATA_DRIFT', 'CONCEPT_DRIFT')
        ORDER BY triggered_at DESC
        LIMIT 10
        """,
    )
    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return {"drift_events": rows, "count": len(rows)}


@app.post("/thresholds/{model_version}", tags=["Configuration"])
def set_thresholds(model_version: str, config: ThresholdConfig):
    """Upsert alerting thresholds for a given model version."""
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO model_thresholds
            (model_version, psi_warning_threshold, psi_critical_threshold, latency_p95_threshold_ms)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (model_version) DO UPDATE SET
            psi_warning_threshold    = EXCLUDED.psi_warning_threshold,
            psi_critical_threshold   = EXCLUDED.psi_critical_threshold,
            latency_p95_threshold_ms = EXCLUDED.latency_p95_threshold_ms
        """,
        (
            model_version,
            config.psi_warning_threshold,
            config.psi_critical_threshold,
            config.latency_p95_threshold_ms,
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "updated", "model_version": model_version, "config": config.model_dump()}

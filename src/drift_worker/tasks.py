import time
import logging
import numpy as np
from celery import Celery

from shared.db_clickhouse import get_clickhouse_client
from shared.db_postgres import get_postgres_connection, init_postgres_schema

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

celery_app = Celery("drift_worker", broker="redis://redis:6379/0")


@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Drift checks run frequently for demonstration; tune for production.
    sender.add_periodic_task(15.0, check_service_health.s(), name="service-health-15s")
    sender.add_periodic_task(30.0, check_prediction_drift.s(), name="prediction-drift-30s")
    sender.add_periodic_task(30.0, check_feature_psi_drift.s(), name="feature-psi-30s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_alert(alert_type: str, severity: str, message: str, model_version: str, drift_score: float = None):
    """Insert a deduplication-aware alert into PostgreSQL."""
    try:
        pg = get_postgres_connection()
        cur = pg.cursor()
        cur.execute(
            """
            INSERT INTO alerts (alert_type, severity, message, model_version, drift_score)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (alert_type, severity, message, model_version, drift_score),
        )
        pg.commit()
        cur.close()
        pg.close()
        logger.info(f"Alert inserted: [{severity}] {alert_type} — {message}")
    except Exception as e:
        logger.error(f"Failed to insert alert: {e}")


def calculate_psi(expected: np.ndarray, actual: np.ndarray, n_bins: int = 10) -> float:
    """
    Population Stability Index (PSI) measures distribution shift between two samples.

    PSI < 0.10  → No significant change
    PSI 0.10–0.20 → Moderate change (WARNING)
    PSI > 0.20  → Significant shift (CRITICAL)

    Reference: Siddiqi, N. (2006). Credit Risk Scorecards.
    """
    # Build bin edges from the expected (baseline) distribution
    breakpoints = np.linspace(0, 1, n_bins + 1)

    expected_counts, _ = np.histogram(expected, bins=breakpoints)
    actual_counts, _ = np.histogram(actual, bins=breakpoints)

    # Convert to relative frequencies; clip to avoid division-by-zero or log(0)
    expected_pct = np.clip(expected_counts / len(expected), 1e-6, None)
    actual_pct = np.clip(actual_counts / len(actual), 1e-6, None)

    psi_per_bin = (actual_pct - expected_pct) * np.log(actual_pct / expected_pct)
    return float(np.sum(psi_per_bin))


# ---------------------------------------------------------------------------
# Celery Tasks
# ---------------------------------------------------------------------------

@celery_app.task
def check_service_health():
    """Check for latency spikes — a basic service-level health indicator."""
    logger.info("Running service health check...")
    try:
        ch = get_clickhouse_client()
        res = ch.query(
            "SELECT count(), avg(latency_ms) FROM predictions "
            "WHERE timestamp > now() - INTERVAL 1 MINUTE"
        )
        count, avg_latency = res.result_rows[0]

        if count > 0 and avg_latency > 50.0:
            logger.warning(f"Latency spike: avg={avg_latency:.1f}ms over {count} requests")
            _insert_alert(
                alert_type="LATENCY_SPIKE",
                severity="WARNING",
                message=f"Average latency is {avg_latency:.1f}ms over the last minute ({count} requests).",
                model_version="xgboost-fraud-v1.0",
            )
        else:
            logger.info(f"Service health OK — count={count}, avg_latency={avg_latency:.1f}ms")
    except Exception as e:
        logger.error(f"check_service_health failed: {e}")


@celery_app.task
def check_prediction_drift():
    """
    Concept Drift check: monitor the rolling average prediction score.

    A sustained spike in the model's output distribution indicates the model
    is encountering a materially different input regime (e.g., an attack pattern).
    """
    logger.info("Running prediction distribution drift check...")
    try:
        ch = get_clickhouse_client()
        res = ch.query(
            "SELECT count(), avg(prediction_score) FROM predictions "
            "WHERE timestamp > now() - INTERVAL 1 MINUTE"
        )
        count, avg_score = res.result_rows[0]

        if count is None or avg_score is None:
            return

        logger.info(f"Prediction drift check — count={count}, avg_score={avg_score:.3f}")

        # Baseline: normal average risk is ~0.10 for clean traffic.
        # A spike to >0.30 with sufficient volume signals concept drift.
        if count >= 10 and avg_score > 0.30:
            severity = "CRITICAL" if avg_score > 0.50 else "WARNING"
            _insert_alert(
                alert_type="CONCEPT_DRIFT",
                severity=severity,
                message=(
                    f"Model output distribution shifted significantly. "
                    f"Avg risk score: {avg_score:.3f} over {count} predictions (baseline ~0.10)."
                ),
                model_version="xgboost-fraud-v1.0",
                drift_score=avg_score,
            )
    except Exception as e:
        logger.error(f"check_prediction_drift failed: {e}")


@celery_app.task
def check_feature_psi_drift():
    """
    Data Drift (Covariate Shift) check using Population Stability Index (PSI).

    Compares the prediction score distribution between a rolling baseline window
    (last 60 minutes) and the current window (last 2 minutes). A significant PSI
    indicates the input feature space has shifted — even before labels arrive.
    """
    logger.info("Running PSI-based feature drift check...")
    try:
        ch = get_clickhouse_client()

        baseline_res = ch.query(
            "SELECT prediction_score FROM predictions "
            "WHERE timestamp > now() - INTERVAL 60 MINUTE "
            "AND timestamp <= now() - INTERVAL 2 MINUTE "
            "LIMIT 5000"
        )
        current_res = ch.query(
            "SELECT prediction_score FROM predictions "
            "WHERE timestamp > now() - INTERVAL 2 MINUTE "
            "LIMIT 1000"
        )

        baseline_scores = np.array([r[0] for r in baseline_res.result_rows], dtype=float)
        current_scores = np.array([r[0] for r in current_res.result_rows], dtype=float)

        if len(baseline_scores) < 20 or len(current_scores) < 10:
            logger.info(
                f"PSI check skipped — insufficient data "
                f"(baseline={len(baseline_scores)}, current={len(current_scores)})"
            )
            return

        psi = calculate_psi(baseline_scores, current_scores)
        logger.info(f"PSI score: {psi:.4f} (baseline={len(baseline_scores)}, current={len(current_scores)})")

        if psi > 0.20:
            _insert_alert(
                alert_type="DATA_DRIFT",
                severity="CRITICAL",
                message=(
                    f"PSI={psi:.4f} — Significant covariate shift detected in prediction score distribution. "
                    f"Investigate upstream feature pipeline for schema or distribution changes."
                ),
                model_version="xgboost-fraud-v1.0",
                drift_score=psi,
            )
        elif psi > 0.10:
            _insert_alert(
                alert_type="DATA_DRIFT",
                severity="WARNING",
                message=(
                    f"PSI={psi:.4f} — Moderate distribution shift detected. "
                    f"Monitor closely for further degradation."
                ),
                model_version="xgboost-fraud-v1.0",
                drift_score=psi,
            )
        else:
            logger.info(f"PSI check OK — PSI={psi:.4f} is within acceptable range.")

    except Exception as e:
        logger.error(f"check_feature_psi_drift failed: {e}")


# ---------------------------------------------------------------------------
# Startup: ensure schema exists before tasks run
# ---------------------------------------------------------------------------

@celery_app.on_after_finalize.connect
def ensure_schema(**kwargs):
    try:
        init_postgres_schema()
        logger.info("Postgres schema verified by drift_worker.")
    except Exception as e:
        logger.warning(f"Schema init check failed (may already exist): {e}")

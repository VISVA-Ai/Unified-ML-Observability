import psycopg2


def get_postgres_connection(host="postgres"):
    return psycopg2.connect(
        host=host,
        database="observability_metadata",
        user="admin",
        password="password",
        port=5432,
    )


def init_postgres_schema(host="postgres"):
    """
    Idempotent schema initialisation — safe to call from multiple services
    at startup without conflict. Uses IF NOT EXISTS throughout.
    """
    conn = get_postgres_connection(host)
    cur = conn.cursor()

    # Alerts table — written to by the drift_worker on every detected anomaly.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id            SERIAL PRIMARY KEY,
            alert_type    VARCHAR(100) NOT NULL,
            severity      VARCHAR(50)  NOT NULL,
            message       TEXT,
            model_version VARCHAR(100),
            drift_score   FLOAT        NULL,
            rca_feature   VARCHAR(100) NULL,
            triggered_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
            resolved_at   TIMESTAMP    NULL
        )
    """)

    # Model threshold configuration — updated via the Control API.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS model_thresholds (
            model_version           VARCHAR(100) PRIMARY KEY,
            psi_warning_threshold   FLOAT DEFAULT 0.10,
            psi_critical_threshold  FLOAT DEFAULT 0.20,
            latency_p95_threshold_ms FLOAT DEFAULT 50.0
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    init_postgres_schema(host="localhost")
    print("PostgreSQL schema initialised.")

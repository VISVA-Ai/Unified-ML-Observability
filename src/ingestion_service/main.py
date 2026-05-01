import time
import json
import logging
import os
from datetime import datetime

from shared.kafka_client import get_kafka_consumer, KAFKA_BROKER_DOCKER
from shared.db_clickhouse import get_clickhouse_client, init_clickhouse_schema
from shared.db_postgres import init_postgres_schema

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 100
MAX_WAIT_SECONDS = 5.0


def run_ingestion():
    # Wait for ClickHouse and Postgres to be ready before consuming
    logger.info("Initialising database schemas...")
    for attempt in range(10):
        try:
            init_clickhouse_schema()
            init_postgres_schema()
            logger.info("Schemas ready.")
            break
        except Exception as e:
            logger.warning(f"Schema init attempt {attempt + 1}/10 failed: {e}. Retrying in 5s...")
            time.sleep(5)

    clickhouse = get_clickhouse_client()
    consumer = get_kafka_consumer("ingestion_group", broker=KAFKA_BROKER_DOCKER)
    consumer.subscribe(["ml-telemetry", "ml-labels"])
    logger.info("Ingestion service listening on topics: ml-telemetry, ml-labels")

    batch = []
    last_flush = time.time()

    def flush_batch():
        nonlocal batch, last_flush
        if not batch:
            return

        predictions_data = []
        labels_data = []

        for topic, msg in batch:
            if topic == "ml-telemetry":
                predictions_data.append([
                    msg.get("request_id"),
                    datetime.fromisoformat(msg.get("timestamp").replace("Z", "+00:00")),
                    str(msg.get("model_version")),
                    str(msg.get("segment")),
                    json.dumps(msg.get("features", {})),
                    float(msg.get("prediction_score", 0.0)),
                    int(msg.get("prediction_class", -1)),
                    float(msg.get("latency_ms", 0.0)),
                ])
            elif topic == "ml-labels":
                labels_data.append([
                    msg.get("request_id"),
                    datetime.fromisoformat(msg.get("timestamp_arrived").replace("Z", "+00:00")),
                    int(msg.get("actual_label", -1)),
                ])

        if predictions_data:
            try:
                clickhouse.insert(
                    "predictions",
                    predictions_data,
                    column_names=[
                        "request_id", "timestamp", "model_version", "segment",
                        "features_json", "prediction_score", "prediction_class", "latency_ms",
                    ],
                )
                logger.info(f"Flushed {len(predictions_data)} predictions to ClickHouse.")
            except Exception as e:
                logger.error(f"Prediction insert failed: {e}")

        if labels_data:
            try:
                clickhouse.insert(
                    "labels",
                    labels_data,
                    column_names=["request_id", "timestamp_arrived", "actual_label"],
                )
                logger.info(f"Flushed {len(labels_data)} labels to ClickHouse.")
            except Exception as e:
                logger.error(f"Label insert failed: {e}")

        batch.clear()
        last_flush = time.time()

    try:
        while True:
            msg = consumer.poll(1.0)

            if msg is None:
                if time.time() - last_flush > MAX_WAIT_SECONDS:
                    flush_batch()
                continue

            if msg.error():
                logger.error(f"Consumer error: {msg.error()}")
                continue

            try:
                value = json.loads(msg.value().decode("utf-8"))
                batch.append((msg.topic(), value))

                if len(batch) >= MAX_BATCH_SIZE or time.time() - last_flush > MAX_WAIT_SECONDS:
                    flush_batch()
            except Exception as e:
                logger.error(f"Message parsing error: {e}")

    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
    finally:
        flush_batch()
        consumer.close()
        logger.info("Ingestion service stopped cleanly.")


if __name__ == "__main__":
    run_ingestion()

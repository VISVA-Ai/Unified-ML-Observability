import time
import random
import json
import os
import redis

from shared.kafka_client import get_kafka_producer, KAFKA_BROKER_DOCKER, KAFKA_BROKER_LOCAL, produce_json
from shared.models import GroundTruth


def _determine_actual_label(prediction_score: float) -> int:
    """
    Simulate correlated ground truth based on the original model score.

    This mimics a realistic fraud detection environment where:
      - High-risk predictions (>0.6) are fraudulent 85% of the time.
      - Low-risk predictions (<0.2) are legitimate 95% of the time.
      - The middle band is noisy (20% fraud rate).
    """
    if prediction_score > 0.6:
        return random.choices([1, 0], weights=[85, 15])[0]
    elif prediction_score < 0.2:
        return random.choices([0, 1], weights=[95, 5])[0]
    else:
        return random.choices([0, 1], weights=[80, 20])[0]


def run_simulation(broker=KAFKA_BROKER_DOCKER):
    """
    Continuously pop pending predictions from Redis and emit delayed ground
    truth labels to the `ml-labels` Kafka topic.

    The delay (3-15 seconds) simulates the real-world lag of fraud chargebacks,
    manual review queues, or other asynchronous labelling processes.
    """
    producer = get_kafka_producer(broker)
    redis_client = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)
    print(f"Label simulator started. Broker: {broker}")

    while True:
        # BLPOP blocks until an item is available — no busy-waiting
        result = redis_client.blpop("pending_labels", timeout=10)

        if result is None:
            # Timeout — nothing in the queue yet; loop and wait
            continue

        _, raw = result
        pending = json.loads(raw)

        # Simulate delayed ground truth arrival (chargeback lag, manual review, etc.)
        delay = random.uniform(3, 15)
        time.sleep(delay)

        actual_label = _determine_actual_label(pending["prediction_score"])

        gt = GroundTruth(
            request_id=pending["request_id"],
            actual_label=actual_label,
        )

        produce_json(producer, "ml-labels", gt.request_id, gt.model_dump(mode="json"))
        print(
            f"  Label emitted | req={gt.request_id[:8]}... "
            f"score={pending['prediction_score']:.2f} -> label={actual_label} "
            f"(delay={delay:.1f}s)"
        )


if __name__ == "__main__":
    broker = KAFKA_BROKER_LOCAL if os.getenv("LOCAL_TEST") == "1" else KAFKA_BROKER_DOCKER
    run_simulation(broker)

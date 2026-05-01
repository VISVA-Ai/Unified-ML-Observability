from confluent_kafka import Producer, Consumer
import json

KAFKA_BROKER_DOCKER = 'redpanda:29092'
KAFKA_BROKER_LOCAL = 'localhost:9092'

def get_kafka_producer(broker=KAFKA_BROKER_DOCKER):
    conf = {'bootstrap.servers': broker}
    return Producer(conf)

def get_kafka_consumer(group_id: str, broker=KAFKA_BROKER_DOCKER, auto_offset_reset='earliest'):
    conf = {
        'bootstrap.servers': broker,
        'group.id': group_id,
        'auto.offset.reset': auto_offset_reset
    }
    return Consumer(conf)

def produce_json(producer: Producer, topic: str, key: str, value: dict):
    producer.produce(topic, key=key.encode('utf-8'), value=json.dumps(value).encode('utf-8'))
    producer.poll(0)

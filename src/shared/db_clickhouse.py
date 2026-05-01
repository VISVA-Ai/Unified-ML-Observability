import clickhouse_connect

# Default to localhost for local testing outside of docker, otherwise clickhouse
def get_clickhouse_client(host='clickhouse', port=8123):
    return clickhouse_connect.get_client(host=host, port=port, username='default', password='')

def init_clickhouse_schema(host='clickhouse'):
    client = get_clickhouse_client(host)
    
    # Telemetry table
    client.command("""
        CREATE TABLE IF NOT EXISTS predictions (
            request_id String,
            timestamp DateTime64(3),
            model_version String,
            segment String,
            features_json String,
            prediction_score Float32,
            prediction_class UInt8,
            latency_ms Float32
        ) ENGINE = MergeTree()
        ORDER BY (model_version, timestamp)
    """)
    
    # Labels table
    client.command("""
        CREATE TABLE IF NOT EXISTS labels (
            request_id String,
            timestamp_arrived DateTime64(3),
            actual_label UInt8
        ) ENGINE = MergeTree()
        ORDER BY (request_id)
    """)
    print("ClickHouse schema initialized successfully.")

if __name__ == "__main__":
    init_clickhouse_schema(host="localhost")

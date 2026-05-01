import requests
import time
import random

API_URL = "http://localhost:8000/predict"

def send_traffic(count=100, delay=0.5, drift=False):
    print(f"Starting load generator (drift={'ON' if drift else 'OFF'})...")

    countries = ["US", "GB", "DE", "FR", "IN"]
    if drift:
        countries = ["NG", "RU", "KP"]

    for i in range(count):
        payload = {
            "user_id": f"user_{random.randint(1000, 9999)}",
            "amount": random.uniform(500, 2000) if drift else random.uniform(10, 100),
            "merchant_category": random.choice(["electronics", "food", "travel", "fashion"]),
            "country": random.choice(countries),
            "is_weekend": random.choice([True, False]),
            "device_type": random.choice(["ios", "android", "web", "unknown"])
        }

        try:
            resp = requests.post(API_URL, json=payload, timeout=5)
            if resp.status_code != 200:
                print(f"[{i+1}/{count}] HTTP {resp.status_code}: {resp.text[:200]}")
                continue
            data = resp.json()
            print(f"[{i+1}/{count}] Score: {data['fraud_probability']:.2f} | Fraud: {data['is_fraud']}")
        except requests.ConnectionError:
            print(f"[{i+1}/{count}] Connection refused — is prediction_api running on localhost:8000?")
            break
        except Exception as e:
            print(f"[{i+1}/{count}] Error: {e}")

        time.sleep(delay)

if __name__ == "__main__":
    # 1. Send normal traffic
    send_traffic(count=20, delay=0.2, drift=False)

    # 2. Wait a bit
    print("\n--- Simulating Distribution Shift (High Risk Traffic) ---\n")
    time.sleep(2)

    # 3. Send drifting traffic
    send_traffic(count=20, delay=0.2, drift=True)

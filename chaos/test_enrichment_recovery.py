#!/usr/bin/env python3
"""
Chaos test: kill enrichment-service mid-pipeline and prove no data is lost.

This is the actual claim the transactional outbox + Kafka consumer-group
architecture makes (see docs/adr/0005-transactional-outbox.md): a consumer
being down doesn't lose work, it just delays it. Kafka retains the message;
the consumer group's committed offset means enrichment-service picks up
exactly where it left off on restart.

Requires the full stack running: `docker compose up -d`.
Uses only the standard library — no pip install needed to run this.

Usage:
    python chaos/test_enrichment_recovery.py
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

BASE_URL = "http://localhost:8000"
CONTAINER_NAME = "personal-finance-platform-enrichment-service-1"


def _request(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _docker(*args: str) -> None:
    subprocess.run(["docker", *args], check=True, capture_output=True)


def _container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True
    )
    return result.stdout.strip() == "true"


def step(message: str) -> None:
    print(f"\n\033[1m--- {message} ---\033[0m")


def main() -> None:
    step("Registering a throwaway test user")
    email = f"chaos-{uuid4().hex[:8]}@example.com"
    status, body = _request(
        "POST", "/api/v1/auth/register", body={"email": email, "password": "correct-horse-battery"}
    )
    assert status == 201, f"register failed: {status} {body}"
    token = body["access_token"]
    print(f"  user: {email}")

    step("Creating an account")
    status, account = _request(
        "POST",
        "/api/v1/accounts",
        token=token,
        body={"name": "Chaos Checking", "type": "checking", "current_balance_minor": 100000},
    )
    assert status == 201, f"account creation failed: {status} {account}"
    account_id = account["id"]

    step(f"Killing {CONTAINER_NAME} - simulating a crash mid-pipeline")
    _docker("kill", CONTAINER_NAME)
    assert not _container_running(CONTAINER_NAME), "container should be down"
    print("  enrichment-service is down.")

    step("Creating an uncategorized transaction while the consumer is dead")
    status, transaction = _request(
        "POST",
        "/api/v1/transactions",
        token=token,
        body={
            "account_id": account_id,
            "merchant_name": "Starbucks",
            "amount_minor": -650,
            "txn_date": "2026-01-15",
        },
    )
    assert status == 201, f"transaction creation failed: {status} {transaction}"
    transaction_id = transaction["id"]
    print(f"  transaction {transaction_id} created, category_id={transaction['category_id']}")

    step("Waiting for the outbox publisher to push it to Kafka anyway")
    time.sleep(5)
    _, transaction = _request("GET", f"/api/v1/transactions/{transaction_id}", token=token)
    assert transaction["category_id"] is None, (
        "transaction got categorized with enrichment-service down — "
        "something else is processing this topic, or the container didn't actually die"
    )
    print("  confirmed: still uncategorized. The message is sitting in Kafka, not lost, not silently dropped.")

    step(f"Restarting {CONTAINER_NAME}")
    _docker("start", CONTAINER_NAME)
    deadline = time.time() + 20
    while time.time() < deadline and not _container_running(CONTAINER_NAME):
        time.sleep(0.5)
    assert _container_running(CONTAINER_NAME), "container failed to restart"
    print("  enrichment-service is back up.")

    step("Waiting for the consumer group to resume and reprocess the pending message")
    # Kafka consumer-group rejoin (session/rebalance timeout) plus Python
    # interpreter startup takes noticeably longer than the container simply
    # reporting "Running" — 60s gives real headroom instead of a flaky test.
    for attempt in range(30):
        time.sleep(2)
        _, transaction = _request("GET", f"/api/v1/transactions/{transaction_id}", token=token)
        if transaction["category_id"] is not None:
            break
    else:
        print("\n\033[91mFAILED: transaction was never categorized after recovery.\033[0m")
        sys.exit(1)

    print(f"  categorized as category_id={transaction['category_id']} after recovery.")
    print("\n\033[92mPASSED\033[0m - no data loss across a mid-pipeline consumer crash.")


if __name__ == "__main__":
    main()

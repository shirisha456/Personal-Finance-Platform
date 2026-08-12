#!/usr/bin/env python3
"""
Chaos test: take the Kafka broker (Redpanda) down entirely and prove the
API keeps working, then prove the outbox catches up once the broker is
back — with no data lost and no manual intervention.

This is a different failure mode from chaos/test_enrichment_recovery.py
(one consumer down vs. the broker itself unreachable), and it's the
actual point of the transactional outbox (ADR-0005): writing the outbox
row happens in the same Postgres transaction as the business row, with
no synchronous call to Kafka at all. A transaction being created should
never fail, block, or hang just because Redpanda is down — publishing is
the separate, independently-retrying outbox_publisher loop's job
(app/core/outbox_publisher.py), not the request path's.

Requires the full stack running: `docker compose up -d`.
Uses only the standard library — no pip install needed to run this.

Usage:
    python chaos/test_outbox_broker_outage.py
"""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

BASE_URL = "http://localhost:8000"
REDPANDA_CONTAINER = "personal-finance-platform-redpanda-1"


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

    step(f"Stopping {REDPANDA_CONTAINER} - the broker itself, not a consumer")
    _docker("stop", REDPANDA_CONTAINER)
    assert not _container_running(REDPANDA_CONTAINER), "redpanda should be down"
    print("  redpanda is down.")

    step("Creating an uncategorized transaction with the broker unreachable")
    start = time.monotonic()
    status, transaction = _request(
        "POST",
        "/api/v1/transactions",
        token=token,
        body={
            "account_id": account_id,
            "merchant_name": "Blue Bottle Coffee",
            "amount_minor": -725,
            "txn_date": "2026-01-15",
        },
    )
    elapsed = time.monotonic() - start
    assert status == 201, (
        f"transaction creation failed with the broker down: {status} {transaction} - "
        "the outbox write should never depend on Kafka being reachable"
    )
    assert elapsed < 5, (
        f"transaction creation took {elapsed:.1f}s with the broker down - "
        "the request path should never block on Kafka connectivity"
    )
    transaction_id = transaction["id"]
    print(f"  transaction {transaction_id} created in {elapsed:.2f}s despite the broker being down.")

    step("Confirming core-api itself stayed healthy through the outage")
    status, health = _request("GET", "/ready")
    # /ready checks the database, not Kafka — the app has no synchronous
    # Kafka dependency to report unready over. A 200 here (not a crash,
    # not a hang) is the actual claim being tested.
    assert status == 200, f"core-api became unhealthy with the broker down: {status} {health}"
    print("  /ready still reports healthy - the outage never reached the request path.")

    step("Confirming the transaction is still uncategorized (nothing could have been published yet)")
    time.sleep(3)
    _, transaction = _request("GET", f"/api/v1/transactions/{transaction_id}", token=token)
    assert transaction["category_id"] is None, (
        "transaction got categorized with the broker down — something processed this event "
        "without Kafka, which shouldn't be possible"
    )
    print("  confirmed: still uncategorized, sitting in the outbox table waiting to be published.")

    step(f"Restarting {REDPANDA_CONTAINER}")
    _docker("start", REDPANDA_CONTAINER)
    deadline = time.time() + 60
    healthy = False
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "exec", REDPANDA_CONTAINER, "rpk", "cluster", "health"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and re.search(r"Healthy:\s+true", result.stdout):
            healthy = True
            break
        time.sleep(2)
    assert healthy, "redpanda did not report healthy again in time"
    print("  redpanda is back up and healthy.")

    step("Waiting for the outbox publisher's next retry to catch up and enrichment-service to process it")
    # outbox_publisher retries every 3s (PUBLISH_INTERVAL_SECONDS); consumer
    # group rejoin/rebalance across enrichment-service can add real delay
    # too — same generous headroom as test_enrichment_recovery.py.
    for attempt in range(30):
        time.sleep(2)
        _, transaction = _request("GET", f"/api/v1/transactions/{transaction_id}", token=token)
        if transaction["category_id"] is not None:
            break
    else:
        print("\n\033[91mFAILED: transaction was never categorized after the broker recovered.\033[0m")
        sys.exit(1)

    print(f"  categorized as category_id={transaction['category_id']} after the broker recovered.")
    print("\n\033[92mPASSED\033[0m - the API never depended on Kafka being up, and no data was lost.")


if __name__ == "__main__":
    main()

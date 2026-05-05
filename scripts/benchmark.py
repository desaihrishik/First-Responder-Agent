#!/usr/bin/env python3
"""
NYC First Responder Dispatch — Benchmark Suite
Runs queries against live /triage endpoint and reports latency statistics.
"""

import json
import time
import sys
import statistics
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"
NUM_QUERIES = 100
TIMEOUT = 30.0

SCENARIOS = [
    {
        "text": "Loud music from apartment at 2am, fourth weekend in a row",
        "borough": "Manhattan",
    },
    {
        "text": "Man collapsed on sidewalk, not breathing, turning blue",
        "borough": "Brooklyn",
    },
    {
        "text": "Smoke pouring from third floor window, people screaming inside",
        "borough": "Brooklyn",
    },
    {
        "text": "Car flipped on BQE, driver trapped, fuel leaking",
        "borough": "Queens",
    },
    {
        "text": "Unattended package near subway entrance, 30 minutes unclaimed",
        "borough": "Manhattan",
    },
    {
        "text": "Gunshots heard near the park, multiple rounds fired",
        "borough": "Bronx",
    },
    {
        "text": "Water main break flooding the intersection, cars stuck",
        "borough": "Queens",
    },
    {
        "text": "Gas smell in apartment building lobby, getting stronger",
        "borough": "Manhattan",
    },
    {
        "text": "Dog attacking pedestrian on the sidewalk, victim bleeding",
        "borough": "Staten Island",
    },
    {
        "text": "Scaffolding collapse on construction site, workers may be trapped",
        "borough": "Manhattan",
    },
]


def run_benchmark():
    print("=" * 60)
    print("NYC First Responder Dispatch — Benchmark")
    print("=" * 60)

    client = httpx.Client(timeout=TIMEOUT)

    try:
        health = client.get(f"{API_BASE}/health")
        if health.status_code != 200:
            print(f"ERROR: /health returned {health.status_code}")
            sys.exit(1)
        health_data = health.json()
        print(f"Server status: {health_data.get('status')}")
        print(f"Models: {health_data.get('models')}")
        print(f"Memory: {health_data.get('memory', {}).get('used_gb', '?')}GB / {health_data.get('memory', {}).get('total_gb', '?')}GB")
    except Exception as e:
        print(f"ERROR: Cannot connect to API at {API_BASE}: {e}")
        sys.exit(1)

    print(f"\nRunning {NUM_QUERIES} queries...")
    print("-" * 60)

    latencies = []
    successes = 0
    failures = 0
    total_tokens = 0

    for i in range(NUM_QUERIES):
        scenario = SCENARIOS[i % len(SCENARIOS)]

        t0 = time.perf_counter()
        try:
            resp = client.post(
                f"{API_BASE}/triage",
                json={
                    "text": scenario["text"],
                    "borough": scenario.get("borough"),
                },
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if resp.status_code == 200:
                data = resp.json()
                server_ms = int(resp.headers.get("X-Total-Ms", elapsed_ms))
                latencies.append(server_ms)
                successes += 1

                summary_len = len(data.get("summary", ""))
                total_tokens += summary_len // 4

                if (i + 1) % 10 == 0:
                    print(f"  [{i+1:3d}/{NUM_QUERIES}] {server_ms:6d}ms | sev={data.get('severity')} | {data.get('agency'):10s} | {scenario['text'][:50]}...")
            else:
                failures += 1
                latencies.append(elapsed_ms)
                print(f"  [{i+1:3d}/{NUM_QUERIES}] FAIL ({resp.status_code})")

        except Exception as e:
            failures += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            print(f"  [{i+1:3d}/{NUM_QUERIES}] ERROR: {e}")

    client.close()

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    if not latencies:
        print("No successful queries.")
        sys.exit(1)

    latencies.sort()

    def percentile(data, p):
        idx = int(len(data) * p)
        idx = min(idx, len(data) - 1)
        return data[idx]

    results = {
        "total_queries": NUM_QUERIES,
        "successes": successes,
        "failures": failures,
        "success_rate": round(successes / NUM_QUERIES * 100, 1),
        "latency_ms": {
            "min": round(min(latencies), 1),
            "p50": round(percentile(latencies, 0.50), 1),
            "p95": round(percentile(latencies, 0.95), 1),
            "p99": round(percentile(latencies, 0.99), 1),
            "max": round(max(latencies), 1),
            "mean": round(statistics.mean(latencies), 1),
            "stdev": round(statistics.stdev(latencies), 1) if len(latencies) > 1 else 0,
        },
        "estimated_tokens_per_sec": round(total_tokens / (sum(latencies) / 1000), 1) if sum(latencies) > 0 else 0,
    }

    print(f"  Queries:     {results['total_queries']}")
    print(f"  Success:     {results['successes']} ({results['success_rate']}%)")
    print(f"  Failures:    {results['failures']}")
    print()
    print(f"  Latency (ms):")
    print(f"    min:   {results['latency_ms']['min']:>8.1f}")
    print(f"    p50:   {results['latency_ms']['p50']:>8.1f}")
    print(f"    p95:   {results['latency_ms']['p95']:>8.1f}")
    print(f"    p99:   {results['latency_ms']['p99']:>8.1f}")
    print(f"    max:   {results['latency_ms']['max']:>8.1f}")
    print(f"    mean:  {results['latency_ms']['mean']:>8.1f}")
    print(f"    stdev: {results['latency_ms']['stdev']:>8.1f}")
    print()
    print(f"  Est. tokens/sec: {results['estimated_tokens_per_sec']}")

    output_path = Path("benchmark_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    run_benchmark()

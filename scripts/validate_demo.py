#!/usr/bin/env python3
"""
NYC First Responder Dispatch — Demo Scenario Validation
Runs all 5 demo scenarios against the live API and validates outputs.
Use this as a final check before the Hack Fair.
"""

import json
import sys
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"
TIMEOUT = 30.0

SCENARIOS_FILE = Path(__file__).resolve().parent.parent / "demo" / "scenarios.json"


def load_scenarios() -> list[dict]:
    with open(SCENARIOS_FILE) as f:
        data = json.load(f)
    return data["scenarios"]


def validate_response(resp: dict, expected: dict, scenario_id: str) -> list[str]:
    """Validate a triage response against expected values. Returns list of issues."""
    issues = []

    severity = resp.get("severity")
    if not isinstance(severity, int) or severity < 1 or severity > 5:
        issues.append(f"Invalid severity: {severity}")

    agency = resp.get("agency", "")
    valid_agencies = {"NYPD", "FDNY", "EMS", "Sanitation", "Buildings", "Housing", "Multi", "311"}
    if agency not in valid_agencies:
        issues.append(f"Invalid agency: {agency}")

    expected_agency = expected.get("agency")
    if expected_agency and agency != expected_agency:
        issues.append(f"Agency mismatch: got {agency}, expected {expected_agency}")

    expected_severity = expected.get("severity")
    if expected_severity:
        if abs(severity - expected_severity) > 1:
            issues.append(f"Severity off by >1: got {severity}, expected {expected_severity}")

    summary = resp.get("summary", "")
    if len(summary) < 20:
        issues.append(f"Summary too short ({len(summary)} chars)")

    confidence = resp.get("confidence", 0)
    if confidence < 0.5:
        issues.append(f"Low confidence: {confidence}")

    similar = resp.get("similar_incidents", [])
    if len(similar) == 0:
        issues.append("No similar incidents returned")

    total_ms = resp.get("total_ms", 0)
    if total_ms > 5000:
        issues.append(f"Latency too high: {total_ms}ms")

    return issues


def main():
    print("=" * 60)
    print("NYC Dispatch — Demo Scenario Validation")
    print("=" * 60)

    client = httpx.Client(timeout=TIMEOUT)

    try:
        health = client.get(f"{API_BASE}/health")
        if health.status_code != 200:
            print(f"ERROR: API not healthy (HTTP {health.status_code})")
            sys.exit(1)
        print(f"API status: {health.json().get('status')}")
    except Exception as e:
        print(f"ERROR: Cannot connect to API: {e}")
        sys.exit(1)

    scenarios = load_scenarios()
    print(f"\nRunning {len(scenarios)} scenarios...\n")

    all_passed = True
    results = []

    for scenario in scenarios:
        sid = scenario["id"]
        print(f"--- {sid} ---")

        payload = {
            "text": scenario["text"],
            "borough": scenario.get("borough"),
        }
        if scenario.get("image_b64"):
            payload["image_b64"] = scenario["image_b64"]

        t0 = time.perf_counter()
        try:
            resp = client.post(f"{API_BASE}/triage", json=payload)
            elapsed = (time.perf_counter() - t0) * 1000

            if resp.status_code != 200:
                print(f"  FAIL: HTTP {resp.status_code}")
                all_passed = False
                continue

            data = resp.json()
            issues = validate_response(data, scenario.get("expected", {}), sid)

            result_entry = {
                "scenario": sid,
                "status": "PASS" if not issues else "WARN",
                "severity": data.get("severity"),
                "agency": data.get("agency"),
                "category": data.get("category"),
                "confidence": data.get("confidence"),
                "total_ms": data.get("total_ms"),
                "client_ms": round(elapsed, 1),
                "issues": issues,
            }
            results.append(result_entry)

            status_icon = "✅" if not issues else "⚠️"
            print(f"  {status_icon} severity={data.get('severity')} agency={data.get('agency')} "
                  f"confidence={data.get('confidence')} latency={data.get('total_ms')}ms")

            if issues:
                for issue in issues:
                    print(f"     ⚠ {issue}")
                all_passed = False

            print(f"  Summary: {data.get('summary', '')[:100]}...")
            print(f"  Similar: {len(data.get('similar_incidents', []))} incidents")
            print()

        except Exception as e:
            print(f"  ERROR: {e}")
            all_passed = False
            results.append({"scenario": sid, "status": "ERROR", "error": str(e)})
            print()

    client.close()

    print("=" * 60)
    if all_passed:
        print("ALL SCENARIOS PASSED ✅")
    else:
        print("SOME SCENARIOS HAVE ISSUES ⚠️")
    print("=" * 60)

    output_path = Path("validation_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results: {output_path}")


if __name__ == "__main__":
    main()

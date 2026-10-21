"""Small dependency-free concurrency check for a staged deployment."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def request_once(url: str, token: str | None) -> tuple[int, float]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            return response.status, (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded HTTP concurrency smoke test")
    parser.add_argument("base_url", help="Staging origin, for example https://staging.example.com")
    parser.add_argument("--path", default="/api/ready")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--token", help="Optional Supabase access token or uv_ API key")
    args = parser.parse_args()
    total = max(1, min(args.requests, 10_000))
    concurrency = max(1, min(args.concurrency, 200))
    url = args.base_url.rstrip("/") + "/" + args.path.lstrip("/")
    results: list[tuple[int, float]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(request_once, url, args.token) for _ in range(total)]
        for future in as_completed(futures):
            results.append(future.result())
    latencies = sorted(duration for _, duration in results)
    failures = sum(1 for status, _ in results if not 200 <= status < 300)
    percentile_index = min(len(latencies) - 1, int(len(latencies) * 0.95))
    report = {
        "url": url,
        "requests": total,
        "concurrency": concurrency,
        "failures": failures,
        "average_ms": round(statistics.fmean(latencies), 2),
        "p95_ms": round(latencies[percentile_index], 2),
        "max_ms": round(max(latencies), 2),
    }
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

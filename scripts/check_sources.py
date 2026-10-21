#!/usr/bin/env python3
"""Test a few configs from every source through xray and report which sources work.

This is the check behind "does this source actually serve working configs". It takes the first
few links of every source in scripts/sources.txt, runs each one through xray, and prints how many
of them answered. The run is stopped once the time budget is reached.

Usage:
    python3 scripts/check_sources.py            # test the first 3 configs of every source
    python3 scripts/check_sources.py --sample 10
"""

import argparse
import concurrent.futures
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import configlib  # noqa: E402

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
TIME_BUDGET = float(os.environ.get("SOURCE_TIME_BUDGET", "300"))
SAMPLE = int(os.environ.get("SOURCE_SAMPLE", "3"))
WORKERS = int(os.environ.get("WORKERS", "20"))


def sample_links(url: str, count: int) -> list[str]:
    """The first links of a source. An empty list means the source cannot be read."""
    try:
        body = configlib.decode_if_base64(configlib.fetch(url, timeout=20))
    except Exception as exc:
        print(f"[warn] could not fetch {url}: {exc}", file=sys.stderr)
        return []
    links = [line.strip() for line in body.splitlines()
             if configlib.PROTOCOL_RE.match(line.strip())]
    return links[:count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=SAMPLE,
                        help="configs to test per source (default 3)")
    args = parser.parse_args()

    sources = configlib.load_sources()
    pairs = []
    for url in sources:
        for link in sample_links(url, args.sample):
            pairs.append((url, link))
    print(f"testing {len(pairs)} links from {len(sources)} sources", flush=True)

    tally, started = {}, time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(configlib.test_link, link): url for url, link in pairs}
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=TIME_BUDGET):
                url = futures[fut]
                link, delay, host, tier = fut.result()
                entry = tally.setdefault(url, [0, 0])
                entry[1] += 1
                if delay is not None:
                    entry[0] += 1
                    print(f"ok   {delay:7.0f}ms  {host:<40} {url}", flush=True)
        except concurrent.futures.TimeoutError:
            print(f"[info] time budget of {TIME_BUDGET:.0f}s reached", file=sys.stderr)

    working_sources = 0
    for url in sources:
        entry = tally.get(url)
        if not entry:
            print(f"  ?  {url}: no config was tested")
            continue
        if entry[0]:
            working_sources += 1
        print(f"  {entry[0]}/{entry[1]}  {url}")
    print(f"\n{working_sources} of {len(sources)} sources serve a working config "
          f"({time.time() - started:.0f}s)")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "source_working_check.json"), "w", encoding="utf-8") as f:
        json.dump({
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sources": {url: {"working": v[0], "tested": v[1]} for url, v in tally.items()},
        }, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Test a few configs from every Telegram channel through xray and report which channels work.

This is the check behind "does this channel actually serve working configs". It takes the first
few links of every channel in scripts/channels.txt, runs each one through xray, and prints how
many of them answered. The run is stopped once the time budget is reached.

Usage:
    python3 scripts/check_channels.py            # test the first 3 configs of every channel
    python3 scripts/check_channels.py --sample 10
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
TIME_BUDGET = float(os.environ.get("CHANNEL_TIME_BUDGET", "300"))
SAMPLE = int(os.environ.get("CHANNEL_SAMPLE", "3"))
WORKERS = int(os.environ.get("WORKERS", "20"))


def sample_links(channel: str, count: int) -> list[str]:
    """The first config links of a channel. An empty list means the channel cannot be read."""
    try:
        body = configlib.fetch(f"https://t.me/s/{channel}", timeout=configlib.TELEGRAM_TIMEOUT)
    except Exception as exc:
        print(f"[warn] could not read @{channel}: {exc}", file=sys.stderr)
        return []
    texts = configlib.telegram_post_texts(body)
    links = [configlib._trim_tg_link(m.group(0))
             for text in texts for m in configlib.TG_LINK_RE.finditer(text)]
    return links[:count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=SAMPLE,
                        help="configs to test per channel (default 3)")
    args = parser.parse_args()

    channels = configlib.load_channels()
    pairs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=configlib.TELEGRAM_WORKERS) as pool:
        for channel, links in zip(channels, pool.map(
                lambda c: sample_links(c, args.sample), channels)):
            for link in links:
                pairs.append((channel, link))
    print(f"testing {len(pairs)} links from {len(channels)} channels", flush=True)

    tally, started = {}, time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(configlib.test_link, link): channel for channel, link in pairs}
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=TIME_BUDGET):
                channel = futures[fut]
                link, delay, host, tier = fut.result()
                entry = tally.setdefault(channel, [0, 0])
                entry[1] += 1
                if delay is not None:
                    entry[0] += 1
                    print(f"ok   {delay:7.0f}ms  {host:<40} @{channel}", flush=True)
        except concurrent.futures.TimeoutError:
            print(f"[info] time budget of {TIME_BUDGET:.0f}s reached", file=sys.stderr)

    working_channels = 0
    for channel in channels:
        entry = tally.get(channel)
        if not entry:
            print(f"  ?  @{channel}: no config was tested")
            continue
        if entry[0]:
            working_channels += 1
        print(f"  {entry[0]}/{entry[1]}  @{channel}")
    print(f"\n{working_channels} of {len(channels)} channels serve a working config "
          f"({time.time() - started:.0f}s)")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "channel_working_check.json"), "w", encoding="utf-8") as f:
        json.dump({
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "channels": {channel: {"working": v[0], "tested": v[1]}
                         for channel, v in tally.items()},
        }, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

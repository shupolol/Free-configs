#!/usr/bin/env python3
"""Test the configs already in output/ and report which ones still connect.

Ignores the test cache, so every result is fresh. Writes output/existing_check.json and prints a summary.
Usage: python scripts/check_existing.py [file ...]   (defaults to all_configs.txt and near_iran_secure.txt)
"""
import concurrent.futures
import json
import os
import sys
import time
from urllib.parse import unquote

import configlib as c

DEFAULT_FILES = ["all_configs.txt", "near_iran_secure.txt"]


def display_name(link: str) -> str:
    if link.lower().startswith("vmess://"):
        try:
            return c.b64_json(link.split("://", 1)[1].split("#", 1)[0]).get("ps", "")
        except Exception:
            return ""
    return unquote(link.split("#", 1)[1]) if "#" in link else ""


def main() -> None:
    files = sys.argv[1:] or DEFAULT_FILES
    report = {}
    for name in files:
        links = c.load_list(os.path.join(c.OUTPUT_DIR, name))
        print(f"{name}: testing {len(links)} configs")
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=c.WORKERS) as pool:
            for link, delay, host, tier in pool.map(c.test_link, links):
                rows.append({"name": display_name(link),
                             "host": host, "tier": tier,
                             "ok": delay is not None,
                             "delay_ms": round(delay, 1) if delay is not None else None})
        ok = sum(r["ok"] for r in rows)
        print(f"  {ok} / {len(rows)} still work")
        report[name] = {"total": len(rows), "working": ok, "configs": rows}

    path = os.path.join(c.OUTPUT_DIR, "existing_check.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **report}, f, indent=2)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()

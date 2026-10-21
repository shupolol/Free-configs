#!/usr/bin/env python3
"""Check every Telegram channel in scripts/channels.txt and keep that list up to date.

A channel counts as working when its public preview (t.me/s/<name>) can be read and holds at
least one config link. The check runs in parallel and stays inside a time budget, so a run
costs about as long as the slowest channel instead of the sum of all of them.

Usage:
    python3 scripts/refresh_channels.py            # check only, write output/channel_status.json
    python3 scripts/refresh_channels.py --update   # also rewrite scripts/channels.txt
    python3 scripts/refresh_channels.py --check    # fail when a channel is broken
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
# The whole check stays under five minutes, and every channel is read in parallel.
TIME_BUDGET = float(os.environ.get("CHANNEL_TIME_BUDGET", "300"))
FETCH_TIMEOUT = float(os.environ.get("CHANNEL_FETCH_TIMEOUT", "20"))
WORKERS = int(os.environ.get("CHANNEL_WORKERS", "16"))

# Channels that are not in scripts/channels.txt yet. They are checked on every run and added
# to the file when they work.
CANDIDATES = [
    "v2rayng_fast",
    "v2rayNG_Matsuri",
    "V2ray_Configs",
    "v2ray_fre",
    "ManVPN",
    "v2ray24",
    "filembad",
    "ProxyMtpVPN",
    "canfingV2rayNG",
    "v2Line",
    "v2ray_configs_pool",
    "shadowproxy66",
    "V2rayCollectorDonate",
    "abc_configs",
    "OnlineVpnConfigs",
    "mellvpn",
    "blackRay",
    "Config_magazine",
    "NetMeli9",
    "XpnTeam",
]


def check_channel(name: str) -> dict:
    """Read a channel preview and count the config links in it."""
    result = {"channel": name, "ok": False, "links": 0, "posts": 0, "error": ""}
    try:
        body = configlib.fetch(f"https://t.me/s/{name}", timeout=FETCH_TIMEOUT)
    except Exception as exc:
        result["error"] = str(exc)
        return result
    if configlib.TG_BLOCKED_RE.search(body):
        result["error"] = "no public preview"
        return result
    texts = configlib.telegram_post_texts(body)
    result["posts"] = len(texts)
    links = [configlib._trim_tg_link(m.group(0))
             for text in texts for m in configlib.TG_LINK_RE.finditer(text)]
    result["links"] = len(links)
    if not links:
        result["error"] = "no config links in the preview"
        return result
    result["ok"] = True
    return result


def write_channels(path: str, names: list[str]) -> None:
    header = [
        "# Telegram channels the configs come from. One channel name per line.",
        "# Only channels with a public preview (t.me/s/<name>) can be read.",
        "# Kept up to date by scripts/refresh_channels.py, which drops the channels that stopped",
        "# posting configs and adds new ones that work.",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + names) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="rewrite scripts/channels.txt with the working channels")
    parser.add_argument("--check", action="store_true",
                        help="exit with a non-zero status when a channel is broken")
    args = parser.parse_args()

    current = configlib.load_channels()
    names = list(dict.fromkeys(current + CANDIDATES))

    started = time.time()
    results = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)
    futures = {pool.submit(check_channel, name): name for name in names}
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=TIME_BUDGET):
            entry = fut.result()
            results.append(entry)
            state = "ok " if entry["ok"] else "FAIL"
            print(f"[{state}] {entry['links']:>5} links  @{entry['channel']}  {entry['error']}")
    except concurrent.futures.TimeoutError:
        print(f"[info] time budget of {TIME_BUDGET:.0f}s reached; "
              f"{len(futures) - len(results)} channels left unchecked", file=sys.stderr)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = time.time() - started

    by_name = {r["channel"]: r for r in results}
    results = [by_name[n] for n in names if n in by_name]
    unchecked = [n for n in names if n not in by_name]

    working = [r["channel"] for r in results if r["ok"]]
    broken = [r["channel"] for r in results if not r["ok"]]
    print(f"\n{len(working)} working, {len(broken)} broken, {len(unchecked)} unchecked "
          f"in {elapsed:.0f}s")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    status = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(names),
        "working": len(working),
        "broken": len(broken),
        "unchecked": unchecked,
        "elapsed_seconds": round(elapsed, 1),
        "channels": results,
    }
    with open(os.path.join(OUTPUT_DIR, "channel_status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

    if args.update:
        # A channel that was not reached within the time budget stays in the file, since a
        # timeout is not proof that the channel is dead.
        keep = [n for n in current if n in set(working) or n in set(unchecked)]
        added = [n for n in working if n not in set(current)]
        write_channels(configlib.CHANNELS_FILE, keep + added)
        print(f"wrote {len(keep) + len(added)} channels to {configlib.CHANNELS_FILE} "
              f"({len(added)} added)")

    if args.check and broken:
        print(f"broken channels: {len(broken)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

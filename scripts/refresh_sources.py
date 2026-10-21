#!/usr/bin/env python3
"""Check every subscription source and keep scripts/sources.txt in sync.

A source counts as working when it can be fetched, decoded if it is base64, and yields at least
one config line that this repository can parse. The check fetches one line of config, not the
whole file, so a large subscription costs one request and one line of memory.

Usage:
    python3 scripts/refresh_sources.py            # check only, write output/source_status.json
    python3 scripts/refresh_sources.py --update   # also rewrite scripts/sources.txt
    python3 scripts/refresh_sources.py --check    # fail if any source is broken
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
# The whole check is kept under five minutes, and every source is fetched in parallel, so the
# run costs about as long as the slowest source instead of the sum of all of them.
TIME_BUDGET = float(os.environ.get("SOURCE_TIME_BUDGET", "300"))
FETCH_TIMEOUT = float(os.environ.get("SOURCE_FETCH_TIMEOUT", "20"))
WORKERS = int(os.environ.get("SOURCE_WORKERS", "16"))

# Extra subscriptions that are not in scripts/sources.txt yet. They are checked on every run and
# added to the file when they work.
CANDIDATES = [
    "https://raw.githubusercontent.com/nyeinkokoaung404/V2ray-Configs/main/Sub8.txt",
    "https://raw.githubusercontent.com/nyeinkokoaung404/V2ray-Configs/main/Splitted-By-Protocol/vless.txt",
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/hysteria2.txt",
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/splitted-by-protocol/vless.txt",
    "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/main/mci/sub_2.txt",
    "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/main/mtn/sub_2.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub1.txt",
    "https://raw.githubusercontent.com/zhangdunlong/free-v2ray-nodes/main/nodes_base64.txt",
]


def check_source(url: str) -> dict:
    """Fetch a source and confirm it serves at least one parsable config link.

    Only the first slice of the file is requested, because one config line is all the check
    needs. A source that keeps nothing in its first slice is fetched in full as a second try,
    so a subscription that starts with a long comment is still checked.

    The first line of a subscription is not always usable, so a few lines are tried before the
    source is called broken. Only one line is parsed per link, which keeps the check cheap."""
    result = {"url": url, "ok": False, "links": 0, "parsed": 0, "first_link": "", "error": ""}
    body = ""
    for headers in ({"Range": "bytes=0-65535"}, None):
        try:
            body = configlib.decode_if_base64(
                configlib.fetch(url, timeout=FETCH_TIMEOUT, headers=headers))
        except Exception as exc:
            if "416" in str(exc):
                result["error"] = "file is empty"
            else:
                result["error"] = str(exc)
            continue
        if configlib.PROTOCOL_RE.search(body):
            break
    if not body:
        return result
    links = [line.strip() for line in body.splitlines()
             if configlib.PROTOCOL_RE.match(line.strip())]
    result["links"] = len(links)
    if not links:
        result["error"] = "no config links in response"
        return result
    for link in links[:25]:
        try:
            if configlib.build_outbound(link) is None:
                continue
        except Exception:
            continue
        result["parsed"] += 1
        if not result["first_link"]:
            result["first_link"] = link[:120]
    if not result["parsed"]:
        result["error"] = "no config in the first 25 links could be parsed"
        return result
    result["ok"] = True
    return result


def write_sources(path: str, urls: list[str]) -> None:
    header = [
        "# Subscription sources used by the builders.",
        "# One URL per line. Blank lines and lines starting with \"#\" are ignored.",
        "# Managed by scripts/refresh_sources.py, which drops dead URLs and adds new working ones.",
        "# The URL may carry a \"#label\" suffix; only the part before the first \"#\" is fetched.",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + urls) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="rewrite scripts/sources.txt with the working sources")
    parser.add_argument("--check", action="store_true",
                        help="exit with a non-zero status when a source is broken")
    args = parser.parse_args()

    current = configlib.load_sources()
    urls = list(dict.fromkeys(current + CANDIDATES))

    started = time.time()
    results = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)
    futures = {pool.submit(check_source, url): url for url in urls}
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=TIME_BUDGET):
            entry = fut.result()
            results.append(entry)
            state = "ok " if entry["ok"] else "FAIL"
            print(f"[{state}] {entry['links']:>6} links  {entry['url']}  {entry['error']}")
    except concurrent.futures.TimeoutError:
        print(f"[info] time budget of {TIME_BUDGET:.0f}s reached; "
              f"{len(futures) - len(results)} sources left unchecked", file=sys.stderr)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = time.time() - started

    # Keep the order of the source list in the report, so it reads the same way every run.
    by_url = {r["url"]: r for r in results}
    results = [by_url[u] for u in urls if u in by_url]
    unchecked = [u for u in urls if u not in by_url]

    working = [r["url"] for r in results if r["ok"]]
    broken = [r["url"] for r in results if not r["ok"]]
    print(f"\n{len(working)} working, {len(broken)} broken, {len(unchecked)} unchecked "
          f"in {elapsed:.0f}s")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    status = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(urls),
        "working": len(working),
        "broken": len(broken),
        "unchecked": unchecked,
        "elapsed_seconds": round(elapsed, 1),
        "sources": results,
    }
    with open(os.path.join(OUTPUT_DIR, "source_status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

    if args.update:
        # Keep the sources that are listed today and still work. A source that was not reached
        # within the time budget stays in the file, since a timeout is not proof that it is dead.
        keep = [u for u in current if u in set(working) or u in set(unchecked)]
        added = [u for u in working if u not in set(current)]
        write_sources(configlib.SOURCES_FILE, keep + added)
        print(f"wrote {len(keep) + len(added)} sources to {configlib.SOURCES_FILE} "
              f"({len(added)} added)")

    if args.check and broken:
        print(f"broken sources: {len(broken)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

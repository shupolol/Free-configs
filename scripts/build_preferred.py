#!/usr/bin/env python3
"""Build output/preferred_secure.txt: up to PREFERRED_TOP_N working configs, ranked by the country
order in PREFERRED_COUNTRIES, then by transport security, then by latency.

Retests the configs from the previous run first, keeps the ones that still work, then adds any
new working configs found in this run. Also writes working_configs.txt, the file existing
subscriptions point at.
"""
import os

import configlib as c

PREFERRED_TOP_N = int(os.environ.get("PREFERRED_TOP_N", "200"))
FILENAME = "preferred_secure.txt"


def main() -> None:
    previous = c.load_list(os.path.join(c.OUTPUT_DIR, FILENAME))
    candidates = c.collect_candidates(previous)
    print(f"Testing up to {len(candidates)} configs within {c.TIME_BUDGET:.0f}s")

    results, tested = c.test_within_budget(candidates)
    working_keys = {c.link_key(link) for _, _, link, _ in results}
    previous_keys = {c.link_key(link) for link in previous}
    kept = len(working_keys & previous_keys)
    print(f"Working: {len(results)} / {tested} tested ({kept} from previous run still work)")

    geo = c.geolocate(list({host for _, _, _, host in results}))
    located = [r for r in results if r[3] in geo]
    print(f"Dropped {len(results) - len(located)} working configs with unknown country")

    ranked = sorted(located, key=lambda r: (c.proximity_rank(geo[r[3]][0]), r[0], r[1]))[:PREFERRED_TOP_N]
    named = []
    for n, (tier, delay, link, host) in enumerate(ranked, start=1):
        code, country = geo[host]
        named.append(c.rename(link, f"{c.flag_for(code)} {country} {n:03d}"))
    c.write_list(FILENAME, named)
    c.write_list("working_configs.txt", named)

    c.update_stats("preferred_secure", {
        "tested": tested,
        "candidates": len(candidates),
        "working": len(located),
        "written": len(named),
        "kept_from_previous": kept,
        "preferred_countries": c.PREFERRED_COUNTRIES,
        "telegram_channels": c.TELEGRAM_CHANNELS,
    })
    print(f"Wrote {len(named)} configs to {FILENAME} and working_configs.txt")


if __name__ == "__main__":
    main()

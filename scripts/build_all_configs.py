#!/usr/bin/env python3
import os

import configlib as c

ALL_TOP_N = int(os.environ.get("ALL_TOP_N", "2000"))
FILENAME = "all_configs.txt"


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

    ranked = sorted(located, key=lambda r: (r[0], r[1]))[:ALL_TOP_N]
    named = []
    for n, (tier, delay, link, host) in enumerate(ranked, start=1):
        code, country = geo[host]
        named.append(c.rename(link, f"{c.flag_for(code)} {country} {n:03d}"))
    c.write_list(FILENAME, named)

    c.update_stats("all_configs", {
        "tested": tested,
        "candidates": len(candidates),
        "working": len(located),
        "written": len(named),
        "kept_from_previous": kept,
        "telegram_channels": c.TELEGRAM_CHANNELS,
    })
    print(f"Wrote {len(named)} configs to {FILENAME}")


if __name__ == "__main__":
    main()

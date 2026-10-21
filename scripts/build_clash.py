#!/usr/bin/env python3
"""Build output/flclash.yaml: a ready-to-import Clash Meta (Mihomo) profile for FlClash and friends.

The input is an already-tested list, so every node in the profile is one that connected during
this run; nothing is added that was never probed. Duplicates of the same server are collapsed
and the nodes Clash Meta cannot represent are dropped, with the counts reported.
"""
import os
import re
import sys
from urllib.parse import unquote

import clashlib as cl
import configlib as c

# Which tested list to convert. all_configs.txt is the full ranked list built by build_all_configs.py.
SOURCE_FILE = os.environ.get("CLASH_SOURCE", "all_configs.txt")
FILENAME = "flclash.yaml"
# Nodes offered in the automatic latency group. Every node stays in the manual group.
AUTO_TOP_N = max(1, int(os.environ.get("CLASH_AUTO_TOP_N", "60")))
AUTO_URL = os.environ.get("CLASH_AUTO_URL", c.TEST_URL)

PROXY_GROUP = "PROXY"
AUTO_GROUP = "AUTO"


def display_name(link: str, fallback: str) -> str:
    """The name a config was published under, so nodes stay recognisable in the client."""
    try:
        if link.lower().startswith("vmess://"):
            name = c.b64_json(link.split("://", 1)[1].split("#", 1)[0]).get("ps", "")
        else:
            name = unquote(link.split("#", 1)[1]) if "#" in link else ""
    except Exception:
        name = ""
    name = (name or fallback).strip()
    # A comma would collide with the "MATCH,GROUP" rule syntax, and mihomo wants unique names.
    return re.sub(r"[\r\n\t,]+", " ", name) or fallback


def build_profile(proxies: list[dict]) -> dict:
    names = [p["name"] for p in proxies]
    return {
        "mixed-port": 7890,
        "allow-lan": False,
        "bind-address": "*",
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "external-controller": "127.0.0.1:9090",
        "unified-delay": True,
        "tcp-concurrent": True,
        "global-client-fingerprint": "chrome",
        "profile": {"store-selected": True, "store-fake-ip": True},
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "nameserver": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
        },
        "proxies": proxies,
        "proxy-groups": [
            {"name": AUTO_GROUP, "type": "url-test", "url": AUTO_URL,
             "interval": 300, "tolerance": 50, "lazy": False, "proxies": names[:AUTO_TOP_N]},
            {"name": PROXY_GROUP, "type": "select", "proxies": [AUTO_GROUP] + names},
        ],
        "rules": [
            "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
            "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
            "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
            "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
            "IP-CIDR,169.254.0.0/16,DIRECT,no-resolve",
            "IP-CIDR6,::1/128,DIRECT,no-resolve",
            "IP-CIDR6,fc00::/7,DIRECT,no-resolve",
            "IP-CIDR6,fe80::/10,DIRECT,no-resolve",
            "GEOIP,private,DIRECT,no-resolve",
            f"MATCH,{PROXY_GROUP}",
        ],
    }


def main() -> None:
    source = os.path.join(c.OUTPUT_DIR, SOURCE_FILE)
    links = c.load_list(source)
    if not links:
        print(f"{source} has no configs. Run scripts/build_all_configs.py first.", file=sys.stderr)
        raise SystemExit(1)
    print(f"Converting {len(links)} tested configs from {SOURCE_FILE} to Clash Meta format")

    proxies, seen = [], set()
    converted = duplicates = unsupported = 0
    for n, link in enumerate(links, start=1):
        proxy = cl.to_mihomo_proxy(link, display_name(link, f"node-{n:03d}"))
        if proxy is None:
            unsupported += 1
            continue
        identity = cl.proxy_identity(proxy)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        proxies.append(proxy)
        converted += 1

    if not proxies:
        print("No config could be converted to Clash Meta format.", file=sys.stderr)
        raise SystemExit(1)
    if len({p["name"] for p in proxies}) != len(proxies):
        names, unique = set(), []
        for proxy in proxies:
            name, suffix = proxy["name"], 2
            while name in names:
                name, suffix = f"{proxy['name']} ({suffix})", suffix + 1
            names.add(name)
            proxy["name"] = name
            unique.append(proxy)
        proxies = unique

    os.makedirs(c.OUTPUT_DIR, exist_ok=True)
    text = cl.to_yaml(build_profile(proxies))
    with open(os.path.join(c.OUTPUT_DIR, FILENAME), "w", encoding="utf-8") as f:
        f.write(text)

    skipped = cl.skip_summary()
    print(f"Skipped {unsupported} configs Clash Meta cannot represent" +
          (f": {', '.join(f'{k} x{v}' for k, v in skipped.items())}" if skipped else ""))
    print(f"Collapsed {duplicates} duplicate servers")
    print(f"Wrote {len(proxies)} proxies to {FILENAME}")

    c.update_stats("flclash", {
        "source": SOURCE_FILE,
        "input": len(links),
        "written": len(proxies),
        "duplicates": duplicates,
        "unsupported": unsupported,
        "skipped_reasons": skipped,
        "auto_group": min(AUTO_TOP_N, len(proxies)),
        "telegram_channels": c.TELEGRAM_CHANNELS,
    })


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Turn every config in the MahsaFreeConfig repository into readable V2Ray configs.

The repository keeps its subscriptions as base64 blobs, so a client needs to decode them first.
This script downloads the repository, decodes every file it can, and writes the configs in three
forms:

    output/mahsa_configs.txt       share links, one per line, ready for a subscription URL
    output/mahsa_readable.txt      the same configs as readable text, field by field
    output/mahsa_xray_configs.json xray configurations, one object per config, ready to import

Files that hold no share links (the encrypted app/sub.txt, the wireguard list, the Telegram
index) are reported in output/mahsa_stats.json instead of being silently dropped.
"""

import base64
import io
import json
import os
import sys
import tarfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import configlib  # noqa: E402

REPO = "mahsanet/MahsaFreeConfig"
TARBALL = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/main"
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
PROTOCOL_RE = configlib.PROTOCOL_RE


def fetch_repo() -> dict[str, bytes]:
    """Download the repository tarball and return every file in it, keyed by path."""
    req = urllib.request.Request(TARBALL, headers=configlib.BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        blob = resp.read()
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            name = member.name.split("/", 1)[-1]
            files[name] = handle.read()
    return files


def links_from_text(text: str) -> list[str]:
    """Config links in a text file, decoding base64 when the file is a base64 subscription."""
    decoded = configlib.decode_if_base64(text)
    return [line.strip() for line in decoded.splitlines() if PROTOCOL_RE.match(line.strip())]


PROXY_PROTOCOLS = ("vmess", "vless", "trojan", "shadowsocks")


def outbounds_from_fragment(raw: bytes) -> list[dict]:
    """Proxy outbounds of an xray fragment file. Freedom, blackhole and dns outbounds are not
    configs a client can subscribe to, so they are left out."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if isinstance(data, dict):
        outbounds = data.get("outbounds", [])
    elif isinstance(data, list):
        outbounds = data
    else:
        return []
    return [o for o in outbounds if isinstance(o, dict)
            and o.get("protocol") in PROXY_PROTOCOLS]


def describe_outbound(outbound: dict) -> str:
    """Readable one-line description of an xray outbound."""
    stream = outbound.get("streamSettings", {})
    settings = outbound.get("settings", {})
    protocol = outbound.get("protocol", "?")
    if "vnext" in settings and settings["vnext"]:
        entry = settings["vnext"][0]
        endpoint = f"{entry.get('address')}:{entry.get('port')}"
        credential = (entry.get("users") or [{}])[0].get("id", "")
    elif "servers" in settings and settings["servers"]:
        entry = settings["servers"][0]
        endpoint = f"{entry.get('address')}:{entry.get('port')}"
        credential = entry.get("password", "")
        if entry.get("method"):
            credential = f"{entry['method']}:{credential}"
    else:
        endpoint, credential = "?", ""
    parts = [f"protocol: {protocol}", f"server: {endpoint}",
             f"transport: {stream.get('network', 'tcp')}"]
    security = stream.get("security", "none")
    if security != "none":
        parts.append(f"security: {security}")
    for key in ("tlsSettings", "realitySettings"):
        tls = stream.get(key) or {}
        if tls.get("serverName"):
            parts.append(f"sni: {tls['serverName']}")
        if tls.get("allowInsecure"):
            parts.append("allowInsecure: true")
        if tls.get("publicKey"):
            parts.append(f"publicKey: {tls['publicKey']}")
    if stream.get("wsSettings", {}).get("path"):
        parts.append(f"path: {stream['wsSettings']['path']}")
    if stream.get("grpcSettings", {}).get("serviceName"):
        parts.append(f"serviceName: {stream['grpcSettings']['serviceName']}")
    if credential:
        parts.append(f"credential: {credential}")
    return " | ".join(parts)


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    try:
        files = fetch_repo()
    except Exception as exc:
        print(f"[warn] could not download {REPO}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"{REPO}: {len(files)} files")

    links, link_origin, fragments = [], [], []
    skipped = {}

    for name, raw in sorted(files.items()):
        if name.endswith(".txt"):
            try:
                text = raw.decode("utf-8", errors="ignore")
            except Exception:
                continue
            found = links_from_text(text)
            if found:
                for link in found:
                    links.append(link)
                    link_origin.append(name)
                print(f"{name}: {len(found)} configs")
            else:
                skipped[name] = "no share links (encrypted or not a share-link file)"
        elif name.endswith(".json"):
            outbounds = outbounds_from_fragment(raw)
            if outbounds:
                fragments.append((name, outbounds))
                print(f"{name}: {len(outbounds)} xray outbounds")

    # A share link that appears twice in the repository is still one config.
    seen, unique_links, unique_origin = set(), [], []
    for link, origin in zip(links, link_origin):
        key = configlib.link_key(link)
        if key in seen:
            continue
        seen.add(key)
        unique_links.append(link)
        unique_origin.append(origin)
    print(f"{len(unique_links)} unique configs from {len(links)} links "
          f"in {len(set(link_origin))} files")

    readable, xray_configs = [], []
    for index, (link, origin) in enumerate(zip(unique_links, unique_origin), start=1):
        try:
            outbound = configlib.build_outbound(link)
        except Exception:
            outbound = None
        name = f"Mahsa {index:03d} ({origin})"
        if outbound is None:
            readable.append(f"{name}: unreadable link")
            continue
        outbound["tag"] = "proxy"
        readable.append(f"{name}: {configlib.describe_link(link)}")
        xray_configs.append({
            "name": name,
            "source": origin,
            "link": link,
            "config": {
                "log": {"loglevel": "warning"},
                "inbounds": [{"listen": "127.0.0.1", "port": 10808, "protocol": "socks",
                              "settings": {"auth": "noauth", "udp": False}}],
                "outbounds": [outbound],
            },
        })

    for name, outbounds in fragments:
        for index, outbound in enumerate(outbounds, start=1):
            outbound = dict(outbound)
            outbound["tag"] = f"proxy-{index}"
            label = f"Mahsa fragment {name} #{index}"
            readable.append(f"{label}: {describe_outbound(outbound)}")
            xray_configs.append({
                "name": label,
                "source": name,
                "link": "",
                "config": {
                    "log": {"loglevel": "warning"},
                    "inbounds": [{"listen": "127.0.0.1", "port": 10808, "protocol": "socks",
                                  "settings": {"auth": "noauth", "udp": False}}],
                    "outbounds": [outbound],
                },
            })

    with open(os.path.join(OUTPUT_DIR, "mahsa_configs.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(unique_links) + ("\n" if unique_links else ""))
    with open(os.path.join(OUTPUT_DIR, "mahsa_readable.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(readable) + ("\n" if readable else ""))
    with open(os.path.join(OUTPUT_DIR, "mahsa_xray_configs.json"), "w", encoding="utf-8") as f:
        json.dump(xray_configs, f, indent=2, ensure_ascii=False)

    stats = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo": REPO,
        "files_read": len(files),
        "links": len(unique_links),
        "readable": len(readable),
        "xray_configs": len(xray_configs),
        "skipped": skipped,
    }
    with open(os.path.join(OUTPUT_DIR, "mahsa_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    print(f"wrote {len(unique_links)} configs, {len(readable)} readable lines, "
          f"{len(xray_configs)} xray configs")


if __name__ == "__main__":
    main()

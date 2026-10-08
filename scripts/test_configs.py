#!/usr/bin/env python3
"""Fetch V2Ray configs from public sources, test them with xray, geolocate survivors, and rename them."""

import base64
import concurrent.futures
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import parse_qs, quote, unquote, urlsplit

SOURCES = [
    "https://raw.githubusercontent.com/patterniha/Free-Configs/main/configs.txt#Patterniha-F",
    "https://github.com/Epodonios/v2ray-configs/raw/main/All_Configs_Sub.txt",
    "https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list/refs/heads/main/all_extracted_configs.txt",
]

PROTOCOL_RE = re.compile(r"^(vmess|vless|trojan|ss)://", re.IGNORECASE)
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
XRAY_BIN = os.environ.get("XRAY_BIN", "xray")
PER_SOURCE = int(os.environ.get("PER_SOURCE", "333"))
TOP_N = int(os.environ.get("TOP_N", "200"))
DEBUG_FAILURES = int(os.environ.get("DEBUG_FAILURES", "20"))
WORKERS = int(os.environ.get("WORKERS", "24"))
TEST_URL = os.environ.get("TEST_URL", "https://www.gstatic.com/generate_204")
TIMEOUT = float(os.environ.get("TEST_TIMEOUT", "8"))
PROBES = int(os.environ.get("PROBES", "3"))
GEO_URL = "http://ip-api.com/batch"


def fetch(url: str) -> str:
    url = url.split("#", 1)[0]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def decode_if_base64(text: str) -> str:
    stripped = "".join(text.split())
    if stripped and not PROTOCOL_RE.search(text):
        try:
            padded = stripped + "=" * (-len(stripped) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if PROTOCOL_RE.search(decoded):
                return decoded
        except (ValueError, base64.binascii.Error):
            pass
    return text


def collect_sampled_links() -> list[str]:
    """Take up to PER_SOURCE random links from each source, dropping duplicates across sources."""
    seen, links = set(), []
    for src in SOURCES:
        try:
            body = decode_if_base64(fetch(src))
        except Exception as exc:
            print(f"[warn] could not fetch {src}: {exc}", file=sys.stderr)
            continue
        source_links = list(dict.fromkeys(
            line.strip() for line in body.splitlines() if PROTOCOL_RE.match(line.strip())))
        print(f"{src.split('#')[0]}: {len(source_links)} links")
        sample = random.sample(source_links, min(PER_SOURCE, len(source_links)))
        for link in sample:
            if link not in seen:
                seen.add(link)
                links.append(link)
    return links


def b64_json(payload: str) -> dict:
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def build_outbound(link: str) -> dict | None:
    """Convert a share link into an xray outbound object. Returns None if unsupported."""
    scheme = link.split("://", 1)[0].lower()
    body = link.split("://", 1)[1].split("#", 1)[0]

    if scheme == "vmess":
        v = b64_json(body)
        net = v.get("net", "tcp")
        stream = {"network": net, "security": v.get("tls", "") or "none"}
        if stream["security"] == "tls":
            stream["tlsSettings"] = {"serverName": v.get("sni") or v.get("host", "")}
        if net == "ws":
            stream["wsSettings"] = {"path": v.get("path", "/"), "headers": {"Host": v.get("host", "")}}
        elif net == "grpc":
            stream["grpcSettings"] = {"serviceName": v.get("path", "")}
        return {
            "protocol": "vmess",
            "settings": {"vnext": [{"address": v["add"], "port": int(v["port"]),
                                    "users": [{"id": v["id"], "alterId": int(v.get("aid", 0)),
                                               "security": "auto"}]}]},
            "streamSettings": stream,
        }

    if scheme == "ss":
        if "@" not in body:
            return None
        userinfo, hostport = body.rsplit("@", 1)
        try:
            method_pass = base64.urlsafe_b64decode(userinfo + "=" * (-len(userinfo) % 4)).decode()
        except Exception:
            method_pass = unquote(userinfo)
        method, password = method_pass.split(":", 1)
        host, _, port_str = hostport.rpartition(":")
        return {"protocol": "shadowsocks",
                "settings": {"servers": [{"address": host, "port": int(port_str),
                                          "method": method, "password": password}]}}

    parts = urlsplit(link)
    host, port = parts.hostname, parts.port
    if not host or not port:
        return None
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}

    if scheme in ("vless", "trojan"):
        user = unquote(parts.username or "")
        if scheme == "vless":
            protocol = "vless"
            settings = {"vnext": [{"address": host, "port": port,
                                   "users": [{"id": user, "encryption": "none", "flow": q.get("flow", "")}]}]}
        else:
            protocol = "trojan"
            settings = {"servers": [{"address": host, "port": port, "password": user}]}
        net = q.get("type", "tcp")
        security = q.get("security", "none")
        stream = {"network": net, "security": security}
        if security == "tls":
            stream["tlsSettings"] = {"serverName": q.get("sni", host), "allowInsecure": True}
        if security == "reality":
            stream["realitySettings"] = {"serverName": q.get("sni", host),
                                         "publicKey": q.get("pbk", ""), "shortId": q.get("sid", ""),
                                         "fingerprint": q.get("fp", "chrome")}
        if net == "ws":
            stream["wsSettings"] = {"path": q.get("path", "/"), "headers": {"Host": q.get("host", host)}}
        elif net == "grpc":
            stream["grpcSettings"] = {"serviceName": q.get("serviceName", "")}
        return {"protocol": protocol, "settings": settings, "streamSettings": stream}
    return None


def transport_tier(outbound: dict) -> int:
    """Lower is better. Reality and TLS-with-WebSocket/gRPC are harder for censors to block than plain transports."""
    stream = outbound.get("streamSettings", {})
    security = stream.get("security", "none")
    network = stream.get("network", "tcp")
    if security == "reality":
        return 0
    if security == "tls" and network in ("ws", "grpc"):
        return 1
    if security == "tls":
        return 2
    return 3


def outbound_host(outbound: dict) -> str:
    settings = outbound["settings"]
    entry = settings["vnext"][0] if "vnext" in settings else settings["servers"][0]
    return entry["address"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, deadline: float = 5.0) -> bool:
    end = time.time() + deadline
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def probe_delay(port: int) -> float | None:
    """One real HTTP round trip through the proxy. Returns milliseconds to first byte."""
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{time_starttransfer}",
         "--max-time", str(TIMEOUT), "--socks5-hostname", f"127.0.0.1:{port}", TEST_URL],
        capture_output=True, text=True)
    parts = result.stdout.split()
    if len(parts) != 2 or parts[0] not in ("204", "200"):
        return None
    return float(parts[1]) * 1000


_failure_count = 0


def report_failure(reason: str, host: str) -> None:
    global _failure_count
    _failure_count += 1
    if _failure_count <= DEBUG_FAILURES:
        print(f"[fail] {host}: {reason}")


def test_link(link: str) -> tuple[str, float | None, str | None, int | None]:
    try:
        outbound = build_outbound(link)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return link, None, None, None
    if outbound is None:
        return link, None, None, None
    host = outbound_host(outbound)
    tier = transport_tier(outbound)
    outbound["tag"] = "proxy"
    port = free_port()
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                      "settings": {"udp": False}}],
        "outbounds": [outbound],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        cfg_path = f.name
    with tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen([XRAY_BIN, "run", "-c", cfg_path],
                                stdout=subprocess.DEVNULL, stderr=err)
        try:
            if not wait_for_port(port):
                err.seek(0)
                report_failure(f"xray did not start: {err.read().strip()[-200:]}", host)
                return link, None, host, tier
            delays = []
            for _ in range(PROBES):
                delay = probe_delay(port)
                if delay is None:
                    break
                delays.append(delay)
            if not delays:
                report_failure("probe failed through proxy", host)
                return link, None, host, tier
            return link, min(delays), host, tier
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            os.unlink(cfg_path)


def geolocate(hosts: list[str]) -> dict[str, tuple[str, str]]:
    """Map host -> (country code, country name) using ip-api.com batch lookups (100 per request)."""
    result = {}
    for i in range(0, len(hosts), 100):
        chunk = hosts[i:i + 100]
        payload = json.dumps([{"query": h, "fields": "status,countryCode,country,query"} for h in chunk]).encode()
        req = urllib.request.Request(GEO_URL, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                entries = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"[warn] geolocation request failed: {exc}", file=sys.stderr)
            continue
        for entry in entries:
            if entry.get("status") == "success":
                result[entry["query"]] = (entry["countryCode"], entry["country"])
        time.sleep(1.5)
    return result


def flag_for(country_code: str) -> str:
    if len(country_code) != 2 or not country_code.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


NEAR_IRAN = [
    "IR", "AE", "IQ", "TR", "AM", "AZ", "TM", "AF", "PK", "OM", "KW", "QA", "BH", "SA",
    "GE", "JO", "SY", "LB", "IL", "TJ", "UZ", "KZ", "RU", "TH", "IN", "CY", "EG", "BY",
    "UA", "DE", "NL", "FR", "GB", "FI", "SE", "SG",
]


def proximity_rank(country_code: str) -> int:
    try:
        return NEAR_IRAN.index(country_code)
    except ValueError:
        return len(NEAR_IRAN)


def rename(link: str, name: str) -> str:
    scheme, rest = link.split("://", 1)
    if scheme.lower() == "vmess":
        v = b64_json(rest.split("#", 1)[0])
        v["ps"] = name
        encoded = base64.b64encode(json.dumps(v, separators=(",", ":")).encode()).decode()
        return f"vmess://{encoded}"
    base = rest.split("#", 1)[0]
    return f"{scheme}://{base}#{quote(name)}"


def write_outputs(entries: list[str], stats: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    text = "\n".join(entries) + "\n"
    with open(os.path.join(OUTPUT_DIR, "working_configs.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(OUTPUT_DIR, "working_configs_base64.txt"), "w", encoding="utf-8") as f:
        f.write(base64.b64encode(text.encode()).decode() + "\n")
    with open(os.path.join(OUTPUT_DIR, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)


def main() -> None:
    links = collect_sampled_links()
    print(f"Testing {len(links)} sampled configs")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for link, delay, host, tier in pool.map(test_link, links):
            if delay is not None:
                results.append((tier, delay, link, host))
    print(f"Working: {len(results)} / {len(links)}")
    geo = geolocate(list({host for _, _, _, host in results}))
    ranked = sorted(
        results,
        key=lambda r: (proximity_rank(geo.get(r[3], ("", ""))[0]), r[0], r[1]),
    )[:TOP_N]
    print(f"Selected top {len(ranked)} by country proximity to Iran, then transport tier, then delay")

    named = []
    for n, (tier, delay, link, host) in enumerate(ranked, start=1):
        code, country = geo.get(host, ("", "Unknown"))
        name = f"{flag_for(code)} {country} {n:03d}"
        named.append(rename(link, name))

    write_outputs(named, {
        "tested": len(links),
        "working": len(named),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


if __name__ == "__main__":
    main()

"""Shared logic for the config builders: collecting candidates, testing them through xray,
geolocating survivors, security tiering, and writing lists."""

import base64
import concurrent.futures
import html
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import parse_qs, quote, unquote, urlsplit

SOURCES = [
    "https://raw.githubusercontent.com/patterniha/Free-Configs/main/configs.txt#Patterniha-F",
    "https://github.com/Epodonios/v2ray-configs/raw/main/All_Configs_Sub.txt",
    "https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list/refs/heads/main/all_extracted_configs.txt",
    "https://raw.githubusercontent.com/Alirewa/V2ray-Configs/main/config.txt",
    "https://raw.githubusercontent.com/Alirewa/V2ray-Configs/main/sub1.txt",
    "https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/top100.txt",
    "https://raw.githubusercontent.com/zhangdunlong/free-v2ray-nodes/main/unique_nodes.txt",
    "https://raw.githubusercontent.com/zhangdunlong/free-v2ray-nodes/main/nodes_base64.txt",
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge.txt",
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge_base64.txt",
    "https://raw.githubusercontent.com/10ium/V2Hub/main/merged",
    "https://raw.githubusercontent.com/10ium/V2Hub/main/merged_base64",
    "https://raw.githubusercontent.com/mheidari98/.proxy/main/vless",
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/vless.txt",
    "https://raw.githubusercontent.com/mahanKenway/Freedom-V2Ray/main/configs/vless.txt",
    "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/v2ray.txt",
    "https://raw.githubusercontent.com/mahdyaralipor/v2all/main/real_sub.txt",
    "https://raw.githubusercontent.com/jafarm83/ConfigV2Ray/main/jafar.txt",
    "https://raw.githubusercontent.com/ripaojiedian/freenode/main/sub",
    "https://raw.githubusercontent.com/zhuhaiuk/free-nodes/main/nodes.txt",
    # Added: subscription files from barry-far/V2ray-Configs and nyeinkokoaung404/V2ray-Configs.
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub1.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub2.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub3.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub4.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub5.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub6.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Sub7.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/Splitted-By-Protocol/vless.txt",
    "https://raw.githubusercontent.com/nyeinkokoaung404/V2ray-Configs/main/Sub1.txt",
    "https://raw.githubusercontent.com/nyeinkokoaung404/V2ray-Configs/main/Sub2.txt",
    "https://raw.githubusercontent.com/nyeinkokoaung404/V2ray-Configs/main/Splitted-By-Protocol/vless.txt",
]

# Public channels scraped from their t.me/s/<name> preview pages.
TELEGRAM_CHANNELS = [
    "vlessconfig",
]

PROTOCOL_RE = re.compile(r"^(vmess|vless|trojan|ss)://", re.IGNORECASE)
TG_LINK_RE = re.compile(r"\b(?:vmess|vless|trojan|ss)://[^\s<>\"']+", re.IGNORECASE)
TG_POST_RE = re.compile(r'data-post="[^"]*/(\d+)"')

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
XRAY_BIN = os.environ.get("XRAY_BIN", "xray")
TIME_BUDGET = float(os.environ.get("TIME_BUDGET", "600"))
DEBUG_FAILURES = int(os.environ.get("DEBUG_FAILURES", "20"))
WORKERS = int(os.environ.get("WORKERS", "48"))
TEST_URL = os.environ.get("TEST_URL", "https://www.gstatic.com/generate_204")
TIMEOUT = float(os.environ.get("TEST_TIMEOUT", "5"))
PROBES = int(os.environ.get("PROBES", "2"))
TELEGRAM_PAGES = int(os.environ.get("TELEGRAM_PAGES", "5"))
CACHE_PATH = os.environ.get("CACHE_PATH", "/tmp/config_test_cache.json")
CACHE_TTL = float(os.environ.get("CACHE_TTL", "3600"))
GEO_URL = "http://ip-api.com/batch"


# ---------- collecting ----------

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


def fetch_telegram(channel: str, pages: int) -> list[str]:
    """Scrape links from a public channel preview (t.me/s/<channel>), walking back through older pages."""
    found, before = [], None
    for _ in range(pages):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        try:
            body = html.unescape(fetch(url))
        except Exception as exc:
            print(f"[warn] could not fetch telegram channel {channel}: {exc}", file=sys.stderr)
            break
        found += [m.group(0).rstrip(".,;)]}") for m in TG_LINK_RE.finditer(body)]
        ids = [int(i) for i in TG_POST_RE.findall(body)]
        if not ids or (before is not None and min(ids) >= before):
            break
        before = min(ids)
    print(f"telegram @{channel}: {len(found)} raw links")
    return found


def link_key(link: str) -> str:
    """Identity of a config ignoring its display name, so renamed configs dedupe correctly."""
    if link.lower().startswith("vmess://"):
        try:
            v = b64_json(link.split("://", 1)[1].split("#", 1)[0])
            v.pop("ps", None)
            return "vmess://" + json.dumps(v, sort_keys=True)
        except Exception:
            return link
    return link.split("#", 1)[0]


def load_list(path: str) -> list[str]:
    """Read a previously written config list. Missing file means an empty list."""
    try:
        with open(path, encoding="utf-8") as f:
            return [line.strip() for line in f if PROTOCOL_RE.match(line.strip())]
    except OSError:
        return []


def collect_candidates(previous: list[str]) -> list[str]:
    """Previous working configs first (so they are retested and kept if they still work),
    then new links from all sources, deduplicated by identity."""
    seen, candidates = set(), []

    def add(links: list[str]) -> int:
        added = 0
        for link in links:
            key = link_key(link)
            if key not in seen:
                seen.add(key)
                candidates.append(link)
                added += 1
        return added

    add(previous)
    new_links = []
    for src in SOURCES:
        try:
            body = decode_if_base64(fetch(src))
        except Exception as exc:
            print(f"[warn] could not fetch {src}: {exc}", file=sys.stderr)
            continue
        source_links = [line.strip() for line in body.splitlines() if PROTOCOL_RE.match(line.strip())]
        print(f"{src.split('#')[0]}: {len(source_links)} links")
        new_links.extend(source_links)
    for channel in TELEGRAM_CHANNELS:
        new_links.extend(fetch_telegram(channel, TELEGRAM_PAGES))

    random.shuffle(new_links)
    added = add(new_links)
    print(f"candidates: {len(candidates) - added} previous (unique) + {added} new = {len(candidates)}")
    return candidates


# ---------- converting links to xray outbounds ----------

def b64_json(payload: str) -> dict:
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def link_allows_insecure(link: str) -> bool:
    """True only if the config itself asks to skip certificate verification."""
    try:
        if link.lower().startswith("vmess://"):
            v = b64_json(link.split("://", 1)[1].split("#", 1)[0])
            values = [str(v.get(k, "")) for k in ("allowInsecure", "skip-cert-verify")]
        else:
            q = {k.lower(): val[0] for k, val in parse_qs(urlsplit(link).query).items()}
            values = [q.get("allowinsecure", ""), q.get("insecure", "")]
    except Exception:
        return False
    return any(v.lower() in ("1", "true") for v in values)


def build_outbound(link: str) -> dict | None:
    """Convert a share link into an xray outbound object. Returns None if unsupported."""
    scheme = link.split("://", 1)[0].lower()
    body = link.split("://", 1)[1].split("#", 1)[0]

    if scheme == "vmess":
        v = b64_json(body)
        net = v.get("net", "tcp")
        stream = {"network": net, "security": v.get("tls", "") or "none"}
        if stream["security"] == "tls":
            stream["tlsSettings"] = {"serverName": v.get("sni") or v.get("host", ""),
                                     "allowInsecure": link_allows_insecure(link)}
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
            stream["tlsSettings"] = {"serverName": q.get("sni", host),
                                     "allowInsecure": link_allows_insecure(link)}
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


def security_tier(outbound: dict, link: str) -> int:
    """Lower is better.
    0 Reality
    1 verified TLS with WebSocket/gRPC
    2 verified TLS
    3 TLS that skips certificate verification
    4 no TLS at all
    """
    stream = outbound.get("streamSettings", {})
    security = stream.get("security", "none")
    network = stream.get("network", "tcp")
    if security == "reality":
        return 0
    if security == "tls":
        if link_allows_insecure(link):
            return 3
        return 1 if network in ("ws", "grpc") else 2
    return 4


def outbound_host(outbound: dict) -> str:
    settings = outbound["settings"]
    entry = settings["vnext"][0] if "vnext" in settings else settings["servers"][0]
    return entry["address"]


# ---------- testing ----------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, proc: subprocess.Popen, deadline: float = 5.0) -> bool:
    end = time.time() + deadline
    while time.time() < end:
        if proc.poll() is not None:
            return False
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
_failure_lock = threading.Lock()


def report_failure(reason: str, host: str) -> None:
    global _failure_count
    with _failure_lock:
        _failure_count += 1
        should_print = _failure_count <= DEBUG_FAILURES
    if should_print:
        print(f"[fail] {host}: {reason}")


def test_link(link: str) -> tuple[str, float | None, str | None, int | None]:
    """Returns (link, best delay in ms or None, host, security tier)."""
    try:
        outbound = build_outbound(link)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return link, None, None, None
    if outbound is None:
        return link, None, None, None
    host = outbound_host(outbound)
    tier = security_tier(outbound, link)
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
            if not wait_for_port(port, proc):
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


def load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError as exc:
        print(f"[warn] could not write test cache: {exc}", file=sys.stderr)


def test_within_budget(links: list[str]) -> tuple[list[tuple[int, float, str, str]], int]:
    """Test links in order until the time budget runs out. Results from the last CACHE_TTL seconds
    are reused, so a second builder in the same run does not retest the same configs."""
    cache = load_cache()
    now = time.time()
    results, tested, todo = [], 0, []
    for link in links:
        entry = cache.get(link_key(link))
        if entry and now - entry["t"] < CACHE_TTL:
            tested += 1
            if entry["ok"]:
                results.append((entry["tier"], entry["delay"], link, entry["host"]))
        else:
            todo.append(link)
    print(f"{tested} configs reused from cache, {len(todo)} to test")

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)
    futures = [pool.submit(test_link, link) for link in todo]
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=TIME_BUDGET):
            link, delay, host, tier = fut.result()
            tested += 1
            ok = delay is not None
            cache[link_key(link)] = {"t": time.time(), "ok": ok, "tier": tier, "delay": delay, "host": host}
            if ok:
                results.append((tier, delay, link, host))
    except concurrent.futures.TimeoutError:
        print(f"[info] time budget of {TIME_BUDGET:.0f}s reached; stopping")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    save_cache(cache)
    return results, tested


# ---------- geolocation, naming, output ----------

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
        time.sleep(4.5)
    return result


def flag_for(country_code: str) -> str:
    if len(country_code) != 2 or not country_code.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


# Countries to prefer, in order. Set with PREFERRED_COUNTRIES="DE,NL,FR" (ISO 3166 codes).
# Empty means no country preference: the output is ranked by transport security and latency only.
PREFERRED_COUNTRIES = [
    code.strip().upper()
    for code in os.environ.get("PREFERRED_COUNTRIES", "").split(",")
    if code.strip()
]


def proximity_rank(country_code: str) -> int:
    """Position in PREFERRED_COUNTRIES; countries not in the list rank after all listed ones."""
    try:
        return PREFERRED_COUNTRIES.index(country_code)
    except ValueError:
        return len(PREFERRED_COUNTRIES)


def rename(link: str, name: str) -> str:
    scheme, rest = link.split("://", 1)
    if scheme.lower() == "vmess":
        v = b64_json(rest.split("#", 1)[0])
        v["ps"] = name
        encoded = base64.b64encode(json.dumps(v, separators=(",", ":")).encode()).decode()
        return f"vmess://{encoded}"
    base = rest.split("#", 1)[0]
    return f"{scheme}://{base}#{quote(name)}"


def write_list(filename: str, entries: list[str]) -> None:
    """Write a plain list and its base64 twin."""
    text = "\n".join(entries) + "\n"
    with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
        f.write(text)
    base = os.path.splitext(filename)[0] + "_base64.txt"
    with open(os.path.join(OUTPUT_DIR, base), "w", encoding="utf-8") as f:
        f.write(base64.b64encode(text.encode()).decode() + "\n")


def update_stats(key: str, values: dict) -> None:
    """Merge one builder's numbers into output/stats.json without touching the others."""
    path = os.path.join(OUTPUT_DIR, "stats.json")
    try:
        with open(path, encoding="utf-8") as f:
            stats = json.load(f)
    except (OSError, ValueError):
        stats = {}
    stats[key] = values
    stats["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

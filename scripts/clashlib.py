"""Converts tested share links into Clash Meta / Mihomo proxies and writes them as YAML.

FlClash, Clash.Meta, Clash Verge, Mihomo Party and Stash all read this format. Nothing here
needs a YAML library, so the builders keep running with a bare Python install.
"""

import base64
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import configlib as c

# Xray transport names mapped onto the network values Mihomo understands.
# "raw" is Xray's name for plain TCP with no header obfuscation, which is Mihomo's "tcp".
NETWORKS = {
    "": "tcp", "none": "tcp", "tcp": "tcp", "raw": "tcp", "original": "tcp",
    "ws": "ws", "websocket": "ws", "gun": "grpc", "grpc": "grpc", "h2": "h2", "http": "h2",
    # Transports Mihomo has no equivalent for (XHTTP, HTTPUpgrade): the node is skipped instead
    # of being written out as something that could never connect.
    "xhttp": None, "splithttp": None, "httpupgrade": None,
}

# Only clean, short ALPN tokens are worth carrying over; many published links put a channel
# watermark in this field instead. "/" is allowed because "http/1.1" is a real ALPN value.
ALPN_TOKEN_RE = re.compile(r"[A-Za-z0-9./\-]{1,30}")

SKIP_REASONS: dict[str, int] = {}


def _skip(reason: str) -> None:
    SKIP_REASONS[reason] = SKIP_REASONS.get(reason, 0) + 1


def skip_summary() -> dict[str, int]:
    return dict(sorted(SKIP_REASONS.items(), key=lambda kv: -kv[1]))


def _int(value, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    return unquote(str(value)).strip() if value else ""


def _query(link: str) -> dict[str, str]:
    return {k.lower(): v[0] for k, v in parse_qs(urlsplit(link).query).items()}


def _b64_text(payload: str) -> str:
    return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")


def _alpn(value) -> list[str] | None:
    if not value:
        return None
    candidates = value if isinstance(value, list) else str(value).split(",")
    tokens = [t for t in (_text(v) for v in candidates) if ALPN_TOKEN_RE.fullmatch(t)]
    return tokens[:5] or None


def _network(raw: str) -> str | None:
    """Mihomo network name, or None when the transport cannot be represented."""
    key = (raw or "").strip().lower()
    network = NETWORKS.get(key, "missing")
    if network is None or network == "missing":
        _skip(f"unsupported transport '{raw or 'tcp'}'")
        return None
    return network


def _transport(network: str, host: str, path: str, service_name: str) -> dict:
    if network == "ws":
        ws = {"path": path or "/"}
        if host:
            ws["headers"] = {"Host": host}
        return {"ws-opts": ws}
    if network == "grpc":
        return {"grpc-opts": {"grpc-service-name": service_name or ""}}
    if network == "h2":
        h2 = {}
        if host:
            h2["host"] = [host]
        if path:
            h2["path"] = path
        return {"h2-opts": h2}
    return {}


def _vless(link: str, name: str) -> dict | None:
    parts = urlsplit(link)
    q = _query(link)
    uuid = unquote(parts.username or "")
    if not parts.hostname or not parts.port or not uuid:
        return None
    network = _network(q.get("type", "tcp"))
    if network is None:
        return None

    proxy = {"name": name, "type": "vless", "server": parts.hostname, "port": int(parts.port),
             "uuid": uuid, "udp": True}

    security = (q.get("security") or "none").strip().lower()
    sni = _text(q.get("sni"))
    if security == "reality":
        if not q.get("pbk"):
            _skip("reality without public-key")
            return None
        proxy["tls"] = True
        if sni:
            proxy["servername"] = sni
        reality = {"public-key": q["pbk"]}
        if q.get("sid"):
            reality["short-id"] = q["sid"]
        proxy["reality-opts"] = reality
    elif security == "tls":
        proxy["tls"] = True
        if sni:
            proxy["servername"] = sni
        proxy["skip-cert-verify"] = c.link_allows_insecure(link)

    if q.get("fp"):
        proxy["client-fingerprint"] = _text(q["fp"])
    alpn = _alpn(q.get("alpn"))
    if alpn:
        proxy["alpn"] = alpn
    if q.get("flow") and network == "tcp":
        # XTLS flow only applies to plain TCP; carrying it onto ws or gRPC makes Mihomo reject the node.
        proxy["flow"] = _text(q["flow"])
    encoding = (_text(q.get("packetencoding")) or _text(q.get("packet-encoding"))).lower()
    if encoding in ("xudp", "packetaddr"):
        proxy["packet-encoding"] = encoding

    proxy.update(_transport(network, _text(q.get("host")), _text(q.get("path")),
                            _text(q.get("servicename"))))
    return proxy


def _vmess(link: str, name: str) -> dict | None:
    try:
        v = c.b64_json(link.split("://", 1)[1].split("#", 1)[0])
    except Exception:
        _skip("unparseable vmess payload")
        return None
    server, port, uuid = _text(v.get("add")), _int(v.get("port")), _text(v.get("id"))
    if not server or not port or not uuid:
        return None
    network = _network(_text(v.get("net")))
    if network is None:
        return None

    proxy = {"name": name, "type": "vmess", "server": server, "port": port,
             "uuid": uuid, "alterId": _int(v.get("aid"), 0), "udp": True,
             "cipher": _text(v.get("scy")) or "auto"}

    tls = (_text(v.get("tls")) or "none").lower()
    if tls == "tls":
        proxy["tls"] = True
        sni = _text(v.get("sni")) or _text(v.get("host"))
        if sni:
            proxy["servername"] = sni
        proxy["skip-cert-verify"] = c.link_allows_insecure(link)
    if _text(v.get("fp")):
        proxy["client-fingerprint"] = _text(v["fp"])
    alpn = _alpn(v.get("alpn"))
    if alpn:
        proxy["alpn"] = alpn

    path = _text(v.get("path"))
    proxy.update(_transport(network, _text(v.get("host")), path, path))
    return proxy


def _trojan(link: str, name: str) -> dict | None:
    parts = urlsplit(link)
    q = _query(link)
    password = unquote(parts.username or "")
    if not parts.hostname or not parts.port or not password:
        return None
    network = _network(q.get("type", "tcp"))
    if network is None:
        return None

    proxy = {"name": name, "type": "trojan", "server": parts.hostname, "port": int(parts.port),
             "password": password, "udp": True}
    sni = _text(q.get("sni")) or (parts.hostname or "")
    if sni:
        proxy["sni"] = sni
    proxy["skip-cert-verify"] = c.link_allows_insecure(link)
    if q.get("fp"):
        proxy["client-fingerprint"] = _text(q["fp"])
    alpn = _alpn(q.get("alpn"))
    if alpn:
        proxy["alpn"] = alpn

    proxy.update(_transport(network, _text(q.get("host")), _text(q.get("path")),
                            _text(q.get("servicename"))))
    return proxy


def _shadowsocks(link: str, name: str) -> dict | None:
    body = link.split("://", 1)[1].split("#", 1)[0]
    if "@" not in body:
        # Legacy form where the whole method:password@host:port block is base64.
        try:
            body = _b64_text(body)
        except Exception:
            return None
    if "@" not in body:
        return None
    userinfo, hostport = body.rsplit("@", 1)
    # SIP002 userinfo is either "method:password" or base64 of it. A base64 blob can never
    # contain ":", so its presence tells the two apart without guessing from decode failures.
    if ":" in userinfo:
        method_pass = unquote(userinfo)
    else:
        try:
            method_pass = _b64_text(userinfo)
        except Exception:
            return None
    if ":" not in method_pass:
        return None
    method, password = method_pass.split(":", 1)
    host, _, port_str = hostport.rpartition(":")
    port = _int(port_str)
    if not host or not port or not method:
        return None
    return {"name": name, "type": "ss", "server": host, "port": port,
            "cipher": method, "password": password, "udp": True}


def to_mihomo_proxy(link: str, name: str) -> dict | None:
    """Convert a share link into a Mihomo proxy object, or None if Mihomo cannot represent it."""
    scheme = link.split("://", 1)[0].lower()
    builder = {"vless": _vless, "vmess": _vmess, "trojan": _trojan, "ss": _shadowsocks}.get(scheme)
    if builder is None:
        _skip(f"unsupported scheme '{scheme}'")
        return None
    try:
        return builder(link, name)
    except Exception:
        _skip(f"unparseable {scheme} link")
        return None


def proxy_identity(proxy: dict) -> tuple:
    """Server identity, so the same node published under two names becomes one proxy."""
    return (proxy.get("type"), proxy.get("server"), proxy.get("port"),
            proxy.get("uuid") or proxy.get("password") or proxy.get("cipher"))


# ---------- YAML output (no third-party dependency) ----------

PLAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-/@+~^%()\[\]=]*$")
NUMERIC_RE = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"}


def _scalar(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value)
    if not PLAIN_RE.match(text) or text != text.strip() \
            or text.lower() in RESERVED or NUMERIC_RE.match(text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _inline(value) -> str:
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    return _scalar(value)


def _emit(value, indent: int, out: list[str]) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                out.append(f"{pad}{_scalar(key)}:")
                _emit(item, indent + 1, out)
            else:
                out.append(f"{pad}{_scalar(key)}: {_inline(item)}")
    else:
        for item in value:
            if isinstance(item, dict) and item:
                block: list[str] = []
                _emit(item, indent + 1, block)
                block[0] = pad + "- " + block[0][len(pad) + 2:]
                out.extend(block)
            elif isinstance(item, list) and item:
                out.append(f"{pad}-")
                _emit(item, indent + 1, out)
            else:
                out.append(f"{pad}- {_inline(item)}")


def to_yaml(data: dict) -> str:
    lines: list[str] = []
    _emit(data, 0, lines)
    return "\n".join(lines) + "\n"
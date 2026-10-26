# -*- coding: utf-8 -*-
"""Per-account proxy support for the HTTP and TCP login paths.

Accounts may carry a proxy string such as ``socks5://user:pass@host:1080`` or
``http://host:8080``. ``httpx`` handles the HTTP calls; game gateway sockets are
opened through the same proxy with a small CONNECT / SOCKS5 handshake so DNS no
longer has to resolve on this machine when a proxy is configured.

Supported schemes: ``http``, ``https``, ``socks5`` (local DNS), ``socks5h``
(proxy-side DNS).
"""

import asyncio
import base64
import ipaddress
import socket
import struct
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit

SUPPORTED_SCHEMES = ("http", "https", "socks5", "socks5h")
DEFAULT_TIMEOUT = 15.0

_SOCKS_REPLIES = {
    1: "general SOCKS server failure",
    2: "connection not allowed by ruleset",
    3: "network unreachable",
    4: "host unreachable",
    5: "connection refused",
    6: "TTL expired",
    7: "command not supported",
    8: "address type not supported",
}


class ProxyError(ConnectionError):
    """Raised when a proxy is misconfigured or refuses the tunnel."""


def parse_proxy(proxy: Optional[str]) -> Optional[Dict[str, object]]:
    """Parse a proxy URL, raising ``ValueError`` when it cannot be used."""
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raise ValueError("proxy must include a scheme, e.g. socks5://host:1080")
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(
            f"unsupported proxy scheme '{scheme}' (use http, https, socks5, or socks5h)"
        )
    if not parts.hostname:
        raise ValueError("proxy is missing a host")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("proxy has an invalid port") from exc
    if not port:
        raise ValueError("proxy is missing a port")
    return {
        "url": raw,
        "scheme": scheme,
        "host": parts.hostname,
        "port": int(port),
        "username": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
    }


def normalize_proxy(proxy: Optional[str]) -> Optional[str]:
    """Return the proxy string when valid, ``None`` when empty. Raises on bad input."""
    info = parse_proxy(proxy)
    return str(info["url"]) if info else None


def describe_proxy(proxy: Optional[str]) -> str:
    """Safe label for logs and the dashboard: credentials are always masked."""
    info = parse_proxy(proxy)
    if not info:
        return ""
    auth = "***@" if info["username"] or info["password"] else ""
    return f"{info['scheme']}://{auth}{info['host']}:{info['port']}"


async def _read_exact(reader: asyncio.StreamReader, size: int) -> bytes:
    try:
        return await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:
        raise ProxyError("proxy closed the connection during handshake") from exc


async def _http_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    port: int,
    info: Dict[str, object],
) -> None:
    target = f"{host}:{port}"
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if info["username"] or info["password"]:
        creds = f"{info['username']}:{info['password']}".encode()
        lines.append(f"Proxy-Authorization: Basic {base64.b64encode(creds).decode()}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    await writer.drain()

    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=DEFAULT_TIMEOUT)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise ProxyError("proxy sent a malformed CONNECT response") from exc
    except asyncio.TimeoutError as exc:
        raise ProxyError("proxy CONNECT timed out") from exc

    status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    fields = status_line.split()
    code = fields[1] if len(fields) > 1 else ""
    if not code.startswith("2"):
        raise ProxyError(f"proxy refused CONNECT ({status_line[:80]})")


async def _socks5_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    port: int,
    info: Dict[str, object],
) -> None:
    wants_auth = bool(info["username"] or info["password"])
    writer.write(b"\x05\x02\x00\x02" if wants_auth else b"\x05\x01\x00")
    await writer.drain()
    version, method = await _read_exact(reader, 2)
    if version != 5:
        raise ProxyError("proxy is not a SOCKS5 server")
    if method == 0x02:
        user = str(info["username"]).encode()[:255]
        password = str(info["password"]).encode()[:255]
        writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
        await writer.drain()
        _, auth_status = await _read_exact(reader, 2)
        if auth_status != 0:
            raise ProxyError("proxy rejected the SOCKS5 credentials")
    elif method != 0x00:
        raise ProxyError("proxy accepts no supported SOCKS5 auth method")

    if info["scheme"] == "socks5h":
        name = host.encode("idna")[:255]
        address = b"\x03" + bytes([len(name)]) + name
    else:
        address = await _socks5_address(host)

    writer.write(b"\x05\x01\x00" + address + struct.pack(">H", int(port)))
    await writer.drain()

    version, reply, _, atyp = await _read_exact(reader, 4)
    if version != 5:
        raise ProxyError("proxy sent a malformed SOCKS5 reply")
    if reply != 0:
        raise ProxyError(f"proxy SOCKS5 connect failed: {_SOCKS_REPLIES.get(reply, reply)}")
    if atyp == 0x01:
        await _read_exact(reader, 4)
    elif atyp == 0x03:
        (length,) = await _read_exact(reader, 1)
        await _read_exact(reader, length)
    elif atyp == 0x04:
        await _read_exact(reader, 16)
    else:
        raise ProxyError("proxy sent an unknown SOCKS5 address type")
    await _read_exact(reader, 2)


async def _socks5_address(host: str) -> bytes:
    """Resolve locally for ``socks5://`` and format an ATYP address."""
    try:
        ip = ipaddress.ip_address(host)
        return (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET)
    except OSError as exc:
        raise ProxyError(f"cannot resolve {host} for socks5 proxy") from exc
    if not infos:
        raise ProxyError(f"cannot resolve {host} for socks5 proxy")
    return b"\x01" + socket.inet_aton(infos[0][4][0])


async def open_connection(
    host: str,
    port: int,
    proxy: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a TCP stream to ``host:port``, tunnelled through ``proxy`` if set."""
    info = parse_proxy(proxy)
    if not info:
        return await asyncio.open_connection(host, int(port))

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(str(info["host"]), int(info["port"])), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise ProxyError(f"cannot reach proxy {info['host']}:{info['port']}: {exc}") from exc

    try:
        if info["scheme"] in ("http", "https"):
            await _http_connect(reader, writer, host, int(port), info)
        else:
            await asyncio.wait_for(
                _socks5_connect(reader, writer, host, int(port), info), timeout=timeout
            )
    except BaseException:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        raise
    return reader, writer

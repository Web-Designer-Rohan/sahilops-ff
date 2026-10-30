# -*- coding: utf-8 -*-
"""Guild lookup and badge helpers.

The dashboard asks Garena's clan endpoint for a guild by ID.  The response is
a protobuf ``data_pb2.response`` whose ``guild_details`` message carries the
name, level, member count, region, and leader.  This module keeps that protocol
code in one place so the web API and future CLI tools share it.

Garena does not return a logo URL in the clan response, so ``logo_url`` points
at the dashboard's deterministic SVG badge endpoint.  That keeps the UI working
even when the guild has no public image or the browser is offline.
"""

from __future__ import annotations

import hashlib
import html
import os
import sys
from typing import Any, Dict, Iterable, Optional

import requests
import urllib3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import data_pb2, encode_id_clan_pb2, storage

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])

REGION_SERVERS = {
    "IND": ("https://client.ind.freefiremobile.com", "client.ind.freefiremobile.com"),
    "ME": ("https://clientbp.ggblueshark.com", "clientbp.ggblueshark.com"),
    "VN": ("https://clientbp.ggpolarbear.com", "clientbp.ggpolarbear.com"),
    "BD": ("https://clientbp.ggwhitehawk.com", "clientbp.ggwhitehawk.com"),
    "PK": ("https://clientbp.ggblueshark.com", "clientbp.ggblueshark.com"),
    "SG": ("https://clientbp.ggpolarbear.com", "clientbp.ggpolarbear.com"),
    "BR": ("https://client.us.freefiremobile.com", "client.us.freefiremobile.com"),
    "NA": ("https://client.us.freefiremobile.com", "client.us.freefiremobile.com"),
    "ID": ("https://clientbp.ggpolarbear.com", "clientbp.ggpolarbear.com"),
    "RU": ("https://clientbp.ggpolarbear.com", "clientbp.ggpolarbear.com"),
    "TH": ("https://clientbp.ggpolarbear.com", "clientbp.ggpolarbear.com"),
    "SAC": ("https://client.br.freefiremobile.com", "client.br.freefiremobile.com"),
    "TW": ("https://client.tw.freefiremobile.com", "client.tw.freefiremobile.com"),
    "CIS": ("https://client.cis.freefiremobile.com", "client.cis.freefiremobile.com"),
}

GAME_HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9)",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/octet-stream",
    "X-Unity-Version": "2018.4.11f1",
    "X-GA": "v1 1",
    "ReleaseVersion": "OB55",
    "Expect": "100-continue",
}


class GuildLookupError(RuntimeError):
    """Raised when a guild cannot be queried with the available tokens."""


def normalize_region(region: Optional[str]) -> str:
    value = str(region or "IND").strip().upper()
    if value == "IN":
        return "IND"
    return value if value in REGION_SERVERS else "IND"


def guild_logo_url(guild_id: Any) -> str:
    """Dashboard-relative badge URL for a guild ID."""
    return f"/api/guild/badge/{int(guild_id)}.svg"


def _iter_tokens(token_cache: Any) -> Iterable[str]:
    if token_cache is None:
        token_cache = storage.tokens_all()
    if isinstance(token_cache, str):
        if token_cache:
            yield token_cache
        return
    if isinstance(token_cache, dict):
        values = token_cache.values()
    else:
        values = token_cache or ()
    for creds in values:
        if isinstance(creds, str):
            if creds:
                yield creds
            continue
        if not isinstance(creds, dict):
            continue
        token = creds.get("token") or creds.get("access_token") or creds.get("jwt")
        if token:
            yield str(token)


def _encrypt(data: bytes) -> bytes:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(data, AES.block_size))


def parse_guild_metadata(content: bytes, guild_id: Any, region: str = "IND") -> Dict[str, Any]:
    """Parse a raw ``GetClanInfoByClanID`` protobuf response."""
    try:
        resp = data_pb2.response()
        resp.ParseFromString(content)
    except Exception as exc:
        raise GuildLookupError(f"guild response was not valid protobuf: {exc}") from exc

    has_details = False
    try:
        has_details = resp.HasField("guild_details")
    except Exception:
        has_details = False

    details = resp.guild_details if has_details else None
    name = ""
    level = 0
    member_count = 0
    members_online = 0
    leader_uid = 0
    guild_region = normalize_region(region)
    if details is not None:
        name = str(getattr(details, "name", "") or "").strip()
        level = int(getattr(details, "level", 0) or 0)
        member_count = int(getattr(details, "total_members", 0) or 0)
        members_online = int(getattr(details, "members_online", 0) or 0)
        leader_uid = int(getattr(details, "leader_uid", 0) or 0)
        guild_region = normalize_region(getattr(details, "region", "") or region)

    # Older responses only populate the top-level fields.
    if not name:
        name = str(getattr(resp, "special_code", "") or "").strip()
    if not level:
        level = int(getattr(resp, "rank", 0) or 0)
    if not name:
        name = f"Guild {guild_id}"

    xp = int(getattr(resp, "xp", 0) or 0)
    return {
        "guild_id": int(guild_id),
        "clan_id": int(guild_id),
        "name": name,
        "clan_name": name,
        "level": level,
        "clan_level": level,
        "member_count": member_count,
        "current_members": member_count,
        "members_online": members_online,
        "max_members": 50,
        "region": guild_region,
        "leader_uid": leader_uid,
        "xp": xp,
        "Total guild xp": xp,
        "logo_url": guild_logo_url(guild_id),
        "source": "garena",
    }


def fetch_guild_metadata(
    guild_id: Any,
    region: str = "IND",
    token_cache: Optional[Any] = None,
    timeout: float = 12.0,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Query Garena for one guild and return normalized metadata.

    ``token_cache`` accepts the storage mapping (``{uid: {"token": ...}}``), a
    single token string, or an iterable of tokens.  A cached game token is
    required because Garena does not expose this endpoint anonymously.
    """
    try:
        guild_id = int(str(guild_id).strip())
    except (TypeError, ValueError) as exc:
        raise GuildLookupError("guild_id must be numeric") from exc

    region_code = normalize_region(region)
    server_base, host = REGION_SERVERS[region_code]
    url = f"{server_base}/GetClanInfoByClanID"

    try:
        request = encode_id_clan_pb2.MyData()
        request.field1 = guild_id
        request.field2 = 1
        payload = _encrypt(request.SerializeToString())
    except Exception as exc:
        raise GuildLookupError(f"could not build guild request: {exc}") from exc

    tokens = list(_iter_tokens(token_cache))
    if not tokens:
        raise GuildLookupError("no cached game token available for guild lookup")

    owns_session = session is None
    session = session or requests.Session()
    last_error = "all cached tokens were rejected"
    try:
        for token in tokens:
            headers = dict(GAME_HEADERS)
            headers["Authorization"] = token if token.startswith("Bearer ") else f"Bearer {token}"
            headers["Host"] = host
            try:
                response = session.post(url, headers=headers, data=payload,
                                        verify=False, timeout=timeout)
            except requests.RequestException as exc:
                last_error = f"guild request failed: {type(exc).__name__}"
                continue

            if response.status_code == 200:
                return parse_guild_metadata(response.content, guild_id, region_code)

            text = (response.text or "").lower()
            last_error = f"guild lookup HTTP {response.status_code}"
            if response.status_code in (401, 403) or "expired" in text:
                continue
            raise GuildLookupError(last_error)
    finally:
        if owns_session:
            session.close()

    raise GuildLookupError(last_error)


def guild_badge_svg(guild_id: Any, name: str = "", level: Any = 0,
                    members: Any = 0) -> str:
    """Deterministic industrial badge used when Garena exposes no image URL."""
    try:
        gid = int(str(guild_id).strip())
    except (TypeError, ValueError):
        gid = 0
    safe_name = str(name or "").strip()
    words = [word for word in safe_name.split() if word]
    initials = "".join(word[0] for word in words[:2]).upper() or "FF"
    digest = hashlib.sha256(str(gid).encode("utf-8")).digest()
    hue = digest[0] * 360 // 256
    accent = f"hsl({hue}, 72%, 56%)"
    try:
        level_value = max(0, int(level or 0))
    except (TypeError, ValueError):
        level_value = 0
    try:
        member_value = max(0, int(members or 0))
    except (TypeError, ValueError):
        member_value = 0

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" role="img" aria-label="Guild {gid} badge">
<defs>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#0f172a"/><stop offset="1" stop-color="#020617"/>
</linearGradient>
</defs>
<rect width="128" height="128" rx="24" fill="url(#g)"/>
<path d="M18 92 L64 18 L110 92 Z" fill="{accent}" opacity="0.18"/>
<path d="M18 92 L64 18 L110 92" fill="none" stroke="{accent}" stroke-width="3"/>
<text x="64" y="70" text-anchor="middle" font-family="Inter,Segoe UI,sans-serif" font-size="34" font-weight="800" fill="#f8fafc">{html.escape(initials)}</text>
<text x="64" y="92" text-anchor="middle" font-family="Inter,Segoe UI,sans-serif" font-size="12" font-weight="700" fill="{accent}">LV {level_value} · {member_value} MEM</text>
<text x="64" y="108" text-anchor="middle" font-family="Inter,Segoe UI,sans-serif" font-size="9" fill="#94a3b8">ID {gid}</text>
</svg>"""

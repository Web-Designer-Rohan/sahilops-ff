# -*- coding: utf-8 -*-
"""Account health: ban/restriction detection and quarantine accounting.

Login payloads and gateway responses are free-form (JSON for OAuth, protobuf for
the game gateways), so suspension detection is a conservative keyword/code scan.
Anything that looks like a ban stops the worker instead of letting it retry a
dead credential forever.
"""

import os
import re
from typing import Optional

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
STATUS_QUARANTINED = "quarantined"
STATUSES = (STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_QUARANTINED)

# A worker that keeps failing is quarantined after this many logged errors.
ERROR_THRESHOLD = max(1, int(os.environ.get("SAHILOPS_ERROR_THRESHOLD", "5") or 5))

# Gateway/OAuth error codes that mean "this credential is not allowed in".
BAN_CODES = tuple(
    code.strip()
    for code in os.environ.get(
        "SAHILOPS_BAN_CODES", "1004,1005,1006,2003,2004"
    ).split(",")
    if code.strip()
)

_SUSPENSION_PATTERNS = [
    (re.compile(r"\bpermanently\s+banned\b"), "permanently banned"),
    (re.compile(r"\baccount\s+(?:is\s+|has\s+been\s+)?banned\b"), "account banned"),
    (re.compile(r"\baccount\s+(?:is\s+|has\s+been\s+)?suspended\b"), "account suspended"),
    (re.compile(r"\baccount\s+(?:is\s+|has\s+been\s+)?(?:blocked|disabled|restricted)\b"),
     "account restricted"),
    (re.compile(r"\bsuspension\b"), "suspension"),
    (re.compile(r"\b(?:ban|restriction)\s+code\b"), "ban code"),
    (re.compile(r"\b(?:invalid|dead|expired)\s+(?:account|credential)s?\b"), "invalid account"),
    (re.compile(r"\blogin\s+denied\b"), "login denied"),
]

# JSON/protobuf-ish flags such as {"ban": 1} or "banned": true.
_FLAG_PATTERN = re.compile(
    r'["\']?(?:is_?ban(?:ned)?|ban(?:ned)?|suspended|restricted|account_status)["\']?'
    r"\s*[:=]\s*[\"']?(?:1|true|yes|banned|suspended|restricted)\b",
    re.IGNORECASE,
)
_CODE_PATTERN = re.compile(
    r'["\']?(?:code|error_code|status_code|retcode)["\']?\s*[:=]\s*["\']?(\d{3,6})\b',
    re.IGNORECASE,
)


class AccountSuspended(Exception):
    """Raised when a login/gateway response says the account is not allowed."""

    def __init__(self, reason: str, uid: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.uid = uid


def detect_suspension(*texts) -> Optional[str]:
    """Return a short reason when any text carries a ban/restriction marker."""
    for text in texts:
        if not text:
            continue
        if isinstance(text, (bytes, bytearray)):
            try:
                text = bytes(text).decode("utf-8", "ignore")
            except Exception:
                continue
        blob = str(text)

        for pattern, label in _SUSPENSION_PATTERNS:
            match = pattern.search(blob.lower())
            if match:
                return label
        match = _FLAG_PATTERN.search(blob)
        if match:
            return f"flag {match.group(0)[:32]}"
        for match in _CODE_PATTERN.finditer(blob):
            if match.group(1) in BAN_CODES:
                return f"code {match.group(1)}"
    return None


def is_quarantine_status(status: str) -> bool:
    return str(status or "").strip().lower() in (STATUS_SUSPENDED, STATUS_QUARANTINED)


def normalize_status(status: Optional[str]) -> str:
    status = str(status or "").strip().lower()
    return status if status in STATUSES else STATUS_ACTIVE

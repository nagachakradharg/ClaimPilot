"""PII redaction for logs, traces and the audit trail.

Two layers: patterns (emails, phone numbers, street addresses, VINs, policy numbers partly
masked) and known values (the claimant's name, email, phone and address from the policy),
because names cannot be found reliably by pattern alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,2}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\w)"
)
SHORT_PHONE = re.compile(r"(?<!\w)555-\d{4}(?!\w)")  # synthetic demo numbers
ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}(?:St|Street|Ave|Avenue|Rd|Road|Ln|Lane|Dr|Drive|Ct|"
    r"Court|Blvd|Boulevard|Way|Pl|Place)\b\.?"
)
VIN = re.compile(r"\b(?:[A-HJ-NPR-Z0-9]{17}|SYNTH\d{12})\b")
POLICY = re.compile(r"\bCP-(\d{2})\d{4}\b")


def redact_text(text: str, known: Iterable[str] = ()) -> str:
    for value in sorted({k for k in known if k and len(k) >= 3}, key=len, reverse=True):
        text = re.sub(re.escape(value), "[redacted]", text, flags=re.I)
    text = EMAIL.sub("[email]", text)
    text = ADDRESS.sub("[address]", text)
    text = PHONE.sub("[phone]", text)
    text = SHORT_PHONE.sub("[phone]", text)
    text = VIN.sub("[vin]", text)
    return POLICY.sub(lambda m: f"CP-{m.group(1)}****", text)


def redact(value: Any, known: Iterable[str] = ()) -> Any:
    """Recursively redact strings inside dicts/lists; other values pass through."""
    known = tuple(known)
    if isinstance(value, str):
        return redact_text(value, known)
    if isinstance(value, dict):
        return {k: redact(v, known) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v, known) for v in value]
    return value


def known_pii(*values: str | None) -> list[str]:
    """Known PII strings plus the individual parts of a full name (e.g. first and last)."""
    out: list[str] = []
    for v in values:
        if not v:
            continue
        out.append(v)
        if " " in v and "@" not in v and not any(ch.isdigit() for ch in v):
            out += [p for p in v.split() if len(p) >= 3]
    return out

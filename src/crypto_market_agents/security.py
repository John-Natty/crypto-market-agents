"""Security guardrails for the crypto market analysis project."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SecurityError(ValueError):
    """Raised when a forbidden security-sensitive setting is detected."""


FORBIDDEN_CREDENTIAL_FRAGMENTS = (
    "private_key",
    "seed_phrase",
    "mnemonic",
    "recovery_phrase",
    "wallet_secret",
    "wallet_private",
    "withdrawal_key",
    "withdrawal_secret",
)

FORBIDDEN_OPERATION_FLAGS = {
    "AUTO_TRADING_ENABLED",
    "TRADING_ENABLED",
    "EXCHANGE_TRADING_ENABLED",
    "ORDER_EXECUTION_ENABLED",
    "WITHDRAWALS_ENABLED",
    "WITHDRAWAL_ENABLED",
}

ALLOWED_EXCHANGE_MODES = {"", "disabled", "read_only", "readonly"}
REDACTED = "[REDACTED]"

SENSITIVE_QUERY_KEYS = {
    "api_key",
    "key",
    "token",
    "access_token",
    "authorization",
    "auth",
    "password",
    "secret",
    "x_cg_demo_api_key",
    "x_cg_pro_api_key",
    "whatsapp_access_token",
}

SENSITIVE_KEY_FRAGMENTS = (
    "token",
    "key",
    "secret",
    "password",
    "authorization",
    "auth",
)

URL_RE = re.compile(r"https?://[^\s\"'<>]+")
BEARER_RE = re.compile(r"(?i)\b(bearer\s+)([^\s,;]+)")
AUTHORIZATION_RE = re.compile(r"(?i)\b(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)")
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api_key|access_token|token|key|secret|password|auth)\s*([=:])\s*([^\s,;&]+)"
)

FORBIDDEN_OPERATION_WORDS = (
    "buy",
    "sell",
    "trade",
    "swap",
    "order",
    "withdraw",
    "transfer",
)


def is_truthy(value: str | None) -> bool:
    """Return True for common env values that mean enabled."""

    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def validate_security_environment(env: Mapping[str, str]) -> None:
    """Reject env settings that would move the app outside read-only analysis."""

    for raw_key, raw_value in env.items():
        key = raw_key.strip().upper()
        normalized_key = key.lower()

        for fragment in FORBIDDEN_CREDENTIAL_FRAGMENTS:
            if fragment in normalized_key:
                raise SecurityError(f"Forbidden crypto credential variable detected: {raw_key}")

        if key in FORBIDDEN_OPERATION_FLAGS and is_truthy(raw_value):
            raise SecurityError(f"Forbidden trading/withdrawal flag enabled: {raw_key}")

    exchange_mode = str(env.get("EXCHANGE_MODE", "")).strip().lower()
    if exchange_mode not in ALLOWED_EXCHANGE_MODES:
        raise SecurityError(
            "EXCHANGE_MODE must be disabled or read_only. "
            "Trading and withdrawals are not supported."
        )


def assert_read_only_operation(operation_name: str) -> None:
    """Reject operation names that suggest trading, transfers, or withdrawals."""

    normalized = operation_name.strip().lower().replace("-", "_")
    for word in FORBIDDEN_OPERATION_WORDS:
        if word in normalized:
            raise SecurityError(
                f"Forbidden operation '{operation_name}'. "
                "This project is read-only and cannot trade or move funds."
            )


def redact_secret(value: str | None) -> str:
    """Return a safe display version of a secret-like value."""

    if not value:
        return ""

    text = str(value)
    if len(text) <= 8:
        return "***"

    return f"{text[:4]}...{text[-4:]}"


def redact_value(value: Any) -> str:
    """Return a fully redacted display value for sensitive data."""

    if value is None:
        return ""

    return REDACTED


def redact_url(url: str) -> str:
    """Return a URL with sensitive query values, credentials, and fragments removed."""

    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "[REDACTED_URL]"

    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_sensitive_key(key):
            query_items.append((key, REDACTED))
        else:
            query_items.append((key, value))

    query = urlencode(query_items).replace("%5BREDACTED%5D", REDACTED)
    netloc = _redact_netloc(parts)

    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def redact_text(text: Any, secrets: Sequence[str | None] | None = None) -> str:
    """Redact URLs, bearer tokens, key-value secrets, and explicit secret values from text."""

    redacted = URL_RE.sub(_redact_url_match, str(text))
    redacted = AUTHORIZATION_RE.sub(r"\1" + REDACTED, redacted)
    redacted = BEARER_RE.sub(r"\1" + REDACTED, redacted)
    redacted = SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2" + REDACTED, redacted)

    for secret in _clean_secrets(secrets):
        redacted = redacted.replace(secret, REDACTED)

    return redacted


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a mapping with sensitive keys and nested values redacted."""

    redacted: dict[str, Any] = {}
    for key, value in data.items():
        text_key = str(key)
        if _is_sensitive_key(text_key):
            redacted[text_key] = redact_value(value)
        else:
            redacted[text_key] = _redact_nested(value)

    return redacted


def redact_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Redact values for keys that should not be printed in logs."""

    redacted: dict[str, str] = {}
    secret_words = ("KEY", "TOKEN", "SECRET", "PASSWORD")

    for key, value in env.items():
        if any(word in key.upper() for word in secret_words):
            redacted[key] = redact_secret(value)
        else:
            redacted[key] = value

    return redacted


def _redact_url_match(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    url, trailing = _split_trailing_punctuation(raw_url)
    return redact_url(url) + trailing


def _split_trailing_punctuation(value: str) -> tuple[str, str]:
    trailing = ""
    cleaned = value
    while cleaned and cleaned[-1] in ".,;)]}":
        trailing = cleaned[-1] + trailing
        cleaned = cleaned[:-1]

    return cleaned, trailing


def _redact_netloc(parts: Any) -> str:
    hostname = parts.hostname
    if not hostname:
        return REDACTED if "@" in parts.netloc else parts.netloc

    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    try:
        port = f":{parts.port}" if parts.port is not None else ""
    except ValueError:
        port = ""

    if parts.username or parts.password:
        return f"{REDACTED}@{host}{port}"

    return f"{host}{port}"


def _redact_nested(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_nested(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)

    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in SENSITIVE_QUERY_KEYS or any(
        fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS
    )


def _clean_secrets(secrets: Sequence[str | None] | None) -> tuple[str, ...]:
    if not secrets:
        return ()

    clean_values = []
    for secret in secrets:
        if secret:
            clean_secret = str(secret).strip()
            if clean_secret:
                clean_values.append(clean_secret)

    return tuple(clean_values)

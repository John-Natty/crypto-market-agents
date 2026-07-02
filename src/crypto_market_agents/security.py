"""Security guardrails for the crypto market analysis project."""

from __future__ import annotations

from collections.abc import Mapping


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
                raise SecurityError(
                    f"Forbidden crypto credential variable detected: {raw_key}"
                )

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


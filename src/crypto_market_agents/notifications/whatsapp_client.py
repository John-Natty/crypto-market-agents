"""Optional WhatsApp Cloud API client."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from crypto_market_agents.config import WhatsAppConfig
from crypto_market_agents.security import redact_mapping, redact_text


GRAPH_API_BASE_URL = "https://graph.facebook.com"
CHANNEL = "whatsapp"
MAX_TEXT_MESSAGE_LENGTH = 4096


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """Small JSON-friendly result for optional notifications."""

    sent: bool
    channel: str
    status: str
    message: str
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "sent": self.sent,
            "channel": self.channel,
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "data": self.data,
        }


class WhatsAppClient:
    """Send optional text notifications through Meta WhatsApp Cloud API."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        to_number: str | None = None,
        graph_api_version: str = "v23.0",
        timeout_seconds: int = 20,
        base_url: str = GRAPH_API_BASE_URL,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.access_token = _clean_optional_text(access_token)
        self.phone_number_id = _clean_optional_text(phone_number_id)
        self.to_number = _clean_optional_text(to_number)
        self.graph_api_version = _clean_graph_api_version(graph_api_version)
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        self.base_url = _clean_base_url(base_url)
        self._opener = opener or urlopen

    @classmethod
    def from_config(cls, config: WhatsAppConfig) -> WhatsAppClient:
        """Create a client from validated application configuration."""

        return cls(
            enabled=config.enabled,
            access_token=config.access_token,
            phone_number_id=config.phone_number_id,
            to_number=config.to_number,
            graph_api_version=config.graph_api_version,
            timeout_seconds=config.timeout_seconds,
        )

    @classmethod
    def from_env(cls) -> WhatsAppClient:
        """Create a client from WhatsApp environment variables."""

        timeout = os.getenv("WHATSAPP_TIMEOUT") or os.getenv("REQUEST_TIMEOUT_SECONDS")
        return cls(
            enabled=_env_bool(os.getenv("WHATSAPP_ENABLED"), default=False),
            access_token=os.getenv("WHATSAPP_ACCESS_TOKEN"),
            phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID"),
            to_number=(os.getenv("WHATSAPP_TO_NUMBER") or os.getenv("WHATSAPP_TO_PHONE_NUMBER")),
            graph_api_version=(
                os.getenv("WHATSAPP_GRAPH_API_VERSION")
                or os.getenv("WHATSAPP_API_VERSION")
                or "v23.0"
            ),
            timeout_seconds=int(timeout or "20"),
        )

    def send_text_message(
        self,
        message: str,
        to_number: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message, or return a clear skipped/error result."""

        if not self.enabled:
            return _result(
                sent=False,
                status="disabled",
                message="WhatsApp notifications are disabled.",
            )

        clean_message = _clean_message(message)
        if clean_message is None:
            return _result(
                sent=False,
                status="invalid_message",
                message="WhatsApp message was not sent.",
                error="message must be a non-empty string.",
            )
        if len(clean_message) > MAX_TEXT_MESSAGE_LENGTH:
            return _result(
                sent=False,
                status="invalid_message",
                message="WhatsApp message was not sent.",
                error=f"message must be {MAX_TEXT_MESSAGE_LENGTH} characters or fewer.",
            )

        recipient = _clean_optional_text(to_number) or self.to_number
        missing = self._missing_configuration(recipient)
        if missing:
            return _result(
                sent=False,
                status="configuration_error",
                message="WhatsApp message was not sent.",
                error="Missing WhatsApp configuration: " + ", ".join(missing) + ".",
            )

        request = self._build_request(clean_message, recipient or "")

        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status_code = _response_status(response)
                body = _read_text(response)
        except HTTPError as exc:
            return _result(
                sent=False,
                status="http_error",
                message="WhatsApp API rejected the message.",
                error=f"HTTP {exc.code}: {self._safe_error_text(_read_text(exc))}",
            )
        except (TimeoutError, socket.timeout) as exc:
            return _result(
                sent=False,
                status="timeout",
                message="WhatsApp message timed out.",
                error=redact_text(str(exc), secrets=(self.access_token,)),
            )
        except URLError as exc:
            if _is_timeout_reason(exc.reason):
                return _result(
                    sent=False,
                    status="timeout",
                    message="WhatsApp message timed out.",
                    error=redact_text(str(exc.reason), secrets=(self.access_token,)),
                )
            return _result(
                sent=False,
                status="network_error",
                message="WhatsApp network request failed.",
                error=redact_text(str(exc.reason), secrets=(self.access_token,)),
            )

        if status_code < 200 or status_code >= 300:
            return _result(
                sent=False,
                status="http_error",
                message="WhatsApp API rejected the message.",
                error=f"HTTP {status_code}: {self._safe_error_text(body)}",
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            return _result(
                sent=False,
                status="invalid_json",
                message="WhatsApp API returned invalid JSON.",
                error=str(exc),
            )

        if not isinstance(payload, dict):
            return _result(
                sent=False,
                status="response_error",
                message="WhatsApp API returned an unexpected response.",
                error="response must be a JSON object.",
            )

        return _result(
            sent=True,
            status="sent",
            message="WhatsApp message accepted by the API.",
            data={"response": payload},
        )

    def _missing_configuration(self, recipient: str | None) -> list[str]:
        missing = []
        if not self.access_token:
            missing.append("WHATSAPP_ACCESS_TOKEN")
        if not self.phone_number_id:
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        if not recipient:
            missing.append("WHATSAPP_TO_NUMBER")

        return missing

    def _build_request(self, message: str, to_number: str) -> Request:
        url = f"{self.base_url}/{self.graph_api_version}/{self.phone_number_id}/messages"
        body = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message,
            },
        }
        data = json.dumps(body).encode("utf-8")
        return Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "User-Agent": "crypto-market-agents/0.1",
            },
            method="POST",
        )

    def _safe_error_text(self, value: str) -> str:
        return _truncate(redact_text(value, secrets=(self.access_token,)))


def _result(
    *,
    sent: bool,
    status: str,
    message: str,
    error: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return NotificationResult(
        sent=sent,
        channel=CHANNEL,
        status=status,
        message=message,
        error=error,
        data=redact_mapping(data or {}),
    ).to_dict()


def _clean_message(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    return cleaned or None


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    return cleaned or None


def _clean_graph_api_version(value: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError("graph_api_version cannot be empty.")
    if not cleaned.startswith("v"):
        cleaned = f"v{cleaned}"

    return cleaned


def _clean_base_url(base_url: str) -> str:
    cleaned = str(base_url).strip().rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be a valid http(s) URL.")

    return cleaned


def _validate_timeout(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timeout_seconds must be an integer.")
    if value < 1 or value > 120:
        raise ValueError("timeout_seconds must be between 1 and 120.")

    return value


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    return default


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()

    return int(status) if isinstance(status, int) else 0


def _read_text(response: Any) -> str:
    raw_body = response.read()
    if isinstance(raw_body, str):
        return raw_body

    return bytes(raw_body).decode("utf-8", errors="replace")


def _is_timeout_reason(reason: Any) -> bool:
    return isinstance(reason, TimeoutError | socket.timeout) or "timed out" in str(reason).lower()


def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value

    return f"{value[:limit]}..."

"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from crypto_market_agents.security import validate_security_environment


class ConfigError(ValueError):
    """Raised when application configuration is invalid."""


@dataclass(frozen=True)
class CoinGeckoConfig:
    """CoinGecko client configuration."""

    base_url: str
    api_key: str | None
    timeout_seconds: int


@dataclass(frozen=True)
class NewsConfig:
    """News provider configuration."""

    provider: str
    api_key: str | None
    base_url: str
    timeout_seconds: int
    language: str
    default_query: str
    max_articles: int
    cryptopanic_api_key: str | None


@dataclass(frozen=True)
class DefiLlamaConfig:
    """DefiLlama client configuration."""

    base_url: str
    timeout_seconds: int


@dataclass(frozen=True)
class HTTPConfig:
    """Shared HTTP robustness configuration for read-only API clients."""

    max_retries: int
    backoff_seconds: float
    cache_ttl_seconds: int
    cache_enabled: bool


@dataclass(frozen=True)
class LLMConfig:
    """LLM synthesis configuration."""

    enabled: bool
    api_key: str | None
    model: str | None


@dataclass(frozen=True)
class WhatsAppConfig:
    """WhatsApp notification configuration."""

    enabled: bool
    access_token: str | None
    phone_number_id: str | None
    to_number: str | None
    graph_api_version: str
    timeout_seconds: int

    @property
    def to_phone_number(self) -> str | None:
        """Backward-compatible alias for older configuration code."""

        return self.to_number

    @property
    def api_version(self) -> str:
        """Backward-compatible alias for older configuration code."""

        return self.graph_api_version


@dataclass(frozen=True)
class SecurityConfig:
    """Read-only security posture configuration."""

    exchange_mode: str
    trading_enabled: bool
    withdrawals_enabled: bool
    order_execution_enabled: bool


@dataclass(frozen=True)
class AppConfig:
    """Validated application configuration."""

    app_env: str
    log_level: str
    base_currency: str
    watchlist: tuple[str, ...]
    report_language: str
    report_output_dir: Path
    request_timeout_seconds: int
    cache_ttl_seconds: int
    alert_risk_threshold: str
    http: HTTPConfig
    coingecko: CoinGeckoConfig
    news: NewsConfig
    defillama: DefiLlamaConfig
    llm: LLMConfig
    whatsapp: WhatsAppConfig
    security: SecurityConfig


def load_config(
    env_file: str | Path | None = None,
    *,
    include_os_environ: bool = True,
) -> AppConfig:
    """Load and validate the app configuration.

    Values from the operating system environment override values from the env file.
    """

    env_path = Path(env_file) if env_file is not None else Path(".env")
    env: dict[str, str] = {}

    if env_path.exists():
        env.update(read_env_file(env_path))

    if include_os_environ:
        env.update(os.environ)

    validate_security_environment(env)

    app_env = _get_choice(env, "APP_ENV", "development", {"development", "test", "production"})
    log_level = _get_choice(env, "LOG_LEVEL", "INFO", {"DEBUG", "INFO", "WARNING", "ERROR"})
    base_currency = _get_non_empty(env, "BASE_CURRENCY", "usd").lower()
    watchlist = _get_csv(env, "WATCHLIST", "bitcoin,ethereum,solana")
    report_language = _get_choice(env, "REPORT_LANGUAGE", "fr", {"fr", "en"})
    report_output_dir = Path(_get_non_empty(env, "REPORT_OUTPUT_DIR", "reports"))
    request_timeout_seconds = _get_int(env, "REQUEST_TIMEOUT_SECONDS", 20, minimum=1, maximum=120)
    legacy_cache_ttl_seconds = _get_int(
        env,
        "CACHE_TTL_SECONDS",
        60,
        minimum=0,
        maximum=86400,
    )
    http = HTTPConfig(
        max_retries=_get_int(env, "HTTP_MAX_RETRIES", 2, minimum=0, maximum=10),
        backoff_seconds=_get_float(
            env,
            "HTTP_BACKOFF_SECONDS",
            0.5,
            minimum=0.0,
            maximum=60.0,
        ),
        cache_ttl_seconds=_get_int(
            env,
            "HTTP_CACHE_TTL_SECONDS",
            legacy_cache_ttl_seconds,
            minimum=0,
            maximum=86400,
        ),
        cache_enabled=_get_bool(env, "HTTP_CACHE_ENABLED", True),
    )
    cache_ttl_seconds = http.cache_ttl_seconds
    alert_risk_threshold = _get_choice(
        env,
        "ALERT_RISK_THRESHOLD",
        "high",
        {"low", "medium", "high", "critical"},
    )

    coingecko = CoinGeckoConfig(
        base_url=_get_url(env, "COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3"),
        api_key=_get_optional(env, "COINGECKO_API_KEY"),
        timeout_seconds=_get_int(
            env,
            "COINGECKO_TIMEOUT",
            request_timeout_seconds,
            minimum=1,
            maximum=120,
        ),
    )
    news = NewsConfig(
        provider=_get_choice(
            env,
            "NEWS_PROVIDER",
            "newsapi",
            {"newsapi", "cryptopanic", "disabled"},
        ),
        api_key=_get_optional(env, "NEWS_API_KEY"),
        base_url=_get_url(env, "NEWS_BASE_URL", "https://newsapi.org/v2"),
        timeout_seconds=_get_int(
            env,
            "NEWS_TIMEOUT",
            request_timeout_seconds,
            minimum=1,
            maximum=120,
        ),
        language=_get_non_empty(env, "NEWS_LANGUAGE", "en").lower(),
        default_query=_get_non_empty(
            env,
            "NEWS_DEFAULT_QUERY",
            "crypto OR bitcoin OR ethereum OR blockchain",
        ),
        max_articles=_get_int(
            env,
            "NEWS_MAX_ARTICLES",
            10,
            minimum=1,
            maximum=100,
        ),
        cryptopanic_api_key=_get_optional(env, "CRYPTOPANIC_API_KEY"),
    )
    defillama = DefiLlamaConfig(
        base_url=_get_url(env, "DEFILLAMA_BASE_URL", "https://api.llama.fi"),
        timeout_seconds=_get_int(
            env,
            "DEFILLAMA_TIMEOUT",
            request_timeout_seconds,
            minimum=1,
            maximum=120,
        ),
    )
    llm = LLMConfig(
        enabled=_get_bool(env, "LLM_ENABLED", False),
        api_key=_get_optional(env, "OPENAI_API_KEY"),
        model=_get_optional(env, "LLM_MODEL"),
    )
    whatsapp = WhatsAppConfig(
        enabled=_get_bool(env, "WHATSAPP_ENABLED", False),
        access_token=_get_optional(env, "WHATSAPP_ACCESS_TOKEN"),
        phone_number_id=_get_optional(env, "WHATSAPP_PHONE_NUMBER_ID"),
        to_number=(
            _get_optional(env, "WHATSAPP_TO_NUMBER")
            or _get_optional(env, "WHATSAPP_TO_PHONE_NUMBER")
        ),
        graph_api_version=_get_non_empty(
            env,
            "WHATSAPP_GRAPH_API_VERSION",
            _get_optional(env, "WHATSAPP_API_VERSION") or "v23.0",
        ),
        timeout_seconds=_get_int(
            env,
            "WHATSAPP_TIMEOUT",
            request_timeout_seconds,
            minimum=1,
            maximum=120,
        ),
    )
    security = SecurityConfig(
        exchange_mode=_get_non_empty(env, "EXCHANGE_MODE", "disabled").lower(),
        trading_enabled=_get_bool(env, "TRADING_ENABLED", False),
        withdrawals_enabled=_get_bool(env, "WITHDRAWALS_ENABLED", False),
        order_execution_enabled=_get_bool(env, "ORDER_EXECUTION_ENABLED", False),
    )

    _validate_feature_credentials(llm=llm, whatsapp=whatsapp)

    return AppConfig(
        app_env=app_env,
        log_level=log_level,
        base_currency=base_currency,
        watchlist=watchlist,
        report_language=report_language,
        report_output_dir=report_output_dir,
        request_timeout_seconds=request_timeout_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        alert_risk_threshold=alert_risk_threshold,
        http=http,
        coingecko=coingecko,
        news=news,
        defillama=defillama,
        llm=llm,
        whatsapp=whatsapp,
        security=security,
    )


def read_env_file(path: Path) -> dict[str, str]:
    """Read a simple dotenv file without expanding shell expressions."""

    env: dict[str, str] = {}

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line.removeprefix("export ").strip()

        if "=" not in line:
            raise ConfigError(f"Invalid env line {line_number} in {path}: missing '='")

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(value.strip())

        if not key:
            raise ConfigError(f"Invalid env line {line_number} in {path}: empty key")

        env[key] = _unquote(value)

    return env


def _validate_feature_credentials(*, llm: LLMConfig, whatsapp: WhatsAppConfig) -> None:
    if llm.enabled and (not llm.api_key or not llm.model):
        raise ConfigError("LLM_ENABLED=true requires OPENAI_API_KEY and LLM_MODEL.")


def _get_optional(env: dict[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None

    cleaned = value.strip()
    return cleaned or None


def _get_non_empty(env: dict[str, str], key: str, default: str) -> str:
    value = _get_optional(env, key)
    if value is None:
        value = default

    if not value.strip():
        raise ConfigError(f"{key} cannot be empty.")

    return value.strip()


def _get_bool(env: dict[str, str], key: str, default: bool) -> bool:
    value = _get_optional(env, key)
    if value is None:
        return default

    normalized = value.lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise ConfigError(f"{key} must be a boolean value.")


def _get_int(
    env: dict[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = _get_optional(env, key)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer.") from exc

    if parsed < minimum or parsed > maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}.")

    return parsed


def _get_float(
    env: dict[str, str],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = _get_optional(env, key)
    if value is None:
        return default

    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number.") from exc

    if parsed < minimum or parsed > maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}.")

    return parsed


def _get_choice(
    env: dict[str, str],
    key: str,
    default: str,
    choices: set[str],
) -> str:
    value = _get_non_empty(env, key, default)
    normalized = value.lower()
    normalized_choices = {choice.lower() for choice in choices}

    if normalized not in normalized_choices:
        allowed = ", ".join(sorted(choices))
        raise ConfigError(f"{key} must be one of: {allowed}.")

    return normalized if default.islower() else normalized.upper()


def _get_url(env: dict[str, str], key: str, default: str) -> str:
    value = _get_non_empty(env, key, default).rstrip("/")
    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{key} must be a valid http(s) URL.")

    return value


def _get_csv(env: dict[str, str], key: str, default: str) -> tuple[str, ...]:
    raw_value = _get_non_empty(env, key, default)
    values: list[str] = []
    seen: set[str] = set()

    for item in raw_value.split(","):
        normalized = item.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            values.append(normalized)

    if not values:
        raise ConfigError(f"{key} must contain at least one value.")

    return tuple(values)


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None

    for index, character in enumerate(value):
        if character in {"'", '"'}:
            quote = None if quote == character else character
            continue
        if character == "#" and quote is None:
            before_hash = value[:index]
            if not before_hash or before_hash.endswith(" "):
                return before_hash.strip()

    return value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]

    return value

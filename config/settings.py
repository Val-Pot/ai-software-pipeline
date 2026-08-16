"""
Application configuration via Pydantic BaseSettings.

All values are read from environment variables or a .env file.
Settings are validated once at startup and cached for the lifetime of the process.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import FrozenSet

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Central configuration object.

    Attributes are read from environment variables (case-insensitive).
    A .env file is loaded automatically if present.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------
    telegram_bot_token: str
    telegram_allowed_user_ids: str = ""   # comma-separated integers
    telegram_use_webhook: bool = False
    telegram_webhook_url: str = ""        # e.g. https://example.com
    telegram_webhook_path: str = "/telegram/webhook"

    # ------------------------------------------------------------------
    # GitHub
    # ------------------------------------------------------------------
    github_token: str = ""
    github_owner: str = ""
    github_repo: str = ""
    github_webhook_secret: str = ""

    #: Comma-separated GitHub Actions workflow names that count as CI.
    #: Empty = any completed workflow with success/failure/timed_out.
    github_ci_workflow_name: str = ""

    # ------------------------------------------------------------------
    # Coding Agent (GitHub Copilot)
    # ------------------------------------------------------------------
    #: GitHub login used to detect Copilot comments (github-copilot[bot]).
    #: Assignment uses copilot-swe-agent[bot] regardless of this value.
    copilot_username: str = "github-copilot[bot]"

    #: Seconds between polling iterations when watching an issue.
    coding_agent_poll_interval: float = 10.0

    #: Idle seconds without a watcher event before giving up (clock resets on activity).
    coding_agent_watcher_timeout: float = 3600.0

    #: Maximum retry attempts for each GitHub API call in the coding agent.
    coding_agent_max_retries: int = 3

    #: Per-request HTTP timeout (seconds) for the coding agent adapter.
    coding_agent_request_timeout: float = 15.0

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    log_level: str = "INFO"
    environment: str = "development"   # development | staging | production

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        level = v.upper()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return level

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, v: str) -> str:
        v = v.lower()
        valid = {"development", "staging", "production"}
        if v not in valid:
            raise ValueError(f"environment must be one of {valid}, got {v!r}")
        return v

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------
    @property
    def allowed_user_ids(self) -> FrozenSet[int]:
        """
        Parsed and validated set of authorised Telegram user IDs.
        Returns an empty frozenset when TELEGRAM_ALLOWED_USER_IDS is unset,
        which means *all* users are allowed (suitable for development only).
        """
        if not self.telegram_allowed_user_ids.strip():
            return frozenset()
        ids: list[int] = []
        for raw in self.telegram_allowed_user_ids.split(","):
            token = raw.strip()
            if token.isdigit():
                ids.append(int(token))
            elif token:
                logger.warning("Skipping invalid user ID in TELEGRAM_ALLOWED_USER_IDS: %r", token)
        return frozenset(ids)

    @property
    def ci_workflow_names(self) -> FrozenSet[str]:
        """Lowercased GitHub Actions workflow names treated as pipeline CI."""
        if not self.github_ci_workflow_name.strip():
            return frozenset()
        return frozenset(
            name.strip().lower()
            for name in self.github_ci_workflow_name.split(",")
            if name.strip()
        )

    @property
    def telegram_webhook_full_url(self) -> str:
        """Absolute URL that Telegram will POST updates to (webhook mode)."""
        base = self.telegram_webhook_url.rstrip("/")
        return f"{base}{self.telegram_webhook_path}"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    The instance is validated once on first call and cached for the
    lifetime of the process.  Use ``get_settings.cache_clear()`` in
    tests to force re-initialisation.
    """
    settings = Settings()  # type: ignore[call-arg]
    logging.getLogger(__name__).info(
        "Settings loaded: environment=%s, log_level=%s, webhook_mode=%s",
        settings.environment,
        settings.log_level,
        settings.telegram_use_webhook,
    )
    return settings

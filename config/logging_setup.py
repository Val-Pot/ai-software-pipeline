"""
Logging initialisation for the AI Software Pipeline.

Loads the YAML logging configuration and overrides the root log level
from the ``LOG_LEVEL`` environment variable (via Settings).

Usage (call once at process start, before importing anything else):

    from config.logging_setup import configure_logging
    configure_logging()
"""
from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "logging.yaml"


def configure_logging(log_level: str | None = None) -> None:
    """
    Load ``config/logging.yaml`` and apply it via ``logging.config.dictConfig``.

    Parameters
    ----------
    log_level:
        Override the root log level.  Reads ``LOG_LEVEL`` env var when
        ``None`` and falls back to ``INFO``.
    """
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            config_dict = yaml.safe_load(fh)
        logging.config.dictConfig(config_dict)
    else:
        # Fallback: basic stderr handler if the YAML file is missing.
        logging.basicConfig(
            format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )

    # Apply effective log level from settings / env var.
    level = (
        log_level
        or os.environ.get("LOG_LEVEL", "INFO")
    ).upper()

    numeric = getattr(logging, level, logging.INFO)
    logging.getLogger().setLevel(numeric)

    logger.debug("Logging configured: level=%s, config_path=%s", level, _CONFIG_PATH)

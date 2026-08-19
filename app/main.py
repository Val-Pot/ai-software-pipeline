from __future__ import annotations

import logging

import uvicorn

from app.lifespan import create_app
from config.settings import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

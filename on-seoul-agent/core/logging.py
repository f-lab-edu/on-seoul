import logging

from core.config import settings


def setup_logging() -> None:
    """어플리케이션 Logger 설정"""
    logger = logging.getLogger("on_seoul_agent")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
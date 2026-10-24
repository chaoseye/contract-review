"""Настройка логов.

В логи пишутся только идентификаторы и события. Тексты договоров и ответы ИИ туда не попадают.
"""

import logging

# HTTP-клиент и SDK openai и anthropic при подробных логах пишут адреса и сведения о запросах к ИИ.
# Их уровень не ниже WARNING, даже если LOG_LEVEL=DEBUG (documentation/DATA_HANDLING.md, раздел 6).
QUIET_LOGGERS = ("openai", "anthropic", "httpx", "httpx2", "httpcore", "httpcore2")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

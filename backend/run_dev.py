"""Локальный запуск без Docker: миграции, первый администратор, worker и сервер на http://127.0.0.1:8000.

    backend/.venv/Scripts/python backend/run_dev.py      (Windows)
    backend/.venv/bin/python backend/run_dev.py          (Linux, macOS)

База по умолчанию — SQLite в backend/data/dev.db. Настройки берутся из .env в корне репозитория.
Worker запускается в том же процессе (флаг --no-worker отключает). Если интерфейс собран (frontend/dist),
он открывается на том же адресе; для разработки интерфейса удобнее `npm run dev` во frontend/.
"""

import argparse
import threading

import uvicorn

from app.cli import bootstrap, migrate
from app.core.config import get_settings
from app.jobs.worker import run_worker
from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный запуск contract-review")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-worker", action="store_true", help="не запускать фоновые задачи")
    args = parser.parse_args()

    settings = get_settings()
    migrate(settings)
    bootstrap(settings)
    stop = threading.Event()
    worker = None
    if not args.no_worker:
        worker = threading.Thread(target=run_worker, args=(settings, stop), name="worker", daemon=True)
        worker.start()
    try:
        # Как в образе (deploy/entrypoint.sh): без заголовка Server.
        uvicorn.run(create_app(settings), host=args.host, port=args.port, server_header=False)
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=10)


if __name__ == "__main__":
    main()

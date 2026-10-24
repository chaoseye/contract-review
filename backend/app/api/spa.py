"""Раздача собранного интерфейса (frontend/dist) с возвратом index.html для маршрутов SPA."""

from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()
    index = root / "index.html"
    assets = root / "assets"
    if assets.is_dir():
        # Имена файлов в assets содержат хеш, их можно кэшировать надолго.
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    # HEAD нужен проверкам доступности, которые не делают GET.
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        # Путь с расширением — это запрос файла (favicon.ico, старый chunk.js), а не маршрут интерфейса.
        # Отдавать вместо него index.html нельзя: браузер получит HTML под видом скрипта или картинки.
        if PurePosixPath(full_path).suffix:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
        if not index.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Интерфейс не собран")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

"""Приложение FastAPI: API и раздача интерфейса."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.analysis.prompt_runs import PromptRuns
from app.api import admin, auth, checks, health, limits, protocol
from app.api.middleware import CsrfHeaderMiddleware, SecurityHeadersMiddleware, UploadSizeMiddleware
from app.api.ratelimit import LoginLimiter
from app.api.spa import mount_spa
from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="Проверка договоров",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.api_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.api_docs else None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.login_limiter = LoginLimiter()
    # Админка: прогоны черновика промпта и вход в Codex живут в памяти процесса (в образе он один).
    app.state.prompt_runs = PromptRuns()
    app.state.codex_login = None
    # Подмена HTTP-клиента подключений ИИ и команды Codex — только для тестов.
    app.state.llm_http_client = None
    app.state.codex_command = None

    app.add_middleware(UploadSizeMiddleware)
    app.add_middleware(CsrfHeaderMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(checks.router)
    app.include_router(protocol.router)
    app.include_router(limits.router)
    app.include_router(admin.router)

    # Последним: перехватывает все остальные GET-пути и отдаёт интерфейс.
    static_dir = settings.resolved_static_dir()
    if static_dir is not None:
        mount_spa(app, static_dir)
    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn app.main:app` создаёт приложение только при обращении, а не при импорте модуля:
    # тесты импортируют create_app и не требуют настроек из окружения.
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)

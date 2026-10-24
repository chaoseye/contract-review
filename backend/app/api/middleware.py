"""Промежуточные обработчики: защита от CSRF, размер запросов, заголовки безопасности."""

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "contract-review"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Предел тела остальных изменяющих запросов к API: самый большой из них — загрузка файла настроек (40 МБ).
MAX_API_BODY_BYTES = 48 * 1024 * 1024
# Политика для страниц интерфейса. Скрипты — только свои; стили встроенные, потому что Ant Design
# вставляет их на странице сам; картинки — свои и data: (иконка страницы). Внешних адресов нет.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


class CsrfHeaderMiddleware(BaseHTTPMiddleware):
    """Изменяющие запросы к API принимаются только с заголовком X-Requested-With: contract-review.

    Чужой сайт не может отправить такой заголовок без CORS-разрешения, а CORS система не включает.
    Вместе с cookie SameSite=Lax этого достаточно для защиты от CSRF.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if (
            request.method not in SAFE_METHODS
            and request.url.path.startswith("/api/")
            and request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE
        ):
            return JSONResponse({"detail": "Запрос отклонён: нет заголовка защиты"}, status_code=403)
        return await call_next(request)


class UploadSizeMiddleware(BaseHTTPMiddleware):
    """Слишком большой запрос отклоняем по Content-Length, не дожидаясь, пока он запишется на диск.

    Загрузка договора ограничена настройкой из админки, остальные изменяющие запросы к API —
    MAX_API_BODY_BYTES. Окончательная проверка размера договора — в обработчике загрузки (запрос может
    прийти без Content-Length).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        length = request.headers.get("content-length")
        size = int(length) if length and length.isdigit() else None
        if request.method == "POST" and request.url.path == "/api/checks":
            limits = await run_in_threadpool(_limits, request)
            # Запас на служебные части multipart-запроса.
            if size is not None and size > limits.max_upload_bytes + 1024 * 1024:
                message = f"Файл больше {limits.max_upload_mb} МБ. Загрузите договор меньшего размера."
                return JSONResponse({"detail": message}, status_code=413)
        elif (
            request.method not in SAFE_METHODS
            and request.url.path.startswith("/api/")
            and size is not None
            and size > MAX_API_BODY_BYTES
        ):
            return JSONResponse({"detail": "Слишком большой запрос."}, status_code=413)
        return await call_next(request)


def _limits(request: Request):  # type: ignore[no-untyped-def]
    from app.runtime import get_limits

    with request.app.state.sessionmaker() as db:
        return get_limits(db, request.app.state.settings)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:
            # Страницы и файлы интерфейса. Ответы API — JSON и файлы для скачивания, им политика не нужна,
            # а /api/docs (Swagger) загружает свои скрипты с CDN и с ней не открылся бы.
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

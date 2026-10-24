"""Лимиты для интерфейса менеджера: максимальный размер файла и срок жизни проверки."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import runtime
from app.api.deps import AppSettings, CurrentUser, DbSession

router = APIRouter(prefix="/api", tags=["checks"])


class LimitsOut(BaseModel):
    check_ttl_hours: int
    max_upload_mb: int


@router.get("/limits", response_model=LimitsOut)
def get_limits(_user: CurrentUser, db: DbSession, settings: AppSettings) -> LimitsOut:
    limits = runtime.get_limits(db, settings)
    return LimitsOut(check_ttl_hours=limits.check_ttl_hours, max_upload_mb=limits.max_upload_mb)

"""Админка (documentation/ARCHITECTURE.md, разделы 7.2 и 8): всё под /api/admin, только для администратора.

Роль проверяется зависимостью на всём роутере и ещё раз в каждом обработчике (AdminUser).
"""

from fastapi import APIRouter, Depends

from app.api.admin import audit_log, llm, organization, prompts, protocol_template, transfer, users
from app.api.deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
for module in (users, prompts, llm, organization, protocol_template, audit_log, transfer):
    router.include_router(module.router)

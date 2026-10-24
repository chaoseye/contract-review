"""Промпт администратора: версии, активация (откат), прогон черновика на договоре."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.analysis import profiles, prompts
from app.analysis.prompt import build_request, prompt_fingerprint
from app.analysis.prompt_runs import PromptRun, PromptRuns, RunBusy
from app.analysis.providers.base import ProviderError
from app.analysis.requisites import check_info
from app.analysis.run import AnalysisOutcome
from app.api.admin.llm import provider_for
from app.api.deps import AdminUser, AppSettings, DbSession
from app.core.crypto import SecretKeyError
from app.core.db import session_scope
from app.models import Check, LlmProfile, PromptVersion, User

router = APIRouter(prefix="/prompts")

RUN_RESULT = {"done": "готово", "failed": "ошибка", "running": "идёт"}


class PromptVersionOut(BaseModel):
    id: int
    comment: str
    author: str | None
    created_at: datetime
    is_active: bool
    fingerprint: str
    length: int


class PromptVersionFull(PromptVersionOut):
    text: str


class PromptCreate(BaseModel):
    text: str = Field(min_length=1, max_length=prompts.MAX_PROMPT_LENGTH)
    comment: str = Field(default="", max_length=500)
    activate: bool = True


class PromptTestIn(BaseModel):
    text: str = Field(min_length=1, max_length=prompts.MAX_PROMPT_LENGTH)
    check_id: str = Field(min_length=1, max_length=32)
    # Другое подключение для сравнения моделей; по умолчанию — активное.
    profile_id: int | None = None


class PromptRunOut(BaseModel):
    id: str
    check_id: str
    profile: str
    fingerprint: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    result: dict[str, Any] | None
    usage: dict[str, Any]
    error: str | None
    detail: str | None


def _authors(db: Session, versions: list[PromptVersion]) -> dict[int, str]:
    ids = {version.author_id for version in versions if version.author_id is not None}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.login).where(User.id.in_(ids))).all())


def _out(db: Session, versions: list[PromptVersion], full: bool = False) -> list[Any]:
    authors = _authors(db, versions)
    model = PromptVersionFull if full else PromptVersionOut
    return [
        model(
            id=version.id,
            comment=version.comment,
            author=authors.get(version.author_id) if version.author_id else None,
            created_at=version.created_at,
            is_active=version.is_active,
            fingerprint=version.fingerprint,
            length=len(version.text),
            **({"text": version.text} if full else {}),
        )
        for version in versions
    ]


def _get(db: Session, version_id: int) -> PromptVersion:
    version = db.get(PromptVersion, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия промпта не найдена")
    return version


@router.get("", response_model=list[PromptVersionOut])
def list_versions(db: DbSession, _admin: AdminUser) -> list[Any]:
    versions = prompts.list_versions(db)
    db.commit()  # первая версия создаётся при первом обращении
    return _out(db, versions)


@router.get("/{version_id}", response_model=PromptVersionFull)
def get_version(version_id: int, db: DbSession, _admin: AdminUser) -> Any:
    return _out(db, [_get(db, version_id)], full=True)[0]


@router.post("", response_model=PromptVersionFull, status_code=status.HTTP_201_CREATED)
def create_version(payload: PromptCreate, db: DbSession, admin: AdminUser) -> Any:
    try:
        version = prompts.create_version(
            db, payload.text, payload.comment, admin.id, activate=payload.activate
        )
    except prompts.PromptError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit.record(
        db,
        "prompt_create",
        user_id=admin.id,
        prompt_version=version.fingerprint,
        details=f"версия {version.id}{', активна' if version.is_active else ''}",
    )
    db.commit()
    return _out(db, [version], full=True)[0]


@router.post("/{version_id}/activate", response_model=PromptVersionFull)
def activate_version(version_id: int, db: DbSession, admin: AdminUser) -> Any:
    version = _get(db, version_id)
    if not version.is_active:
        previous = prompts.active_version(db)
        prompts.activate_version(db, version)
        audit.record(
            db,
            "prompt_activate",
            user_id=admin.id,
            prompt_version=version.fingerprint,
            details=f"версия {previous.id} → {version.id}",
        )
        db.commit()
    return _out(db, [version], full=True)[0]


def _run_out(run: PromptRun) -> PromptRunOut:
    return PromptRunOut(
        id=run.id,
        check_id=run.check_id,
        profile=run.profile,
        fingerprint=run.fingerprint,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        result=run.result,
        usage=run.usage,
        error=run.error,
        detail=run.detail,
    )


@router.post("/test", response_model=PromptRunOut, status_code=status.HTTP_202_ACCEPTED)
def start_test(
    payload: PromptTestIn, request: Request, db: DbSession, admin: AdminUser, settings: AppSettings
) -> PromptRunOut:
    """Прогнать черновик на прочитанном договоре. Результат не сохраняется."""
    check = db.get(Check, payload.check_id)
    if check is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проверка не найдена")
    if not check.blocks:
        raise HTTPException(status.HTTP_409_CONFLICT, "В этой проверке документ ещё не прочитан.")
    if payload.profile_id is not None:
        profile: LlmProfile | None = db.get(LlmProfile, payload.profile_id)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Подключение не найдено")
    else:
        profile = profiles.active_profile(db)
        if profile is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Нет активного подключения ИИ.")
    try:
        provider = provider_for(request, profile, settings)
    except (profiles.ProfileError, SecretKeyError, ProviderError) as exc:
        message = exc.message if isinstance(exc, ProviderError) else str(exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from exc
    text = payload.text.strip()
    fingerprint = prompt_fingerprint(text)
    analysis_request = build_request(check.blocks, check_info(db, check), text)
    factory = request.app.state.sessionmaker
    check_id, admin_id, model = check.id, admin.id, profile.model

    def on_finish(run: PromptRun, outcome: AnalysisOutcome | None) -> None:
        usage = outcome.usage if outcome is not None else None
        with session_scope(factory) as session:
            audit.record(
                session,
                "prompt_test",
                user_id=admin_id,
                check=session.get(Check, check_id),
                check_id=check_id,
                model=outcome.model if outcome is not None else model,
                tokens_in=usage.input_tokens if usage else None,
                tokens_out=usage.output_tokens if usage else None,
                cost_rub=usage.cost_rub if usage else None,
                prompt_version=fingerprint,
                details=f"черновик, подключение «{run.profile}»: {RUN_RESULT[run.status]}",
            )

    runs: PromptRuns = request.app.state.prompt_runs
    try:
        run = runs.start(
            user_id=admin.id,
            check_id=check.id,
            profile=profile.name,
            fingerprint=fingerprint,
            provider=provider,
            request=analysis_request,
            blocks=check.blocks,
            on_finish=on_finish,
        )
    except RunBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _run_out(run)


@router.get("/test/{run_id}", response_model=PromptRunOut)
def get_test(run_id: str, request: Request, db: DbSession, _admin: AdminUser) -> PromptRunOut:
    runs: PromptRuns = request.app.state.prompt_runs
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Прогон не найден или устарел")
    # Проверку удалили — вместе с ней пропадает и ответ модели по её договору.
    if db.get(Check, run.check_id) is None and run.status != "running":
        runs.forget(run_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проверка удалена — результат прогона тоже")
    return _run_out(run)

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import SESSION_COOKIE
from app.api.middleware import CSRF_HEADER
from app.core.db import utcnow
from app.core.security import hash_session_token
from app.main import create_app
from app.models import UserRole, UserSession
from tests.conftest import PASSWORD, TEST_SECRET, add_user, make_settings, migrate


def login(client: TestClient, login: str = "manager", password: str = PASSWORD):
    return client.post("/api/auth/login", json={"login": login, "password": password})


def test_login_sets_http_only_cookie_and_me_returns_user(client: TestClient, db_factory) -> None:
    add_user(db_factory)
    response = login(client)
    assert response.status_code == 200
    assert response.json() == {"id": 1, "login": "manager", "name": "Manager", "role": "manager"}

    set_cookie = response.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE}=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie  # PUBLIC_URL по http

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["login"] == "manager"


def test_login_is_case_insensitive_and_trims(client: TestClient, db_factory) -> None:
    add_user(db_factory)
    assert login(client, login="  MANAGER ").status_code == 200


def test_session_token_is_stored_hashed(client: TestClient, db_factory: sessionmaker[Session]) -> None:
    add_user(db_factory)
    login(client)
    token = client.cookies[SESSION_COOKIE]
    with db_factory() as db:
        stored = db.scalars(select(UserSession.token_hash)).all()
    assert stored == [hash_session_token(token, TEST_SECRET)]
    assert token not in stored


def test_wrong_password_and_unknown_user_give_same_error(client: TestClient, db_factory) -> None:
    add_user(db_factory)
    wrong = login(client, password="wrong-password")
    unknown = login(client, login="nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert SESSION_COOKIE not in client.cookies


def test_inactive_user_cannot_login(client: TestClient, db_factory) -> None:
    add_user(db_factory, is_active=False)
    response = login(client)
    assert response.status_code == 403
    assert SESSION_COOKIE not in client.cookies


def test_me_requires_login(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401


def test_logout_ends_session(client: TestClient, db_factory: sessionmaker[Session]) -> None:
    add_user(db_factory)
    login(client)
    token = client.cookies[SESSION_COOKIE]
    assert client.post("/api/auth/logout").status_code == 204
    with db_factory() as db:
        assert db.scalars(select(UserSession)).all() == []
    # Старый токен больше не работает, даже если его подставить вручную.
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/me").status_code == 401


def test_expired_session_is_rejected(client: TestClient, db_factory: sessionmaker[Session]) -> None:
    add_user(db_factory)
    login(client)
    with db_factory() as db:
        record = db.scalars(select(UserSession)).one()
        record.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/me").status_code == 401


def test_deactivated_user_loses_access(client: TestClient, db_factory: sessionmaker[Session]) -> None:
    from app.models import User

    user_id = add_user(db_factory)
    login(client)
    with db_factory() as db:
        db.get(User, user_id).is_active = False
        db.commit()
    assert client.get("/api/me").status_code == 401


def test_session_is_extended_on_activity(client: TestClient, db_factory: sessionmaker[Session]) -> None:
    add_user(db_factory)
    login(client)
    with db_factory() as db:
        record = db.scalars(select(UserSession)).one()
        record.last_seen_at = utcnow() - timedelta(hours=1)
        record.expires_at = utcnow() + timedelta(minutes=10)
        db.commit()
    assert client.get("/api/me").status_code == 200
    with db_factory() as db:
        record = db.scalars(select(UserSession)).one()
        assert record.expires_at > utcnow() + timedelta(hours=11)


def test_post_without_csrf_header_is_rejected(client: TestClient, db_factory) -> None:
    add_user(db_factory)
    del client.headers[CSRF_HEADER]
    response = login(client)
    assert response.status_code == 403
    assert SESSION_COOKIE not in client.cookies


def test_cookie_is_secure_when_public_url_is_https(tmp_path, db_factory) -> None:
    settings = make_settings(tmp_path, public_url="https://contracts.example.ru")
    migrate(settings)
    add_user(db_factory)
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        response = client.post(
            "/api/auth/login",
            json={"login": "manager", "password": PASSWORD},
            headers={CSRF_HEADER: "contract-review"},
        )
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_admin_flag(db_factory, client: TestClient) -> None:
    add_user(db_factory, login="boss", role=UserRole.ADMIN)
    response = login(client, login="boss")
    assert response.json()["role"] == "admin"

"""Configuració dels tests: motor cap a la base de dades de tests i sessió aïllada per test.

Cada test s'executa dins d'una transacció que es desfà al final (rollback),
de manera que queden aïllats entre si.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.models import User
from app.security import compute_email_hash, hash_password

REPO_ROOT = Path(__file__).resolve().parents[2]
CATEGORIES_FILE = REPO_ROOT / "data" / "prompts" / "categories.yaml"

DEFAULT_PASSWORD = "ContrasenyaSegura123!"


@pytest.fixture(scope="session")
def engine():
    """Motor cap a la base de dades de tests; crea l'esquema un sol cop."""
    settings = get_settings()
    eng = create_engine(settings.database_test_url, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with Session(eng) as seed_session:
        document = yaml.safe_load(CATEGORIES_FILE.read_text(encoding="utf-8"))
        seed_session.add_all([models.Category(**category) for category in document["categories"]])
        seed_session.commit()
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """Sessió embolcallada en una transacció externa que es desfà sempre."""
    connection = engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield sess
    finally:
        sess.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(session):
    """TestClient de FastAPI amb la dependència `get_db` apuntant a la sessió de test."""

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    # Base URL amb HTTPS perquè el client accepti i reenviï cookies marcades com a
    # `Secure` (com passa en producció amb `COOKIE_SECURE=true`).
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def create_user(session):
    """Fàbrica per crear usuaris de prova (verificats o no) directament a la base de dades."""

    def _create(
        email: str,
        *,
        verified: bool = True,
        password: str = DEFAULT_PASSWORD,
        consent_version: str = "v1",
    ) -> User:
        user = User(
            email=email,
            email_hash=compute_email_hash(email),
            password_hash=hash_password(password),
            consent_version=consent_version,
            consent_at=datetime.now(UTC),
            email_verified_at=datetime.now(UTC) if verified else None,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    return _create


@pytest.fixture
def login(client):
    """Fàbrica per iniciar sessió via l'API; deixa la cookie de sessió al client."""

    def _login(email: str, password: str = DEFAULT_PASSWORD):
        response = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert response.status_code == 200
        return response

    return _login


@pytest.fixture
def logged_in_user(create_user, login):
    """Crea un usuari verificat i n'inicia la sessió, retornant l'usuari."""

    def _make(email: str, password: str = DEFAULT_PASSWORD) -> User:
        user = create_user(email, verified=True, password=password)
        login(email, password)
        return user

    return _make

"""Demostració i comprovació del flux d'autenticació contra un servidor real.

Aquest script recorre tot el cicle de vida d'un usuari (registre, verificació,
login, obtenció de tasca, vot, exportació i baixa) fent peticions HTTP reals al
backend en marxa. Per a cada pas imprimeix la petició, l'status i el cos de la
resposta, i marca ✓/✗. Si algun pas no retorna l'status esperat, atura el flux i
surt amb codi ≠ 0, de manera que serveix alhora per **visualitzar** i per
**comprovar** el flux.

El token de verificació de correu no es retorna per l'API (a la v1 només s'escriu
al log). L'script el genera localment amb `app.security`, que comparteix la mateixa
`HMAC_SECRET_KEY` del `.env` amb el servidor; per obtenir l'`user_id` consulta la
base de dades directament.

Setup de servidor + PostgreSQL
------------------------------
Des de l'arrel del repositori:

    cp .env.example .env
    docker compose up -d --wait

Des de `backend/`:

    uv sync                                  # instal·la dependències
    uv run alembic upgrade head              # aplica les migracions
    uv run python scripts/auth_flow_demo.py

Opcions:

    --base-url URL       URL del backend (per defecte http://localhost:8000).
    --category CODI      Categoria per demanar la tasca (per defecte "correccio").
    --keep-account       No dona de baixa el compte; fa logout i el deixa viu.
    --seed-demo-data     Insereix un prompt i dues respostes efímeres abans del pas
                         de tasca i les esborra en acabar, per provar tasca i vot
                         sense dependre de la càrrega d'inferències.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any

# Permet importar el paquet `app` quan s'executa l'script des de qualsevol directori.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx2 as httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import get_sessionmaker  # noqa: E402
from app.models import Category, Prompt, Response, User, Vote  # noqa: E402
from app.security import compute_email_hash, create_email_verification_token  # noqa: E402

# Colors ANSI per a la sortida (buits si no és un terminal).
_TTY = sys.stdout.isatty()
GREEN = "\033[32m" if _TTY else ""
RED = "\033[31m" if _TTY else ""
YELLOW = "\033[33m" if _TTY else ""
BLUE = "\033[34m" if _TTY else ""
DIM = "\033[2m" if _TTY else ""
BOLD = "\033[1m" if _TTY else ""
RESET = "\033[0m" if _TTY else ""


class FlowError(Exception):
    """Un pas del flux no ha retornat l'status esperat."""


def _print_step(title: str) -> None:
    print(f"\n{BOLD}{BLUE}▶ {title}{RESET}")


def _print_request(method: str, url: str, body: Any | None = None) -> None:
    print(f"  {DIM}→ {method} {url}{RESET}")
    if body is not None:
        print(f"  {DIM}  body: {body}{RESET}")


def _print_response(resp: httpx.Response, expected: int) -> None:
    ok = resp.status_code == expected
    mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    color = GREEN if ok else RED
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    print(f"  {mark} status {color}{resp.status_code}{RESET} (esperat {expected})")
    print(f"  {DIM}  resposta: {body}{RESET}")


def _expect(resp: httpx.Response, expected: int) -> Any:
    """Imprimeix la resposta i llança FlowError si l'status no és l'esperat."""
    _print_response(resp, expected)
    if resp.status_code != expected:
        raise FlowError(f"status {resp.status_code}, esperat {expected}")
    try:
        return resp.json()
    except Exception:
        return None


def _lookup_verification_token(email: str) -> str:
    """Obté l'user_id de la BD i genera un token de verificació vàlid."""
    session_factory = get_sessionmaker()
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email_hash == compute_email_hash(email)))
        if user is None:
            raise FlowError(f"no s'ha trobat l'usuari {email} a la base de dades")
        return create_email_verification_token(user.id, email)


# Codi del prompt efímer que insereix --seed-demo-data (fora del rang de dades reals).
_DEMO_PROMPT_CODE = "demo_auth_flow"
_DEMO_PROMPT_VERSION = "demo"


def seed_demo_data(category_code: str) -> int:
    """Insereix un prompt efímer amb dues respostes i en retorna l'id del prompt."""
    session_factory = get_sessionmaker()
    with session_factory() as db:
        category = db.scalar(select(Category).where(Category.code == category_code))
        if category is None:
            raise FlowError(f"la categoria '{category_code}' no existeix a la base de dades")

        # Reutilitza el prompt efímer si ha quedat d'una execució anterior interrompuda.
        prompt = db.scalar(
            select(Prompt).where(
                Prompt.version == _DEMO_PROMPT_VERSION,
                Prompt.code == _DEMO_PROMPT_CODE,
            )
        )
        if prompt is None:
            prompt = Prompt(
                version=_DEMO_PROMPT_VERSION,
                code=_DEMO_PROMPT_CODE,
                category_id=category.id,
                text="Prompt de demostració del flux d'autenticació.",
            )
            db.add(prompt)
            db.flush()
            db.add_all(
                [
                    Response(prompt_id=prompt.id, model="demo-model-a", text="Resposta A de demo."),
                    Response(prompt_id=prompt.id, model="demo-model-b", text="Resposta B de demo."),
                ]
            )
            db.commit()
        return prompt.id


def cleanup_demo_data() -> None:
    """Esborra el prompt efímer i les seves respostes (cascada) i vots associats."""
    session_factory = get_sessionmaker()
    with session_factory() as db:
        prompt = db.scalar(
            select(Prompt).where(
                Prompt.version == _DEMO_PROMPT_VERSION,
                Prompt.code == _DEMO_PROMPT_CODE,
            )
        )
        if prompt is None:
            return
        # Els vots referencien les respostes amb una FK composta que l'ORM no coneix
        # com a relaci\u00f3; esborra'ls i fes flush abans d'eliminar prompt i respostes.
        for vote in db.scalars(select(Vote).where(Vote.prompt_id == prompt.id)).all():
            db.delete(vote)
        db.flush()
        db.delete(prompt)  # les respostes cauen per cascada (delete-orphan).
        db.commit()


def run_flow(base_url: str, category: str, keep_account: bool, seed_demo: bool) -> None:
    email = f"demo_{int(time.time())}@example.com"
    password = "Contrasenya-Segura-123"

    print(f"{BOLD}Flux d'autenticació contra {base_url}{RESET}")
    print(f"{DIM}Usuari de prova: {email}{RESET}")

    client = httpx.Client(base_url=base_url, timeout=10.0)

    # 0. Health check: comprovem que el servidor respon abans de començar.
    _print_step("Health check")
    _print_request("GET", "/docs")
    try:
        resp = client.get("/docs")
    except httpx.ConnectError as err:
        print(f"  {RED}✗ No s'ha pogut connectar amb {base_url}{RESET}")
        print(f"  {YELLOW}  Arrenca el servidor: uv run uvicorn app.main:app --port 8000{RESET}")
        raise FlowError(str(err)) from err
    ok = resp.status_code == 200
    mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    print(f"  {mark} status {resp.status_code} (servidor actiu)")
    if not ok:
        raise FlowError(f"health check: status {resp.status_code}")

    # 1. Registre.
    _print_step("Registre (POST /api/auth/register)")
    payload = {"email": email, "password": password, "consent": True}
    _print_request("POST", "/api/auth/register", payload)
    resp = client.post("/api/auth/register", json=payload)
    _expect(resp, 200)

    # 2. Control: login abans de verificar el correu ha de donar 403.
    _print_step("Control: login sense verificar (POST /api/auth/login) → 403")
    login_payload = {"email": email, "password": password}
    _print_request("POST", "/api/auth/login", login_payload)
    resp = client.post("/api/auth/login", json=login_payload)
    _expect(resp, 403)

    # 3. Obtenció del token de verificació (generat localment, no via API).
    _print_step("Obtenció del token de verificació (generat localment)")
    token = _lookup_verification_token(email)
    print(f"  {DIM}  token: {token}{RESET}")
    print(f"  {YELLOW}  (en producció, aquest token arribaria per correu){RESET}")

    # 4. Verificació del correu.
    _print_step("Verificació del correu (POST /api/auth/verify)")
    _print_request("POST", "/api/auth/verify", {"token": "…"})
    resp = client.post("/api/auth/verify", json={"token": token})
    _expect(resp, 200)

    # 5. Login: ara sí, amb la cookie de sessió.
    _print_step("Login (POST /api/auth/login)")
    _print_request("POST", "/api/auth/login", login_payload)
    resp = client.post("/api/auth/login", json=login_payload)
    _expect(resp, 200)
    cookie = client.cookies.get("session_token")
    if cookie is None:
        raise FlowError("el login no ha establert la cookie session_token")
    print(f"  {GREEN}✓ cookie session_token establerta{RESET} {DIM}({cookie[:12]}…){RESET}")

    # 5b. Sembra opcional de dades efímeres per poder provar tasca i vot.
    if seed_demo:
        _print_step("Sembra de dades de demo (prompt + 2 respostes)")
        prompt_id = seed_demo_data(category)
        print(f"  {GREEN}✓ prompt efímer #{prompt_id} amb dues respostes inserit{RESET}")

    # 6. Obtenció d'una tasca (requereix usuari verificat via cookie).
    _print_step(f"Obtenció d'una tasca (GET /api/task?category_code={category})")
    _print_request("GET", f"/api/task?category_code={category}")
    resp = client.get("/api/task", params={"category_code": category})
    task_token: str | None = None
    if resp.status_code == 200:
        task = _expect(resp, 200)
        task_token = task["token"]
    elif seed_demo:
        # Amb dades sembrades hem d'obtenir tasca sí o sí: un fracàs és un error dur.
        _expect(resp, 200)
    else:
        _print_response(resp, 200)
        print(
            f"  {YELLOW}  Avís: no s'ha pogut obtenir una tasca. Cal sembrar dades "
            f"(prompts amb dues respostes) per provar tasca i vot.{RESET}"
        )

    # 7. Emissió d'un vot (només si tenim tasca).
    if task_token is not None:
        _print_step("Emissió d'un vot (POST /api/vote)")
        vote_payload = {"winner": "a", "token": task_token}
        _print_request("POST", "/api/vote", {"winner": "a", "token": "…"})
        resp = client.post("/api/vote", json=vote_payload)
        _expect(resp, 200)
    else:
        print(f"\n{YELLOW}▶ Vot omès (no hi ha tasca disponible){RESET}")

    # 8. Exportació de dades (RGPD).
    _print_step("Exportació de dades (GET /api/auth/export)")
    _print_request("GET", "/api/auth/export")
    resp = client.get("/api/auth/export")
    data = _expect(resp, 200)
    n_votes = len(data.get("votes", []))
    print(f"  {GREEN}✓ exportats {n_votes} vot(s){RESET}")

    if keep_account:
        # 9a. Logout: revoca la sessió i deixa el compte viu.
        _print_step("Logout (POST /api/auth/logout)")
        _print_request("POST", "/api/auth/logout")
        resp = client.post("/api/auth/logout")
        _expect(resp, 200)

        # 10a. Control: després del logout, la sessió ja no és vàlida → 401.
        _print_step("Control: exportació després del logout → 401")
        _print_request("GET", "/api/auth/export")
        resp = client.get("/api/auth/export")
        _expect(resp, 401)
    else:
        # 9b. Baixa del compte amb reautenticació (RGPD, dret a l'oblit).
        _print_step("Baixa del compte (POST /api/auth/delete-account)")
        delete_payload = {"current_password": password}
        _print_request("POST", "/api/auth/delete-account", {"current_password": "…"})
        resp = client.post("/api/auth/delete-account", json=delete_payload)
        _expect(resp, 200)

        # 10b. Control: després de la baixa, la sessió està revocada → 401.
        _print_step("Control: exportació després de la baixa → 401")
        _print_request("GET", "/api/auth/export")
        resp = client.get("/api/auth/export")
        _expect(resp, 401)

    print(f"\n{BOLD}{GREEN}✓ Flux completat correctament.{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Demostra i comprova el flux d'autenticació.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="URL del backend.")
    parser.add_argument("--category", default="correccio", help="Categoria per demanar la tasca.")
    parser.add_argument(
        "--keep-account",
        action="store_true",
        help="No dona de baixa el compte; fa logout i el deixa viu.",
    )
    parser.add_argument(
        "--seed-demo-data",
        action="store_true",
        help="Insereix un prompt i dues respostes efímeres per provar tasca i vot.",
    )
    args = parser.parse_args()

    try:
        run_flow(args.base_url, args.category, args.keep_account, args.seed_demo_data)
    except FlowError as err:
        print(f"\n{BOLD}{RED}✗ Flux interromput: {err}{RESET}")
        return 1
    finally:
        if args.seed_demo_data:
            cleanup_demo_data()
            print(f"{DIM}Dades de demo esborrades.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

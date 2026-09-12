"""Càrrega de tasques fictícies (mock) a la base de dades.

Insereix prompts i respostes sintètiques perquè el flux de `GET /api/task` i
`POST /api/vote` es pugui provar sense dependre de la càrrega d'inferències
reals. Cada prompt rep almenys dues respostes de models diferents, de manera
que el sampler pot formar parelles i servir tasques.

Les dades es guarden sota una `version` pròpia (per defecte `mock`) per
mantenir-les aïllades de les dades reals i poder-les esborrar amb `--clear`.
L'script és idempotent: si un prompt fictici ja existeix, no es duplica.

Setup de PostgreSQL
-------------------
Des de l'arrel del repositori:

    cp .env.example .env
    docker compose up -d --wait

Des de `backend/`:

    uv sync
    uv run alembic upgrade head
    uv run python scripts/seed_mock_tasks.py

Opcions:

    --prompts-per-category N   Prompts ficticis per categoria (per defecte 5).
    --models MODEL [MODEL ...] Noms de model a generar (per defecte 3 models mock).
    --version VERSIO           Etiqueta de versió de les dades (per defecte "mock").
    --categories CODI [...]    Limita a aquestes categories (per defecte, totes).
    --clear                    Esborra les dades fictícies d'aquesta versió i surt.
"""

import argparse
import sys
from pathlib import Path

# Permet importar el paquet `app` quan s'executa l'script des de qualsevol directori.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import get_sessionmaker  # noqa: E402
from app.models import Category, Prompt, Response, Vote  # noqa: E402

# Models ficticis per defecte. Cal com a mínim dos perquè el sampler formi parelles.
DEFAULT_MODELS = ["mock-modela", "mock-modelb", "mock-modelc"]
DEFAULT_VERSION = "mock"
DEFAULT_PROMPTS_PER_CATEGORY = 5


def _prompt_text(category: Category, index: int) -> str:
    """Genera el text del prompt fictici per a una categoria i índex."""
    base = category.description or f"Tasca de {category.name}."
    return f"[{category.name} · exemple {index}] {base}"


def _response_text(category: Category, index: int, model: str) -> str:
    """Genera el text d'una resposta fictícia d'un model concret."""
    return (
        f"Resposta fictícia del model «{model}» a l'exemple {index} "
        f"de la categoria «{category.name}»."
    )


def seed_mock_tasks(
    db: Session,
    *,
    version: str,
    prompts_per_category: int,
    models: list[str],
    category_codes: list[str] | None,
) -> tuple[int, int]:
    """Insereix prompts i respostes ficticis. Retorna (prompts_nous, respostes_noves)."""
    if len(models) < 2:
        raise SystemExit("Calen com a mínim dos models per poder formar parelles de vot.")

    stmt = select(Category)
    if category_codes:
        stmt = stmt.where(Category.code.in_(category_codes))
    categories = db.scalars(stmt).all()

    if not categories:
        raise SystemExit("No s'ha trobat cap categoria. Has carregat el catàleg YAML?")

    new_prompts = 0
    new_responses = 0

    for category in categories:
        for index in range(1, prompts_per_category + 1):
            code = f"mock_{category.code}_{index}"

            # Idempotència: reutilitza el prompt si ja existeix per a aquesta versió.
            prompt = db.scalar(select(Prompt).where(Prompt.version == version, Prompt.code == code))
            if prompt is None:
                prompt = Prompt(
                    version=version,
                    code=code,
                    category_id=category.id,
                    text=_prompt_text(category, index),
                )
                db.add(prompt)
                db.flush()
                new_prompts += 1

            # Afegeix només les respostes de models que encara no tingui el prompt.
            existing_models = {r.model for r in prompt.responses}
            for model in models:
                if model in existing_models:
                    continue
                db.add(
                    Response(
                        prompt_id=prompt.id,
                        model=model,
                        text=_response_text(category, index, model),
                        inference_metadata={"mock": True},
                    )
                )
                new_responses += 1

    db.commit()
    return new_prompts, new_responses


def clear_mock_tasks(db: Session, *, version: str) -> int:
    """Esborra tots els prompts (i respostes/vots) d'una versió. Retorna prompts esborrats."""
    prompts = db.scalars(select(Prompt).where(Prompt.version == version)).all()
    if not prompts:
        return 0

    prompt_ids = [p.id for p in prompts]
    # Els vots referencien les respostes amb una FK composta que l'ORM no coneix
    # com a relació; esborra'ls abans dels prompts i les respostes.
    for vote in db.scalars(select(Vote).where(Vote.prompt_id.in_(prompt_ids))).all():
        db.delete(vote)
    db.flush()

    for prompt in prompts:
        db.delete(prompt)  # les respostes cauen per cascada (delete-orphan).
    db.commit()
    return len(prompts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Carrega tasques fictícies a la base de dades.")
    parser.add_argument(
        "--prompts-per-category",
        type=int,
        default=DEFAULT_PROMPTS_PER_CATEGORY,
        help="Nombre de prompts ficticis per categoria (per defecte 5).",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="Noms dels models ficticis a generar (mínim 2).",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help='Etiqueta de versió de les dades fictícies (per defecte "mock").',
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=None,
        help="Codis de categoria a omplir (per defecte, totes).",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Esborra les dades fictícies d'aquesta versió i surt.",
    )
    args = parser.parse_args()

    session_factory = get_sessionmaker()
    with session_factory() as db:
        if args.clear:
            removed = clear_mock_tasks(db, version=args.version)
            print(f"Esborrats {removed} prompts ficticis (versió «{args.version}»).")
            return

        new_prompts, new_responses = seed_mock_tasks(
            db,
            version=args.version,
            prompts_per_category=args.prompts_per_category,
            models=args.models,
            category_codes=args.categories,
        )

    print(
        f"Carregades dades fictícies (versió «{args.version}»): "
        f"{new_prompts} prompts nous, {new_responses} respostes noves."
    )
    if new_prompts == 0 and new_responses == 0:
        print("No hi havia res nou a inserir (les dades ja existien).")


if __name__ == "__main__":
    main()

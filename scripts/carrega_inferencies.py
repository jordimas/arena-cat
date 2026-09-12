"""Càrrega idempotent de prompts i inferències a la base de dades.

Llegeix els prompts versionats a ``data/prompts/<version>/*.txt`` (text pla, on
el nom del fitxer és el codi) i les inferències a
``data/inferencies/<version>/<model_id>/*.yaml``, i en fa *upsert* a les taules
``prompts`` i ``responses``. La clau natural d'un prompt és ``(version, code)``
i la d'una resposta ``(prompt_id, model)``, de manera que tornar a executar
l'script no duplica files ni en trenca les restriccions.

La connexió es resol amb la mateixa configuració que el servei (``app.config``),
és a dir el rol d'aplicació amb permisos limitats.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

REPO_ROOT = Path(__file__).resolve().parents[1]
# El model de dades i la configuració viuen al paquet backend/app.
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import get_sessionmaker  # noqa: E402
from app.models import Category, Prompt, Response  # noqa: E402

LOGGER = logging.getLogger("carrega_inferencies")

DEFAULT_VERSION = "v1"
DEFAULT_CATEGORIES_FILE = REPO_ROOT / "data" / "prompts" / "categories.yaml"
DEFAULT_PROMPTS_DIR = REPO_ROOT / "data" / "prompts" / DEFAULT_VERSION
DEFAULT_INFERENCIES_DIR = REPO_ROOT / "data" / "inferencies" / DEFAULT_VERSION

# El codi d'un prompt acaba amb un sufix numèric (p. ex. ``traduccio_10``); la
# resta identifica la categoria (``traduccio``).
_CODE_SUFFIX = re.compile(r"_\d+$")

# Separa els trams numèrics d'un nom per poder ordenar-lo de manera natural.
_DIGITS = re.compile(r"(\d+)")


def _natural_key(path: Path) -> list[object]:
    """Clau d'ordenació natural d'un camí.

    Ordena els trams numèrics pel seu valor i no lexicogràficament, de manera que
    ``traduccio_2`` va abans de ``traduccio_10``. Opera sobre el camí sencer, així
    que manté l'agrupació per directori (p. ex. per model) i només canvia l'ordre
    dels números dins de cada nom.

    Args:
        path: Camí del fitxer a ordenar.

    Returns:
        Llista alternada de text i enters, comparable amb la d'altres camins.
    """
    return [int(part) if part.isdigit() else part for part in _DIGITS.split(str(path))]


class SchemaError(Exception):
    """Un fitxer YAML no compleix l'esquema esperat.

    Es fa servir per a camps obligatoris absents, tipus inesperats o referències
    a entitats desconegudes (categoria o prompt).
    """


class CategoryCatalogError(ValueError):
    """El catàleg no existeix o no compleix l'esquema esperat."""


class CategoryDefinition(BaseModel):
    """Metadades d'una categoria declarada al catàleg."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str | None = None
    evaluation_instructions: str

    @field_validator("name", "evaluation_instructions")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Normalitza els textos obligatoris i rebutja cadenes buides."""
        value = value.strip()
        if not value:
            raise ValueError("el nom no pot ser buit")
        return value


class CategoryCatalog(BaseModel):
    """Document arrel del fitxer YAML."""

    model_config = ConfigDict(extra="forbid")

    categories: list[CategoryDefinition]


def load_category_catalog(path: Path | str) -> list[CategoryDefinition]:
    """Llegeix i valida un catàleg YAML de categories."""
    path = Path(path)
    try:
        document = CategoryCatalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise CategoryCatalogError(f"catàleg de categories no vàlid ({path}): {error}") from error

    codes = [category.code for category in document.categories]
    if len(codes) != len(set(codes)):
        raise CategoryCatalogError("el catàleg conté codis de categoria duplicats")
    return document.categories


@dataclass(slots=True)
class PromptRecord:
    """Prompt normalitzat, a punt per fer *upsert* a la taula ``prompts``."""

    version: str
    code: str
    category_code: str
    text: str
    source: Path


@dataclass(slots=True)
class ResponseRecord:
    """Resposta d'un model normalitzada, per fer *upsert* a ``responses``."""

    version: str
    prompt_code: str
    model: str
    text: str
    metadata: dict[str, Any]
    source: Path


@dataclass(slots=True)
class Stats:
    """Recompte d'un tipus d'entitat carregada.

    No hi ha categoria ``updated``: la càrrega és idempotent només si el contingut
    és idèntic (``skipped``). Si una entitat ja existeix amb un contingut diferent,
    es compta com a ``errors`` i s'exigeix una versió nova en comptes de modificar
    la fila (vegeu :func:`upsert_response` i :func:`upsert_prompt`).
    """

    inserted: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(slots=True)
class Summary:
    """Resum global de la càrrega."""

    prompts: Stats
    responses: Stats

    @property
    def total_errors(self) -> int:
        return self.prompts.errors + self.responses.errors


def _require(value: Any, message: str, source: Path) -> Any:
    """Comprova que un camp obligatori hi és i no és buit.

    Args:
        value: Valor llegit del YAML.
        message: Descripció de l'incompliment per al missatge d'error.
        source: Fitxer d'origen, per situar l'error.

    Returns:
        El valor original si és vàlid.

    Raises:
        SchemaError: Si el valor és ``None`` o una cadena buida.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SchemaError(f"{source}: {message}")
    return value


def _nested(data: Any, *keys: str) -> Any:
    """Accedeix a una clau imbricada sense petar si falta un nivell.

    Args:
        data: Estructura carregada del YAML.
        *keys: Seqüència de claus a recórrer.

    Returns:
        El valor trobat, o ``None`` si qualsevol nivell no és un mapa o no hi és.
    """
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def parse_prompt_file(path: Path, version: str) -> PromptRecord:
    """Llegeix un fitxer de prompt en text pla.

    El nom del fitxer (sense extensió) és el codi del prompt; el contingut
    sencer és el text. La categoria es dedueix eliminant el sufix numèric del
    codi (p. ex. ``traduccio_10`` → ``traduccio``).

    Args:
        path: Fitxer de prompt (``.txt``).
        version: Versió del conjunt de dades.

    Returns:
        Prompt normalitzat.

    Raises:
        SchemaError: Si el fitxer és buit.
    """
    text = _require(path.read_text(encoding="utf-8"), "el prompt no té text", path)
    code = path.stem
    category_code = _CODE_SUFFIX.sub("", code)
    return PromptRecord(
        version=version,
        code=code,
        category_code=category_code,
        text=text.strip(),
        source=path,
    )


def parse_inference_file(path: Path, version: str) -> ResponseRecord:
    """Llegeix un fitxer d'inferència i el normalitza.

    Args:
        path: Fitxer YAML d'inferència (sortida de la canonada).
        version: Versió del conjunt de dades.

    Returns:
        Resposta normalitzada. El raonament intern es desa a les metadades, no al
        text visible, perquè l'avaluació és a cegues.

    Raises:
        SchemaError: Si no és un mapa o falta ``prompt.id``, ``model.id`` o
            ``output.answer``.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: la inferència ha de ser un mapa")

    prompt_code = _require(_nested(data, "prompt", "id"), "falta prompt.id", path)
    model = _require(_nested(data, "model", "id"), "falta model.id", path)
    answer = _require(_nested(data, "output", "answer"), "falta output.answer", path)

    metadata = {
        "model_name": _nested(data, "model", "model_name"),
        "revision": _nested(data, "model", "revision"),
        "generation": data.get("generation"),
        "run": data.get("run"),
        "backend": data.get("backend"),
        "prompt_sha256": _nested(data, "prompt", "sha256"),
        "reasoning": _nested(data, "reasoning", "content"),
    }
    metadata = {key: value for key, value in metadata.items() if value is not None}

    return ResponseRecord(
        version=version,
        prompt_code=str(prompt_code),
        model=str(model),
        text=str(answer).strip(),
        metadata=metadata,
        source=path,
    )


def upsert_prompt(
    session: Session,
    record: PromptRecord,
    category_ids: dict[str, int],
    stats: Stats,
) -> None:
    """Fa *upsert* d'un prompt per la clau ``(version, code)``.

    Insereix si no existeix i omet si ja existeix amb el mateix contingut. Si
    existeix amb un contingut diferent (text o categoria), ho compta com a error i
    **no** modifica la fila: igual que les respostes, els prompts ja carregats
    poden tenir vots associats, de manera que un canvi exigeix una versió nova en
    comptes de mutar la fila existent.

    Args:
        session: Sessió de base de dades activa.
        record: Prompt normalitzat.
        category_ids: Mapa de codi de categoria a identificador.
        stats: Recompte que s'actualitza segons el resultat.
    """
    category_id = category_ids.get(record.category_code)
    if category_id is None:
        LOGGER.error("%s: categoria desconeguda '%s'", record.source, record.category_code)
        stats.errors += 1
        return

    existing = session.scalar(
        select(Prompt).where(Prompt.version == record.version, Prompt.code == record.code)
    )
    if existing is None:
        session.add(
            Prompt(
                version=record.version,
                code=record.code,
                category_id=category_id,
                text=record.text,
            )
        )
        session.flush()
        stats.inserted += 1
    elif existing.text == record.text and existing.category_id == category_id:
        stats.skipped += 1
    else:
        LOGGER.error(
            "%s: el prompt (%s/%s) ja existeix amb un contingut diferent; "
            "publica'l amb una versió nova en comptes de modificar-lo",
            record.source,
            record.version,
            record.code,
        )
        stats.errors += 1


def upsert_response(session: Session, record: ResponseRecord, stats: Stats) -> None:
    """Fa *upsert* d'una resposta per la clau ``(prompt_id, model)``.

    Insereix si no existeix i omet si ja existeix amb el mateix contingut. Si
    existeix amb un contingut diferent (text o metadades), ho compta com a error i
    **no** modifica la fila: els vots existents apunten a aquest ``response_id`` i
    sobreescriure'n el text els invalidaria semànticament. Un canvi de contingut
    exigeix, doncs, una versió nova en comptes de mutar la resposta.

    Args:
        session: Sessió de base de dades activa.
        record: Resposta normalitzada.
        stats: Recompte que s'actualitza segons el resultat.
    """
    prompt = session.scalar(
        select(Prompt).where(Prompt.version == record.version, Prompt.code == record.prompt_code)
    )
    if prompt is None:
        LOGGER.error(
            "%s: prompt desconegut (%s/%s)", record.source, record.version, record.prompt_code
        )
        stats.errors += 1
        return

    existing = session.scalar(
        select(Response).where(Response.prompt_id == prompt.id, Response.model == record.model)
    )
    if existing is None:
        session.add(
            Response(
                prompt_id=prompt.id,
                model=record.model,
                text=record.text,
                inference_metadata=record.metadata,
            )
        )
        session.flush()
        stats.inserted += 1
    elif existing.text == record.text and existing.inference_metadata == record.metadata:
        stats.skipped += 1
    else:
        LOGGER.error(
            "%s: la resposta (%s/%s) ja existeix amb un contingut diferent; "
            "publica-la amb una versió nova en comptes de modificar-la, perquè els "
            "vots existents apunten al text actual",
            record.source,
            record.prompt_code,
            record.model,
        )
        stats.errors += 1


def load_prompts(
    session: Session,
    prompts_dir: Path,
    version: str,
    category_ids: dict[str, int],
    stats: Stats,
) -> None:
    """Carrega tots els prompts d'un directori.

    Args:
        session: Sessió de base de dades activa.
        prompts_dir: Directori amb els fitxers de prompt.
        version: Versió del conjunt de dades.
        category_ids: Mapa de codi de categoria a identificador.
        stats: Recompte que s'actualitza.
    """
    for path in sorted(prompts_dir.glob("*.txt"), key=_natural_key):
        try:
            record = parse_prompt_file(path, version)
        except SchemaError as error:
            LOGGER.error("prompt no vàlid: %s", error)
            stats.errors += 1
            continue
        upsert_prompt(session, record, category_ids, stats)


def load_categories(
    session: Session,
    definitions: list[CategoryDefinition],
) -> dict[str, int]:
    """Sincronitza el catàleg i retorna els identificadors per codi."""
    categories = {category.code: category for category in session.scalars(select(Category)).all()}

    for definition in definitions:
        category = categories.get(definition.code)
        if category is None:
            category = Category(code=definition.code)
            categories[definition.code] = category
            session.add(category)

        category.name = definition.name
        category.description = definition.description
        category.evaluation_instructions = definition.evaluation_instructions

    session.flush()
    return {definition.code: categories[definition.code].id for definition in definitions}


def load_responses(session: Session, inferencies_dir: Path, version: str, stats: Stats) -> None:
    """Carrega totes les inferències d'un directori (recursivament).

    Args:
        session: Sessió de base de dades activa.
        inferencies_dir: Directori arrel de les inferències.
        version: Versió del conjunt de dades.
        stats: Recompte que s'actualitza.
    """
    for path in sorted(inferencies_dir.rglob("*.yaml"), key=_natural_key):
        try:
            record = parse_inference_file(path, version)
        except (SchemaError, yaml.YAMLError) as error:
            LOGGER.error("inferència no vàlida: %s", error)
            stats.errors += 1
            continue
        upsert_response(session, record, stats)


def run_load(
    session: Session,
    prompts_dir: Path | str,
    inferencies_dir: Path | str,
    version: str | None = None,
    categories_file: Path | str = DEFAULT_CATEGORIES_FILE,
) -> Summary:
    """Carrega prompts i inferències dins de la sessió donada.

    No fa ``commit``: és responsabilitat de qui crida la funció (la CLI confirma
    la transacció; els tests la desfan).

    Args:
        session: Sessió de base de dades activa.
        prompts_dir: Directori dels prompts.
        inferencies_dir: Directori arrel de les inferències.
        version: Versió del conjunt de dades. Si és ``None``, es dedueix del nom
            del directori de prompts (p. ex. ``data/prompts/v1`` -> ``v1``).

    Returns:
        Resum amb els recomptes de prompts i respostes.
    """
    prompts_dir = Path(prompts_dir)
    inferencies_dir = Path(inferencies_dir)
    version = version or prompts_dir.name

    categories = load_category_catalog(categories_file)
    category_ids = load_categories(session, categories)
    summary = Summary(prompts=Stats(), responses=Stats())
    load_prompts(session, prompts_dir, version, category_ids, summary.prompts)
    load_responses(session, inferencies_dir, version, summary.responses)
    return summary


def _format_stats(stats: Stats) -> str:
    return f"inserits={stats.inserted} omesos={stats.skipped} errors={stats.errors}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Llegeix els paràmetres CLI.

    Args:
        argv: Arguments alternatius (per a tests); ``None`` usa ``sys.argv``.

    Returns:
        Namespace amb els paràmetres.
    """
    parser = argparse.ArgumentParser(
        description="Càrrega idempotent de prompts i inferències a la base de dades.",
    )
    parser.add_argument(
        "--categories-file",
        type=Path,
        default=DEFAULT_CATEGORIES_FILE,
        help="Fitxer YAML amb el catàleg de categories.",
    )
    parser.add_argument(
        "--prompts-dir",
        type=Path,
        default=DEFAULT_PROMPTS_DIR,
        help="Directori amb els fitxers de text dels prompts.",
    )
    parser.add_argument(
        "--inferencies-dir",
        type=Path,
        default=DEFAULT_INFERENCIES_DIR,
        help="Directori arrel amb les inferències (un subdirectori per model).",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Versió del conjunt de dades. Per defecte, el nom del directori de prompts.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Nivell mínim dels missatges de logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Punt d'entrada CLI.

    Args:
        argv: Arguments alternatius (per a tests); ``None`` usa ``sys.argv``.

    Returns:
        ``0`` si tot ha anat bé, ``1`` si algun fitxer no complia l'esquema.
    """
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s:%(name)s:%(message)s")

    with get_sessionmaker()() as session:
        try:
            summary = run_load(
                session,
                args.prompts_dir,
                args.inferencies_dir,
                args.version,
                categories_file=args.categories_file,
            )
        except CategoryCatalogError as error:
            LOGGER.error("%s", error)
            return 1
        session.commit()

    print(f"Prompts:   {_format_stats(summary.prompts)}")
    print(f"Respostes: {_format_stats(summary.responses)}")

    if summary.total_errors:
        LOGGER.error("S'han trobat %s fitxers no vàlids.", summary.total_errors)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

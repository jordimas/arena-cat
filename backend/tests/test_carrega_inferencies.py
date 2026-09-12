"""Tests de la càrrega idempotent de prompts i inferències (contra PostgreSQL real)."""

import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

from app.models import Category, Prompt, Response

# L'script viu a scripts/ (projecte arrel), fora del paquet backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import carrega_inferencies as loader  # noqa: E402


def write_prompt(prompts_dir: Path, code: str, text: str = "Tradueix aquest text.") -> None:
    """Escriu un prompt de text pla (l'única forma acceptada per la canonada)."""
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / f"{code}.txt").write_text(text, encoding="utf-8")


def write_inference(
    inferencies_dir: Path,
    model_id: str,
    prompt_code: str,
    answer: str = "Una resposta.",
    reasoning: str | None = None,
) -> None:
    """Escriu una inferència imitant la sortida de scripts/inferencia.py."""
    model_dir = inferencies_dir / model_id
    model_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "run": {"timestamp": "2026-01-01T00:00:00Z", "git_commit": "abc123", "seed": 42},
        "prompt": {
            "id": prompt_code,
            "path": f"data/prompts/v1/{prompt_code}.txt",
            "sha256": "deadbeef",
        },
        "model": {"id": model_id, "model_name": f"org/{model_id}", "revision": "main"},
        "generation": {"temperature": 0.7, "top_p": 0.9, "max_new_tokens": 1024, "seed": 42},
        "backend": {"engine": "transformers", "transformers_version": "5", "torch_version": "2"},
        "output": {"answer": answer},
        "reasoning": {"content": reasoning},
    }
    (model_dir / f"{prompt_code}.yaml").write_text(
        yaml.dump(document, allow_unicode=True), encoding="utf-8"
    )


@pytest.fixture
def dirs(tmp_path):
    """Directoris buits de prompts i inferències per a la versió v1."""
    prompts_dir = tmp_path / "prompts" / "v1"
    inferencies_dir = tmp_path / "inferencies" / "v1"
    prompts_dir.mkdir(parents=True)
    inferencies_dir.mkdir(parents=True)
    return prompts_dir, inferencies_dir


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def write_categories(
    path: Path,
    name: str = "Cultura",
    evaluation_instructions: str = "Comprova quatre aspectes importants.",
) -> None:
    """Escriu un catàleg mínim per provar-ne la sincronització."""
    path.write_text(
        f"""categories:
  - code: cultura
    name: {name}
    description: Avalua coneixements culturals.
    evaluation_instructions: {evaluation_instructions}
""",
        encoding="utf-8",
    )


def test_categories_are_inserted_updated_and_loaded_idempotently(session, dirs, tmp_path):
    prompts_dir, inferencies_dir = dirs
    categories_file = tmp_path / "categories.yaml"
    write_categories(categories_file)

    loader.run_load(session, prompts_dir, inferencies_dir, categories_file=categories_file)
    loader.run_load(session, prompts_dir, inferencies_dir, categories_file=categories_file)
    write_categories(
        categories_file,
        name="Cultura catalana",
        evaluation_instructions="Comprova quatre criteris culturals.",
    )
    loader.run_load(session, prompts_dir, inferencies_dir, categories_file=categories_file)

    category = session.scalar(select(Category).where(Category.code == "cultura"))
    assert category.name == "Cultura catalana"
    assert category.evaluation_instructions == "Comprova quatre criteris culturals."
    assert (
        session.scalar(select(func.count()).select_from(Category).where(Category.code == "cultura"))
        == 1
    )


@pytest.mark.parametrize(
    "document",
    [
        "categories: [{code: correccio}]",
        "categories: [{code: 'Còrrecció', name: Correcció}]",
        "categories: [{code: correccio, name: Correcció, extra: true}]",
        "categories: [{code: correccio, name: Correcció}, {code: correccio, name: Altra}]",
    ],
)
def test_invalid_category_catalog_is_rejected(tmp_path, document):
    categories_file = tmp_path / "categories.yaml"
    categories_file.write_text(document, encoding="utf-8")

    with pytest.raises(loader.CategoryCatalogError):
        loader.load_category_catalog(categories_file)


def test_prompt_is_inserted_with_derived_category(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "traduccio_1", "Tradueix això.")

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.prompts.inserted == 1
    assert summary.prompts.errors == 0
    prompt = session.scalar(select(Prompt).where(Prompt.code == "traduccio_1"))
    assert prompt.version == "v1"
    assert prompt.text == "Tradueix això."
    assert prompt.category.code == "traduccio"


def test_prompts_load_in_natural_order(session, dirs):
    # traduccio_10 s'ha d'inserir després de traduccio_2, no entre l'1 i el 2.
    prompts_dir, inferencies_dir = dirs
    for code in ("traduccio_1", "traduccio_2", "traduccio_10"):
        write_prompt(prompts_dir, code)

    loader.run_load(session, prompts_dir, inferencies_dir)

    codes_by_id = session.scalars(select(Prompt.code).order_by(Prompt.id)).all()
    assert codes_by_id == ["traduccio_1", "traduccio_2", "traduccio_10"]


def test_versioned_v1_prompts_follow_loader_naming_convention(session, tmp_path):
    """Els prompts publicats han de començar pel codi de la categoria."""
    repo_root = Path(__file__).resolve().parents[2]
    prompts_dir = repo_root / "data" / "prompts" / "v1"
    inferencies_dir = tmp_path / "inferencies" / "v1"
    inferencies_dir.mkdir(parents=True)

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.prompts.errors == 0
    assert summary.prompts.inserted == 30


def test_load_is_idempotent(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "correccio_1")
    write_inference(inferencies_dir, "qwen-3.5-9b", "correccio_1")

    first = loader.run_load(session, prompts_dir, inferencies_dir)
    assert (first.prompts.inserted, first.responses.inserted) == (1, 1)

    second = loader.run_load(session, prompts_dir, inferencies_dir)
    assert second.prompts.skipped == 1
    assert second.responses.skipped == 1
    assert second.prompts.inserted == 0
    assert second.responses.inserted == 0

    # Cap duplicat.
    assert _count(session, Prompt) == 1
    assert _count(session, Response) == 1


def test_responses_link_to_prompt_by_version_and_code(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "reformulacio_1")
    write_inference(inferencies_dir, "qwen-3.5-9b", "reformulacio_1", answer="Resposta A")
    write_inference(
        inferencies_dir, "salamandra-7b-instruct", "reformulacio_1", answer="Resposta B"
    )

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.responses.inserted == 2
    assert summary.responses.errors == 0
    prompt = session.scalar(select(Prompt).where(Prompt.code == "reformulacio_1"))
    responses = session.scalars(
        select(Response).where(Response.prompt_id == prompt.id).order_by(Response.model)
    ).all()
    assert [r.model for r in responses] == ["qwen-3.5-9b", "salamandra-7b-instruct"]
    assert {r.text for r in responses} == {"Resposta A", "Resposta B"}
    assert responses[0].inference_metadata["model_name"] == "org/qwen-3.5-9b"
    assert responses[0].inference_metadata["generation"]["temperature"] == 0.7


def test_reasoning_goes_to_metadata_not_visible_text(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "traduccio_1")
    write_inference(
        inferencies_dir,
        "qwen-3.5-9b",
        "traduccio_1",
        answer="Resposta final",
        reasoning="Raonament intern del model",
    )

    loader.run_load(session, prompts_dir, inferencies_dir)

    response = session.scalar(select(Response))
    assert response.text == "Resposta final"
    assert "Raonament" not in response.text
    assert response.inference_metadata["reasoning"] == "Raonament intern del model"


def test_changed_answer_is_conflict_error_and_keeps_original(session, dirs):
    # Tornar a carregar una resposta amb un text diferent no l'ha de sobreescriure:
    # els vots existents hi apunten. És un error que exigeix una versió nova.
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "traduccio_1")
    write_inference(inferencies_dir, "qwen-3.5-9b", "traduccio_1", answer="Primera")
    loader.run_load(session, prompts_dir, inferencies_dir)

    write_inference(inferencies_dir, "qwen-3.5-9b", "traduccio_1", answer="Segona")
    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.responses.errors == 1
    assert summary.responses.inserted == 0
    assert summary.responses.skipped == 0
    response = session.scalar(select(Response))
    assert response.text == "Primera"  # intacta
    assert _count(session, Response) == 1


def test_changed_prompt_text_is_conflict_error_and_keeps_original(session, dirs):
    # El mateix s'aplica als prompts: un canvi de text amb la mateixa clau falla.
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "traduccio_1", "Tradueix això.")
    loader.run_load(session, prompts_dir, inferencies_dir)

    write_prompt(prompts_dir, "traduccio_1", "Tradueix una altra cosa.")
    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.prompts.errors == 1
    assert summary.prompts.inserted == 0
    assert summary.prompts.skipped == 0
    prompt = session.scalar(select(Prompt).where(Prompt.code == "traduccio_1"))
    assert prompt.text == "Tradueix això."  # intacte
    assert _count(session, Prompt) == 1


def test_unknown_prompt_in_inference_is_error(session, dirs):
    prompts_dir, inferencies_dir = dirs
    # Cap prompt traduccio_99 a la base de dades.
    write_inference(inferencies_dir, "qwen-3.5-9b", "traduccio_99")

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.responses.errors == 1
    assert summary.responses.inserted == 0
    assert _count(session, Response) == 0


def test_unknown_category_is_error(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "inexistent_1")

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.prompts.errors == 1
    assert summary.prompts.inserted == 0
    assert _count(session, Prompt) == 0


def test_missing_required_field_is_error(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "traduccio_1")
    # Inferència sense output.answer.
    model_dir = inferencies_dir / "qwen-3.5-9b"
    model_dir.mkdir(parents=True)
    (model_dir / "traduccio_1.yaml").write_text(
        yaml.dump({"prompt": {"id": "traduccio_1"}, "model": {"id": "qwen-3.5-9b"}}),
        encoding="utf-8",
    )

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.responses.errors == 1
    assert summary.responses.inserted == 0


def test_empty_prompt_is_counted_and_others_load(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "traduccio_1")
    (prompts_dir / "traduccio_2.txt").write_text("", encoding="utf-8")

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.prompts.inserted == 1
    assert summary.prompts.errors == 1
    assert _count(session, Prompt) == 1


def test_summary_total_errors_drives_exit_code(session, dirs):
    prompts_dir, inferencies_dir = dirs
    write_prompt(prompts_dir, "traduccio_1")
    write_inference(inferencies_dir, "qwen-3.5-9b", "traduccio_99")

    summary = loader.run_load(session, prompts_dir, inferencies_dir)

    assert summary.total_errors == 1

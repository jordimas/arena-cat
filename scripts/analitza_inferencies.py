# Genera results.txt amb les distàncies entre sortides de cada model i el
# rang dels prompts segons com de discriminants són (com més divergents les
# sortides, més discriminant és el prompt).

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import yaml  # noqa: E402
from jinja2 import Environment, FileSystemLoader  # noqa: E402

from metriques import load_answers, pairwise_metrics  # noqa: E402

RECOMMENDED_THRESHOLD = 0.40
TRANSLATION_CATEGORY = "traduccio"

MODEL_DISPLAY = {
    "qwen3.8-27b": "Qwen/Qwen3.8-27B",
    "mistral-small-3.2-24b-instruct-2506": (
        "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    ),
    "gemma-3-27b-it": "google/gemma-3-27b-it",
}
MODEL_IDS = list(MODEL_DISPLAY)


def _category(prompt_id: str) -> str:
    return prompt_id.split("_", 1)[0]


def _validation_score(entry: dict) -> float:
    """Retorna la puntuació usada per validar el prompt segons la categoria."""
    if _category(entry["prompt_id"]) == TRANSLATION_CATEGORY:
        return entry["metrics"]["combinat_mean"]
    return entry["metrics"]["combinat_worst"]


def _discover_prompt_ids(inferences_dir: Path) -> list[str]:
    ids = {p.stem for m in MODEL_IDS for p in (inferences_dir / m).glob("*.yaml")}
    return sorted(ids)


def _load_original_prompt(inferences_dir: Path, prompt_id: str) -> str:
    """Llegeix el prompt original referenciat per qualsevol de les inferències."""
    for model_id in MODEL_IDS:
        inf_path = inferences_dir / model_id / f"{prompt_id}.yaml"
        if not inf_path.is_file():
            continue
        rel = (yaml.safe_load(inf_path.read_text("utf-8")) or {}).get("prompt", {}).get(
            "path"
        )
        prompt_path = REPO_ROOT / rel if rel else None
        if not prompt_path or not prompt_path.is_file():
            continue
        raw = prompt_path.read_text("utf-8")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            data = None
        return (data["text"] if isinstance(data, dict) and "text" in data else raw).strip()
    return "(prompt original no trobat)"


def _category_summary(entries: list[dict]) -> list[dict]:
    rows = {}
    for e in entries:
        category = _category(e["prompt_id"])
        row = rows.setdefault(
            category,
            {
                "category": category,
                "total": 0,
                "valid": 0,
                "invalid": 0,
            },
        )
        row["total"] += 1
        if _validation_score(e) >= RECOMMENDED_THRESHOLD:
            row["valid"] += 1
        else:
            row["invalid"] += 1

    summaries = []
    for row in rows.values():
        row["valid_pct"] = row["valid"] * 100 / row["total"]
        row["invalid_pct"] = row["invalid"] * 100 / row["total"]
        summaries.append(row)
    return sorted(summaries, key=lambda s: str(s["category"]))


def _print_category_summary(category_summary: list[dict]) -> None:
    print("\nResum de validesa per categoria")
    print(f"Invàlid = puntuació de validació < {RECOMMENDED_THRESHOLD:.2f}")
    print("Validació: traduccio usa combinat_mean; la resta usa combinat_worst")
    print(
        f"{'categoria':<16}{'total':>7}{'vàlids':>9}{'invàlids':>10}"
        f"{'% vàlids':>10}{'% invàlids':>12}"
    )
    for row in category_summary:
        print(
            f"{row['category']:<16}{row['total']:>7}{row['valid']:>9}"
            f"{row['invalid']:>10}{row['valid_pct']:>9.1f}%"
            f"{row['invalid_pct']:>11.1f}%"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calcula mètriques de distància entre sortides de models "
        "per als prompts d'un directori d'inferències."
    )
    parser.add_argument(
        "--inferencies",
        default="data/inferencies/v1",
        help="Subdirectori d'inferències (relatiu al repo).",
    )
    args = parser.parse_args()

    inferences_dir = REPO_ROOT / args.inferencies
    entries = []
    for prompt_id in _discover_prompt_ids(inferences_dir):
        outputs = load_answers(prompt_id, MODEL_IDS, inference_subdir=args.inferencies)
        if len(outputs) < 2:
            print(f"avís: {prompt_id} té només {len(outputs)} sortida(es), s'omet")
            continue
        entries.append({
            "prompt_id": prompt_id,
            "prompt_text": _load_original_prompt(inferences_dir, prompt_id),
            "outputs": outputs,
            "metrics": pairwise_metrics(outputs),
            "missing": [m for m in MODEL_IDS if m not in outputs],
        })

    entries.sort(key=lambda e: e["metrics"]["combinat_mean"], reverse=True)
    category_summary = _category_summary(entries)
    if category_summary:
        _print_category_summary(category_summary)

    env = Environment(loader=FileSystemLoader(Path(__file__).parent))
    rendered = env.get_template("results.txt.j2").render(
        inferencies=args.inferencies,
        models=MODEL_IDS,
        models_display=[MODEL_DISPLAY[m] for m in MODEL_IDS],
        models_display_map=MODEL_DISPLAY,
        recommended_threshold=RECOMMENDED_THRESHOLD,
        category_summary=category_summary,
        entries=entries,
    )

    target = REPO_ROOT / "results.txt"
    target.write_text(rendered, encoding="utf-8")
    print(f"Escrit: {target}")


if __name__ == "__main__":
    main()

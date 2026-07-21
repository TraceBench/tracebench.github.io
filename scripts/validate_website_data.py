#!/usr/bin/env python3
"""Validate browser-ready TraceBench website data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public" / "data"
REQUIRED_SEEDS = [0, 1, 2, 3, 4]
REQUIRED_SIMULATORS = ["BallDrop", "BounceBall", "MassSlide"]
HOMEPAGE_DATA_FILENAME = "data_main_page.json"
HOMEPAGE_REQUIRED_COLUMNS = {"Position", "Velocity", "Hard_Stop_f", "time"}
EXPECTED_PROMPT_COMBINATIONS = {
    (desc_level, task_type, training_samples)
    for desc_level in ("high", "none")
    for task_type in ("direct", "code")
    for training_samples in ("none", "one", "multiple")
}
MALFORMED_PROMPT_PREFIX = "You are given multivariate time-series observations"
FORBIDDEN_WEBSITE_PROMPT_SNIPPETS = (
    "Observed Signals:",
    "Requirements:",
    "Requirements for predict(df):",
    "Evaluation:",
    "Additional requirements:",
    "Internet access is disabled",
)
FORBIDDEN_HOMEPAGE_RANKED_PROMPT_SNIPPETS = (
    "ranked list",
    "lower-confidence",
    "<top_1_label>",
    "<optional_lower_confidence_label>",
    "For each dataframe, predict(df)",
    "def predict(df) -> list[str]:",
    "columns col1, col2, col3, and col4",
    "The function must return the correct label as a string.",
)


class ValidationError(Exception):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_leaderboard(data_dir: Path) -> None:
    leaderboard = read_json(data_dir / "leaderboard.json")
    require(isinstance(leaderboard, dict), "leaderboard.json must be an object")

    if isinstance(leaderboard.get("rows"), list):
        rows = leaderboard["rows"]
        require("filters" in leaderboard, "leaderboard.json with rows must include filters")
        for row in rows:
            require(isinstance(row, dict), "leaderboard rows must be objects")
            for key in ("rank", "submission_id", "agent", "model", "score", "date", "submitter", "scope"):
                require(key in row, f"leaderboard row missing {key}")
            require(row.get("complete") is not False, f"leaderboard row {row.get('submission_id')} is incomplete")
            detail = read_json(data_dir / "submissions" / f"{row['submission_id']}.json")
            validate_submission_archive_contract(detail, str(row["submission_id"]))
        return

    conditions = leaderboard.get("conditions")
    require(isinstance(conditions, list), "leaderboard.json must include rows or conditions")
    grid = leaderboard.get("grid")
    require(isinstance(grid, dict), "leaderboard.grid must be an object")
    require(grid.get("requiredSeeds") == REQUIRED_SEEDS, "leaderboard.grid.requiredSeeds must be [0, 1, 2, 3, 4]")
    for row in conditions:
        require(isinstance(row, dict), "condition records must be objects")
        require(row.get("score") is not None, f"condition record {row.get('id')} has no score")
        if "seedIds" in row:
            require(row["seedIds"] == REQUIRED_SEEDS, f"condition record {row.get('id')} must list seeds {REQUIRED_SEEDS}")


def validate_submission_archive_contract(detail: Any, submission_id: str) -> None:
    require(isinstance(detail, dict), f"{submission_id}.json must be an object")
    per_seed_results = detail.get("per_seed_results")
    require(isinstance(per_seed_results, list) and per_seed_results, f"{submission_id}.json missing per_seed_results")
    contract = detail.get("archive_contract")
    require(isinstance(contract, dict), f"{submission_id}.json missing archive_contract")
    require(contract.get("schema_version") == 1, f"{submission_id}.json has unknown archive schema")
    for key in ("contains_raw_scores", "contains_raw_trajectories", "contains_artifacts"):
        require(contract.get(key) is True, f"{submission_id}.json archive contract missing {key}")
    require(
        contract.get("run_count") == len(per_seed_results),
        f"{submission_id}.json archive run count must match per_seed_results",
    )


def validate_environments(data_dir: Path, summary: dict[str, Any]) -> None:
    simulators = summary.get("simulators") or ["BallDrop", "BounceBall", "MassSlide"]
    require(isinstance(simulators, list) and simulators, "summary.json must include simulators")
    require(simulators == REQUIRED_SIMULATORS, "summary.json simulators must match the public TraceBench simulators")
    for simulator in simulators:
        env_dir = data_dir / "environments" / str(simulator)
        description = read_json(env_dir / "description.json")
        require(isinstance(description, dict), f"{simulator}/description.json must be an object")
        require(description.get("name"), f"{simulator}/description.json missing name")
        require(description.get("short_one_line_description"), f"{simulator}/description.json missing short description")
        validate_prompt_combinations(description.get("prompt_combinations"), simulator)
        if simulator == "BallDrop":
            validate_homepage_prompt_combinations(description.get("homepage_prompt_combinations"), simulator)
        else:
            require(
                "homepage_prompt_combinations" not in description,
                f"{simulator}/description.json must not include homepage_prompt_combinations",
            )
        expected_download_link = (
            "https://huggingface.co/datasets/eth-siplab/tracebench/tree/main/questions/"
            f"{simulator}"
        )
        require(
            description.get("download_link") == expected_download_link,
            f"{simulator}/description.json download_link must point to the Hugging Face question directory",
        )
        sample_count = int(description.get("sample_count") or 5)
        require(sample_count >= 1, f"{simulator} sample_count must be positive")
        for sample_number in range(1, min(sample_count, 5) + 1):
            sample = read_json(env_dir / f"data_{sample_number}.json")
            require(isinstance(sample.get("rows"), list), f"{simulator}/data_{sample_number}.json must include rows")
            require(sample.get("intervention_time") is not None, f"{simulator}/data_{sample_number}.json missing intervention_time")
        expected_files = {"description.json"} | {f"data_{sample_number}.json" for sample_number in range(1, 6)}
        actual_files = {path.name for path in env_dir.glob("*.json")}
        require(actual_files == expected_files, f"{simulator} environment directory must contain only description.json and data_1.json through data_5.json")


def validate_homepage_data(data_dir: Path) -> None:
    homepage_data = read_json(data_dir / "environments" / HOMEPAGE_DATA_FILENAME)
    require(isinstance(homepage_data, dict), f"{HOMEPAGE_DATA_FILENAME} must be an object")
    require(homepage_data.get("run_id"), f"{HOMEPAGE_DATA_FILENAME} missing run_id")
    require(
        homepage_data.get("source") == "programmatic:ball-drop-bounce-gif",
        f"{HOMEPAGE_DATA_FILENAME} source must identify the GIF-matching generator",
    )
    require(
        homepage_data.get("intervention_time") is not None,
        f"{HOMEPAGE_DATA_FILENAME} missing intervention_time",
    )
    require(homepage_data.get("intervention_parameter"), f"{HOMEPAGE_DATA_FILENAME} missing intervention_parameter")
    require(homepage_data.get("answer"), f"{HOMEPAGE_DATA_FILENAME} missing answer")

    columns = set(homepage_data.get("columns") or [])
    require(
        HOMEPAGE_REQUIRED_COLUMNS.issubset(columns),
        f"{HOMEPAGE_DATA_FILENAME} columns must include {sorted(HOMEPAGE_REQUIRED_COLUMNS)}",
    )
    rows = homepage_data.get("rows")
    require(isinstance(rows, list) and rows, f"{HOMEPAGE_DATA_FILENAME} must include non-empty rows")
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"{HOMEPAGE_DATA_FILENAME} rows[{index}] must be an object")
        for column in HOMEPAGE_REQUIRED_COLUMNS:
            require(column in row, f"{HOMEPAGE_DATA_FILENAME} rows[{index}] missing {column}")
            require(isinstance(row[column], (int, float)), f"{HOMEPAGE_DATA_FILENAME} rows[{index}].{column} must be numeric")


def validate_prompt_combinations(value: Any, simulator: str) -> None:
    validate_prompt_combination_set(value, simulator, "prompt_combinations")


def validate_homepage_prompt_combinations(value: Any, simulator: str) -> None:
    validate_prompt_combination_set(value, simulator, "homepage_prompt_combinations")
    assert isinstance(value, list)
    for index, item in enumerate(value):
        agent_instruction = str(item.get("agent_instruction") or "").strip()
        for snippet in FORBIDDEN_WEBSITE_PROMPT_SNIPPETS:
            require(
                snippet not in agent_instruction,
                f"{simulator} homepage_prompt_combinations[{index}] contains full-agent prompt section {snippet!r}",
            )
        for snippet in FORBIDDEN_HOMEPAGE_RANKED_PROMPT_SNIPPETS:
            require(
                snippet not in agent_instruction,
                f"{simulator} homepage_prompt_combinations[{index}] contains ranked-list prompt text {snippet!r}",
            )


def validate_prompt_combination_set(value: Any, simulator: str, field_name: str) -> None:
    require(isinstance(value, list) and value, f"{simulator}/description.json missing {field_name}")
    require(
        len(value) == len(EXPECTED_PROMPT_COMBINATIONS),
        f"{simulator}/description.json must contain exactly {len(EXPECTED_PROMPT_COMBINATIONS)} {field_name}",
    )
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(value):
        require(isinstance(item, dict), f"{simulator} {field_name}[{index}] must be an object")
        desc_level = str(item.get("desc_level") or "").strip()
        task_type = str(item.get("task_type") or "").strip()
        training_samples = str(item.get("training_samples") or "").strip()
        agent_instruction = str(item.get("agent_instruction") or "").strip()
        require(desc_level, f"{simulator} {field_name}[{index}] missing desc_level")
        require(task_type, f"{simulator} {field_name}[{index}] missing task_type")
        require(training_samples, f"{simulator} {field_name}[{index}] missing training_samples")
        require(agent_instruction, f"{simulator} {field_name}[{index}] missing agent_instruction")
        require(
            not agent_instruction.startswith(MALFORMED_PROMPT_PREFIX),
            f"{simulator} {field_name}[{index}] appears to contain stale pre-rendered website prompt text",
            )
        key = (desc_level, task_type, training_samples)
        require(key not in seen, f"{simulator}/description.json has duplicate {field_name}: {key}")
        seen.add(key)
    missing = sorted(EXPECTED_PROMPT_COMBINATIONS - seen)
    require(not missing, f"{simulator}/description.json missing {field_name}: {missing}")


def validate_site_metadata(root: Path = ROOT) -> None:
    site = read_json(root / "site.json")
    require(isinstance(site, dict), "site.json must be an object")
    for key in ("website_url", "code_url", "hf_repo", "hf_dataset_url", "issues_url", "affiliation_url", "paper_url", "contributors", "contact", "citation"):
        require(key in site, f"site.json missing {key}")
    require(isinstance(site["contributors"], list) and site["contributors"], "site.json contributors must be a non-empty list")
    for contributor in site["contributors"]:
        require(isinstance(contributor, dict), "site.json contributor entries must be objects")
        require(contributor.get("name"), "site.json contributor missing name")
        require("affiliation" in contributor, "site.json contributor missing affiliation")
    require(isinstance(site["contact"], dict), "site.json contact must be an object")
    require(site["contact"].get("label"), "site.json contact missing label")
    require(site["contact"].get("url"), "site.json contact missing url")
    require(isinstance(site["citation"], str) and site["citation"].strip(), "site.json citation must be a non-empty string")
    expected_values = {
        "website_url": "https://tracebench.github.io/",
        "code_url": "https://github.com/TommasoBendinelli/TraceBench",
        "hf_repo": "eth-siplab/tracebench",
        "hf_dataset_url": "https://huggingface.co/datasets/eth-siplab/tracebench",
        "issues_url": "https://github.com/TommasoBendinelli/TraceBench/issues",
    }
    for key, expected in expected_values.items():
        require(site.get(key) == expected, f"site.json {key} must be {expected}")
    require("TraceBench" in site["citation"], "site.json citation must identify TraceBench")


def validate(data_dir: Path = DATA) -> None:
    validate_site_metadata()
    summary = read_json(data_dir / "summary.json")
    require(isinstance(summary, dict), "summary.json must be an object")
    canonical = summary.get("canonical_scope")
    require(isinstance(canonical, dict), "summary.json must include canonical_scope")
    for key in ("task_mode", "noise", "context", "examples"):
        require(key in canonical, f"canonical_scope missing {key}")

    validate_leaderboard(data_dir)
    validate_homepage_data(data_dir)
    validate_environments(data_dir, summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate generated TraceBench website JSON data.")
    parser.add_argument("--data-dir", type=Path, default=DATA)
    args = parser.parse_args()
    try:
        validate(args.data_dir)
    except ValidationError as exc:
        raise SystemExit(f"Website data validation failed: {exc}") from exc
    print(f"Validated TraceBench website data in {args.data_dir}")


if __name__ == "__main__":
    main()

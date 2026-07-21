#!/usr/bin/env python3
"""Synchronize Hugging Face benchmark JSON into the static website tree."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from typing import Any, Callable, Mapping, Sequence

from validate_website_data import validate


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public" / "data"
DEFAULT_REPO = "eth-siplab/tracebench"
TOP_LEVEL_FILES = ("summary.json", "leaderboard.json")
SIMULATORS = ("BallDrop", "BounceBall", "MassSlide")
HOMEPAGE_DATA_FILENAME = "data_main_page.json"
PROMPT_DESC_LEVELS = ("high", "none")
PROMPT_TASK_TYPES = ("direct", "code")
PROMPT_TRAINING_SAMPLES = ("none", "one", "multiple")
WEBSITE_PROMPT_FIELDS = (
    "sample_source",
    "environment_description",
    "intervention_semantics",
    "label_space",
    "no_change_guidance",
    "task_artifact",
    "prediction_format",
)
TRACEBENCH_DOCUMENTED_PROMPT_FIELDS = (
    "sample_source",
    "environment_description",
    "observed_columns",
    "intervention_semantics",
    "label_space",
    "no_change_guidance",
    "task_artifact",
    "prediction_format",
    "fewshot_context",
    "mode_specific_requirements",
    "evaluation",
    "runtime_constraints",
)
TRACEBENCH_LEGACY_PROMPT_FIELDS = (
    "first_sentence",
    "model_description",
    "shared_description",
    "task_instruction",
)
TRACEBENCH_PROMPT_PLACEHOLDER_RE = re.compile(r"\{([^{}\n]+)\}")

MAIN_PAGE_DURATION_S = 4.0
MAIN_PAGE_G = 13.5
MAIN_PAGE_INITIAL_HEIGHT = 1.0
MAIN_PAGE_PRE_INTERVENTION_E = 0.9
MAIN_PAGE_POST_INTERVENTION_E = 0.2
MAIN_PAGE_NEGLIGIBLE_HEIGHT = 0.005 * 1.05
MAIN_PAGE_TRACE_HZ = 400


PromptRenderer = Callable[..., str]


@dataclass(frozen=True)
class HomepageSegment:
    kind: str
    start: float
    end: float
    peak: float


def build_homepage_segments() -> tuple[list[HomepageSegment], float]:
    segments: list[HomepageSegment] = []
    time_s = 0.0

    fall_time = math.sqrt(2.0 * MAIN_PAGE_INITIAL_HEIGHT / MAIN_PAGE_G)
    segments.append(HomepageSegment("drop", time_s, time_s + fall_time, MAIN_PAGE_INITIAL_HEIGHT))
    time_s += fall_time

    previous_peak = MAIN_PAGE_INITIAL_HEIGHT
    for _ in range(3):
        peak = MAIN_PAGE_PRE_INTERVENTION_E * previous_peak
        bounce_time = 2.0 * math.sqrt(2.0 * peak / MAIN_PAGE_G)
        segments.append(HomepageSegment("bounce", time_s, time_s + bounce_time, peak))
        time_s += bounce_time
        previous_peak = peak

    intervention_time = time_s

    while MAIN_PAGE_POST_INTERVENTION_E * previous_peak >= MAIN_PAGE_NEGLIGIBLE_HEIGHT:
        peak = MAIN_PAGE_POST_INTERVENTION_E * previous_peak
        bounce_time = 2.0 * math.sqrt(2.0 * peak / MAIN_PAGE_G)
        segments.append(HomepageSegment("bounce", time_s, time_s + bounce_time, peak))
        time_s += bounce_time
        previous_peak = peak

    return segments, intervention_time


HOMEPAGE_SEGMENTS, HOMEPAGE_INTERVENTION_TIME = build_homepage_segments()


def homepage_segment_at(time_s: float) -> HomepageSegment | None:
    for segment in HOMEPAGE_SEGMENTS:
        if segment.start <= time_s < segment.end:
            return segment
    return None


def homepage_position_velocity(time_s: float) -> tuple[float, float]:
    segment = homepage_segment_at(time_s)
    if segment is None:
        return 0.0, 0.0

    tau = time_s - segment.start
    if segment.kind == "drop":
        position = segment.peak - 0.5 * MAIN_PAGE_G * tau * tau
        velocity = -MAIN_PAGE_G * tau
    else:
        duration = segment.end - segment.start
        centered = tau - 0.5 * duration
        position = segment.peak - 0.5 * MAIN_PAGE_G * centered * centered
        velocity = -MAIN_PAGE_G * centered

    return max(position, 0.0), velocity


def generate_homepage_data() -> dict[str, Any]:
    impact_impulses = {
        round(segment.end, 6): round(math.sqrt(2.0 * MAIN_PAGE_G * segment.peak), 6)
        for segment in HOMEPAGE_SEGMENTS
        if 0.0 <= segment.end <= MAIN_PAGE_DURATION_S
    }
    times = {
        round(index / MAIN_PAGE_TRACE_HZ, 6)
        for index in range(int(MAIN_PAGE_DURATION_S * MAIN_PAGE_TRACE_HZ) + 1)
    }
    times.update(round(segment.start, 6) for segment in HOMEPAGE_SEGMENTS)
    times.update(round(segment.end, 6) for segment in HOMEPAGE_SEGMENTS)
    times.add(round(MAIN_PAGE_DURATION_S, 6))

    rows = []
    for time_s in sorted(time for time in times if 0.0 <= time <= MAIN_PAGE_DURATION_S):
        position, velocity = homepage_position_velocity(time_s)
        rows.append(
            {
                "time": time_s,
                "Position": round(position, 6),
                "Velocity": round(velocity, 6),
                "Hard_Stop_f": impact_impulses.get(time_s, 0.0),
            }
        )

    return {
        "run_id": "ball-drop-main-page",
        "source": "programmatic:ball-drop-bounce-gif",
        "columns": ["Position", "Velocity", "Hard_Stop_f", "time"],
        "intervention_time": round(HOMEPAGE_INTERVENTION_TIME, 6),
        "intervention_parameter": "coefficient of restitution",
        "answer": "coefficient of restitution",
        "rows": rows,
    }


def write_homepage_data(environments_dir: Path) -> None:
    environments_dir.mkdir(parents=True, exist_ok=True)
    write_json(environments_dir / HOMEPAGE_DATA_FILENAME, generate_homepage_data())


def ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stringify_tracebench_prompt_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value))
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True)
    return str(value or "")


def default_prompt_field_entries(question_text: Mapping[str, Any]) -> list[tuple[str, str]]:
    fields = (
        TRACEBENCH_DOCUMENTED_PROMPT_FIELDS
        if any(field in question_text for field in TRACEBENCH_DOCUMENTED_PROMPT_FIELDS)
        else TRACEBENCH_LEGACY_PROMPT_FIELDS
    )
    return [(field, "\n\n" if index < len(fields) - 1 else "") for index, field in enumerate(fields)]


def tracebench_prompt_field_entries(question_text: Mapping[str, Any]) -> list[tuple[str, str]]:
    field_order_raw = question_text.get("ordered_field_agent_prompt")
    if not (isinstance(field_order_raw, list) and field_order_raw):
        return default_prompt_field_entries(question_text)

    entries: list[tuple[str, str]] = []
    for index, raw_entry in enumerate(field_order_raw):
        if isinstance(raw_entry, (list, tuple)):
            if not raw_entry:
                continue
            field = str(raw_entry[0]).strip()
            separator = str(raw_entry[1]) if len(raw_entry) > 1 else "\n\n"
        else:
            field = str(raw_entry).strip()
            separator = "\n\n"
        if not field:
            continue
        if index == len(field_order_raw) - 1 and not isinstance(raw_entry, (list, tuple)):
            separator = ""
        entries.append((field, separator))
    return entries or default_prompt_field_entries(question_text)


def resolve_mapping_path(mapping: Mapping[str, Any], path: Sequence[str]) -> Any:
    value: Any = mapping
    for part in path:
        key = str(part).strip()
        if not key:
            raise KeyError("empty placeholder path segment")
        if not isinstance(value, Mapping) or key not in value:
            raise KeyError(key)
        value = value[key]
    return value


def question_text_for_placeholder(
    *,
    target_slug: str | None,
    current_question_text: Mapping[str, Any],
    current_question_slug: str | None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None,
) -> Mapping[str, Any]:
    if not target_slug or target_slug == current_question_slug:
        return current_question_text
    if questions_by_id is None:
        raise KeyError(target_slug)
    target_question = questions_by_id.get(target_slug)
    if not isinstance(target_question, Mapping):
        raise KeyError(target_slug)
    target_question_text = target_question.get("question_text")
    if not isinstance(target_question_text, Mapping):
        raise KeyError(f"{target_slug}.question_text")
    return target_question_text


def placeholder_target_slug(raw_slug: str | None, *, current_question_slug: str | None) -> str | None:
    target_slug = str(raw_slug or "").strip()
    if target_slug.startswith("<") and target_slug.endswith(">"):
        target_slug = target_slug[1:-1].strip()
    if target_slug == "question_slug":
        return current_question_slug
    return target_slug or current_question_slug


def resolve_tracebench_question_text_placeholder(
    *,
    body: str,
    target_slug: str | None,
    field_path: str,
    current_question_text: Mapping[str, Any],
    current_question_slug: str | None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None,
    questions_metadata: Mapping[str, Any] | None,
    stack: tuple[str, ...],
) -> str:
    if not field_path:
        raise KeyError(body)
    lookup_key = f"{target_slug or '<current>'}.question_text.{field_path}"
    if lookup_key in stack:
        raise ValueError(f"Recursive TraceBench prompt placeholder: {lookup_key}")
    source_text = question_text_for_placeholder(
        target_slug=target_slug,
        current_question_text=current_question_text,
        current_question_slug=current_question_slug,
        questions_by_id=questions_by_id,
    )
    try:
        resolved_value = resolve_mapping_path(source_text, field_path.split("."))
    except KeyError as exc:
        raise KeyError(f"Could not resolve TraceBench prompt placeholder {{{body}}}") from exc
    return resolve_tracebench_prompt_placeholders(
        resolved_value,
        current_question_text=current_question_text,
        current_question_slug=current_question_slug,
        questions_by_id=questions_by_id,
        questions_metadata=questions_metadata,
        stack=(*stack, lookup_key),
    )


def resolve_tracebench_questions_namespace_placeholder(
    *,
    body: str,
    current_question_text: Mapping[str, Any],
    current_question_slug: str | None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None,
    questions_metadata: Mapping[str, Any] | None,
    stack: tuple[str, ...],
) -> str:
    tail = body.removeprefix("questions.").strip()
    if not tail:
        raise KeyError(body)
    target_slug, separator, field_path = tail.partition(".question_text.")
    if separator:
        return resolve_tracebench_question_text_placeholder(
            body=body,
            target_slug=placeholder_target_slug(target_slug, current_question_slug=current_question_slug),
            field_path=field_path,
            current_question_text=current_question_text,
            current_question_slug=current_question_slug,
            questions_by_id=questions_by_id,
            questions_metadata=questions_metadata,
            stack=stack,
        )
    if questions_metadata is None:
        raise KeyError(body)
    lookup_key = f"questions.{tail}"
    if lookup_key in stack:
        raise ValueError(f"Recursive TraceBench prompt placeholder: {lookup_key}")
    try:
        resolved_value = resolve_mapping_path(questions_metadata, tail.split("."))
    except KeyError as exc:
        raise KeyError(f"Could not resolve TraceBench prompt placeholder {{{body}}}") from exc
    return resolve_tracebench_prompt_placeholders(
        resolved_value,
        current_question_text=current_question_text,
        current_question_slug=current_question_slug,
        questions_by_id=questions_by_id,
        questions_metadata=questions_metadata,
        stack=(*stack, lookup_key),
    )


def resolve_tracebench_prompt_placeholder_body(
    body: str,
    *,
    current_question_text: Mapping[str, Any],
    current_question_slug: str | None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None,
    questions_metadata: Mapping[str, Any] | None,
    stack: tuple[str, ...],
) -> str | None:
    if body.startswith("questions."):
        return resolve_tracebench_questions_namespace_placeholder(
            body=body,
            current_question_text=current_question_text,
            current_question_slug=current_question_slug,
            questions_by_id=questions_by_id,
            questions_metadata=questions_metadata,
            stack=stack,
        )
    if body.startswith("<question_slug>.question_text."):
        return resolve_tracebench_question_text_placeholder(
            body=body,
            target_slug=current_question_slug,
            field_path=body.removeprefix("<question_slug>.question_text."),
            current_question_text=current_question_text,
            current_question_slug=current_question_slug,
            questions_by_id=questions_by_id,
            questions_metadata=questions_metadata,
            stack=stack,
        )
    if body.startswith("question_text."):
        return resolve_tracebench_question_text_placeholder(
            body=body,
            target_slug=current_question_slug,
            field_path=body.removeprefix("question_text."),
            current_question_text=current_question_text,
            current_question_slug=current_question_slug,
            questions_by_id=questions_by_id,
            questions_metadata=questions_metadata,
            stack=stack,
        )
    target_slug, separator, field_path = body.partition(".question_text.")
    if not separator:
        return None
    return resolve_tracebench_question_text_placeholder(
        body=body,
        target_slug=placeholder_target_slug(target_slug, current_question_slug=current_question_slug),
        field_path=field_path,
        current_question_text=current_question_text,
        current_question_slug=current_question_slug,
        questions_by_id=questions_by_id,
        questions_metadata=questions_metadata,
        stack=stack,
    )


def resolve_tracebench_prompt_placeholders(
    value: Any,
    *,
    current_question_text: Mapping[str, Any],
    current_question_slug: str | None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None,
    questions_metadata: Mapping[str, Any] | None,
    stack: tuple[str, ...] = (),
) -> str:
    text = stringify_tracebench_prompt_value(value)
    if "question_text." not in text and "{questions." not in text:
        return text.strip()

    def replace(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        resolved = resolve_tracebench_prompt_placeholder_body(
            body,
            current_question_text=current_question_text,
            current_question_slug=current_question_slug,
            questions_by_id=questions_by_id,
            questions_metadata=questions_metadata,
            stack=stack,
        )
        return match.group(0) if resolved is None else resolved

    return TRACEBENCH_PROMPT_PLACEHOLDER_RE.sub(replace, text).strip()


def website_prompt_field(
    question_text: Mapping[str, Any],
    field: str,
    *,
    question_slug: str | None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None,
    questions_metadata: Mapping[str, Any] | None = None,
) -> str:
    if field not in question_text:
        return ""
    return resolve_tracebench_prompt_placeholders(
        question_text.get(field),
        current_question_text=question_text,
        current_question_slug=str(question_slug or "").strip() or None,
        questions_by_id=questions_by_id,
        questions_metadata=questions_metadata,
    )


def first_sentence(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    match = re.search(r"(?<=[.!?])(?:\s|$)", cleaned)
    return cleaned[: match.start()].strip() if match else cleaned


def combine_context(sample_source: str, environment_description: str, desc_level: str) -> str:
    source = str(sample_source or "").strip()
    description = str(environment_description or "").strip()
    if desc_level == "high":
        description = first_sentence(description)
    if not source:
        return description
    if not description:
        return source
    if source.endswith(".") or source.endswith(":"):
        return f"{source}\n{description}"
    return f"{source} {description}"


def website_fewshot_context(task_type: str, training_samples: str) -> str:
    if training_samples == "none":
        return ""
    count = "one labeled example per class" if training_samples == "one" else "multiple labeled examples per class"
    suffix = " while developing rule.py" if task_type == "code" else ""
    return (
        f"To help with this task, you can use the labeled train_samples/ directory containing {count}"
        f"{suffix}. The corresponding labels are available in train_labels.json."
    )


def compact_label_space(text: str) -> str:
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in str(text or "").splitlines()).strip()


def website_task_artifact(text: str, task_type: str) -> str:
    rendered = str(text or "").strip()
    if task_type == "code":
        return re.sub(r"def predict\(df\)\s*->\s*[^:\n]+:", "def predict(df) -> str:", rendered)
    return rendered


def website_prediction_format(desc_level: str, task_type: str) -> str:
    if task_type != "code":
        return (
            "For each file in test_samples/, return the correct label.\n"
            "The output must be valid JSON with exactly this structure:\n"
            "{\n\n"
            '  "<filename_1>": "<correct_label_filename_1>",\n'
            '  "<filename_2>": "<correct_label_filename_2>"\n\n'
            "}"
        )
    input_description = (
        "one column, 'ball height'"
        if desc_level == "high"
        else "one column, col1"
    )
    return (
        f"The input df is a pandas DataFrame containing one sample with {input_description}.\n"
        "The function will be called on samples inside test_samples/ and on additional held-out samples with the same schema and label set."
    )


def render_website_task(task_artifact: str, desc_level: str, task_type: str) -> str:
    artifact = website_task_artifact(task_artifact, task_type)
    prediction_format = website_prediction_format(desc_level, task_type)
    return "\n".join(part for part in (artifact, prediction_format) if part.strip())


def render_website_prompt(
    question_text: Mapping[str, Any],
    *,
    question_slug: str | None = None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    desc_level: str,
    task_type: str,
    training_samples: str,
    questions_metadata: Mapping[str, Any] | None = None,
) -> str:
    values = {
        field: website_prompt_field(
            question_text,
            field,
            question_slug=question_slug,
            questions_by_id=questions_by_id,
            questions_metadata=questions_metadata,
        )
        for field in WEBSITE_PROMPT_FIELDS
    }
    context = combine_context(values["sample_source"], values["environment_description"], desc_level)
    task = render_website_task(values["task_artifact"], desc_level, task_type)
    blocks = [
        context,
        values["intervention_semantics"],
        compact_label_space(values["label_space"]),
        values["no_change_guidance"],
        task,
        website_fewshot_context(task_type, training_samples),
    ]
    return "\n\n".join(block for block in blocks if block.strip()).strip()


def render_documented_tracebench_agent_prompt(
    question_text: Mapping[str, Any],
    *,
    question_slug: str | None = None,
    questions_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    questions_metadata: Mapping[str, Any] | None = None,
) -> str:
    prompt_parts: list[str] = []
    pending_separator = ""
    for field, separator in tracebench_prompt_field_entries(question_text):
        if field not in question_text:
            if prompt_parts:
                pending_separator = separator
            continue
        rendered = resolve_tracebench_prompt_placeholders(
            question_text.get(field),
            current_question_text=question_text,
            current_question_slug=str(question_slug or "").strip() or None,
            questions_by_id=questions_by_id,
            questions_metadata=questions_metadata,
        )
        if rendered:
            if prompt_parts:
                prompt_parts.append(pending_separator)
            prompt_parts.append(rendered)
            pending_separator = separator
        elif prompt_parts:
            pending_separator = separator
    return "".join(prompt_parts)


def hf_url(repo: str, path: str, revision: str) -> str:
    encoded = "/".join(quote(part) for part in path.split("/"))
    return f"https://huggingface.co/datasets/{repo}/resolve/{quote(revision)}/{encoded}"


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {}
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
            destination.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"failed to download {url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to download {url}: {exc.reason}") from exc


def candidate_tracebench_roots(explicit_root: Path | None) -> list[Path]:
    roots: list[Path] = []
    if explicit_root is not None:
        roots.append(explicit_root)
    env_root = (
        os.environ.get("TRACEBENCH_REPO_ROOT", "").strip()
        or os.environ.get("TSENV_REPO_ROOT", "").strip()
    )
    if env_root:
        roots.append(Path(env_root))
    roots.extend(
        [
            ROOT / "_TraceBench",
            ROOT.parent / "TraceBench",
            ROOT.parent / "tracebench",
            ROOT / "_tsENV",
            ROOT.parent / "tsENV",
            ROOT.parent / "tsenv",
        ]
    )
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def load_prompt_renderer(tracebench_root: Path | None) -> PromptRenderer:
    checked: list[str] = []
    for root in candidate_tracebench_roots(tracebench_root):
        checked.append(str(root))
        if not (root / "shared" / "prompts.py").is_file():
            continue
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from shared.prompts import render_tsenv_agent_prompt
        except Exception as exc:  # noqa: BLE001 - expose import failure with context.
            raise RuntimeError(f"could not import TraceBench prompt renderer from {root}: {exc}") from exc
        return render_tsenv_agent_prompt
    print(
        "TraceBench prompt renderer checkout not found; using local documented prompt renderer. "
        f"Checked: {', '.join(checked)}",
        file=sys.stderr,
    )
    return render_documented_tracebench_agent_prompt


def sample_bucket(value: Any) -> str | None:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return "none"
    if count == 1:
        return "one"
    return "multiple"


def select_question(
    questions: dict[str, Any],
    *,
    desc_level: str,
    task_type: str,
    training_samples: str,
) -> tuple[str, dict[str, Any]]:
    def question_matches(question: dict[str, Any], *, allow_positive_fallback: bool = False) -> bool:
        recipe = question.get("recipe_info")
        if not isinstance(recipe, dict):
            return False
        if str(recipe.get("desc_level") or "").strip().lower() != desc_level:
            return False
        if str(recipe.get("type_of_request") or "").strip().lower() != task_type:
            return False
        bucket = sample_bucket(recipe.get("number_train_samples_per_class"))
        if bucket == training_samples:
            return True
        return allow_positive_fallback and training_samples in {"one", "multiple"} and bucket in {"one", "multiple"}

    for allow_positive_fallback in (False, True):
        for question_slug in sorted(questions):
            question = questions[question_slug]
            if not isinstance(question, dict):
                continue
            if not question_matches(question, allow_positive_fallback=allow_positive_fallback):
                continue
            question_text = question.get("question_text")
            if not isinstance(question_text, dict):
                raise RuntimeError(f"question {question_slug} has no question_text object")
            return question_slug, question
    raise RuntimeError(
        "could not find representative question for "
        f"desc_level={desc_level!r}, task_type={task_type!r}, "
        f"training_samples={training_samples!r}"
    )


def rendered_prompt_combinations(questions_path: Path, renderer: PromptRenderer) -> list[dict[str, str]]:
    payload = read_json(questions_path)
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, dict) or not questions:
        raise RuntimeError(f"{questions_path} must contain a non-empty questions object")

    combinations: list[dict[str, str]] = []
    for desc_level in PROMPT_DESC_LEVELS:
        for training_samples in PROMPT_TRAINING_SAMPLES:
            for task_type in PROMPT_TASK_TYPES:
                question_slug, question = select_question(
                    questions,
                    desc_level=desc_level,
                    task_type=task_type,
                    training_samples=training_samples,
                )
                rendered = renderer(
                    question["question_text"],
                    question_slug=question_slug,
                    questions_by_id=questions,
                ).strip()
                if not rendered:
                    raise RuntimeError(f"rendered prompt for {question_slug} is empty")
                combinations.append(
                    {
                        "desc_level": desc_level,
                        "task_type": task_type,
                        "training_samples": training_samples,
                        "agent_instruction": rendered,
                    }
                )
    return combinations


def rendered_homepage_prompt_combinations(questions_path: Path) -> list[dict[str, str]]:
    payload = read_json(questions_path)
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, dict) or not questions:
        raise RuntimeError(f"{questions_path} must contain a non-empty questions object")

    combinations: list[dict[str, str]] = []
    for desc_level in PROMPT_DESC_LEVELS:
        for training_samples in PROMPT_TRAINING_SAMPLES:
            for task_type in PROMPT_TASK_TYPES:
                question_slug, question = select_question(
                    questions,
                    desc_level=desc_level,
                    task_type=task_type,
                    training_samples=training_samples,
                )
                rendered = render_website_prompt(
                    question["question_text"],
                    question_slug=question_slug,
                    questions_by_id=questions,
                    desc_level=desc_level,
                    task_type=task_type,
                    training_samples=training_samples,
                ).strip()
                if not rendered:
                    raise RuntimeError(f"rendered prompt for {question_slug} is empty")
                combinations.append(
                    {
                        "desc_level": desc_level,
                        "task_type": task_type,
                        "training_samples": training_samples,
                        "agent_instruction": rendered,
                    }
                )
    return combinations


def sync_from_hf(repo: str, revision: str, output_dir: Path, tracebench_root: Path | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_renderer = load_prompt_renderer(tracebench_root)
    for filename in TOP_LEVEL_FILES:
        download_file(hf_url(repo, f"website/{filename}", revision), output_dir / filename)
    environments_dir = output_dir / "environments"
    if environments_dir.exists():
        shutil.rmtree(environments_dir)
    write_homepage_data(environments_dir)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for simulator in SIMULATORS:
            base = f"website/environments/{simulator}"
            env_dir = output_dir / "environments" / simulator
            description_path = env_dir / "description.json"
            questions_path = tmp_dir / f"{simulator}_questions.json"
            download_file(hf_url(repo, f"{base}/description.json", revision), description_path)
            download_file(hf_url(repo, f"questions/{simulator}/questions.json", revision), questions_path)
            description = read_json(description_path)
            if not isinstance(description, dict):
                raise RuntimeError(f"{description_path} must contain a JSON object")
            description["prompt_combinations"] = rendered_prompt_combinations(questions_path, prompt_renderer)
            if simulator == "BallDrop":
                description["homepage_prompt_combinations"] = rendered_homepage_prompt_combinations(questions_path)
            else:
                description.pop("homepage_prompt_combinations", None)
            write_json(description_path, description)
            for sample_number in range(1, 6):
                download_file(hf_url(repo, f"{base}/data_{sample_number}.json", revision), env_dir / f"data_{sample_number}.json")

    submissions_manifest = output_dir / "submissions_manifest.txt"
    try:
        download_file(hf_url(repo, "website/submissions_manifest.txt", revision), submissions_manifest)
    except RuntimeError:
        submissions_manifest.write_text("", encoding="utf-8")

    submissions_dir = output_dir / "submissions"
    if submissions_dir.exists():
        shutil.rmtree(submissions_dir)
    submissions_dir.mkdir(exist_ok=True)
    for line in submissions_manifest.read_text(encoding="utf-8").splitlines():
        submission_id = line.strip()
        if not submission_id:
            continue
        download_file(
            hf_url(repo, f"website/submissions/{submission_id}.json", revision),
            submissions_dir / f"{submission_id}.json",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync TraceBench website data from Hugging Face.")
    parser.add_argument("--hf-repo", default=DEFAULT_REPO)
    parser.add_argument("--revision", default="main")
    parser.add_argument("--output-dir", type=Path, default=DATA)
    parser.add_argument("--source-dir", type=Path, help="Copy browser-ready data from a local directory instead of Hugging Face.")
    parser.add_argument(
        "--tracebench-root",
        "--tsenv-root",
        dest="tracebench_root",
        type=Path,
        help="Path to a TraceBench checkout containing shared/prompts.py.",
    )
    parser.add_argument("--validate-only", action="store_true", help="Only validate the output directory.")
    args = parser.parse_args()

    if args.source_dir:
        copy_tree(args.source_dir, args.output_dir)
    elif not args.validate_only:
        sync_from_hf(args.hf_repo, args.revision, args.output_dir, args.tracebench_root)

    try:
        validate(args.output_dir)
    except Exception as exc:  # noqa: BLE001 - CLI should present one concise failure.
        raise SystemExit(str(exc)) from exc
    print(f"Website data ready in {args.output_dir}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

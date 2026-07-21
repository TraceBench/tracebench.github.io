#!/usr/bin/env python3
"""Create static entry points for every public TraceBench SPA route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROUTES = ("results", "environments", "get-started", "contributors")


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def safe_segment(value: object, *, source: str) -> str:
    segment = str(value or "").strip()
    if not segment or segment in {".", ".."} or Path(segment).name != segment:
        raise ValueError(f"unsafe route segment from {source}: {segment!r}")
    return segment


def public_routes(root: Path) -> list[Path]:
    routes = [Path(route) for route in STATIC_ROUTES]

    submissions_dir = root / "public" / "data" / "submissions"
    for submission_path in sorted(submissions_dir.glob("*.json")):
        submission = read_json(submission_path)
        submission_id = safe_segment(
            submission.get("submission_id"),
            source=str(submission_path),
        )
        if submission_id != submission_path.stem:
            raise ValueError(
                f"{submission_path} submission_id must match its filename"
            )
        routes.append(Path("results") / submission_id)

    summary_path = root / "public" / "data" / "summary.json"
    summary = read_json(summary_path)
    simulators = summary.get("simulators")
    if not isinstance(simulators, list) or not simulators:
        raise ValueError(f"{summary_path} must list simulators")
    for simulator in simulators:
        routes.append(
            Path("environments")
            / safe_segment(simulator, source=str(summary_path))
        )

    return routes


def materialize(root: Path) -> list[Path]:
    index_path = root / "index.html"
    index_html = index_path.read_bytes()
    targets: list[Path] = []
    for route in public_routes(root):
        target = root / route / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(index_html)
        targets.append(target)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create static entry points for TraceBench website routes."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    targets = materialize(args.root.resolve())
    print(f"Materialized {len(targets)} TraceBench routes")


if __name__ == "__main__":
    main()

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "materialize_routes",
    ROOT / "scripts" / "materialize_routes.py",
)
assert SPEC and SPEC.loader
materialize_routes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(materialize_routes)


class MaterializeRoutesTest(unittest.TestCase):
    def test_materializes_static_and_data_driven_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<title>TraceBench</title>", encoding="utf-8")
            submissions = root / "public" / "data" / "submissions"
            submissions.mkdir(parents=True)
            (submissions / "agent-a.json").write_text(
                json.dumps({"submission_id": "agent-a"}),
                encoding="utf-8",
            )
            (root / "public" / "data" / "summary.json").write_text(
                json.dumps({"simulators": ["BallDrop"]}),
                encoding="utf-8",
            )

            targets = materialize_routes.materialize(root)

            relative_targets = {target.relative_to(root) for target in targets}
            self.assertIn(Path("results/index.html"), relative_targets)
            self.assertIn(Path("results/agent-a/index.html"), relative_targets)
            self.assertIn(Path("environments/BallDrop/index.html"), relative_targets)
            self.assertEqual(
                (root / "results" / "agent-a" / "index.html").read_text(
                    encoding="utf-8"
                ),
                "<title>TraceBench</title>",
            )

    def test_rejects_unsafe_route_segments(self) -> None:
        with self.assertRaises(ValueError):
            materialize_routes.safe_segment("../escape", source="test")


if __name__ == "__main__":
    unittest.main()

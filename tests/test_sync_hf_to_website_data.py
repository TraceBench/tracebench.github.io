from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sync_hf_to_website_data = load_module(
    "sync_hf_to_website_data",
    ROOT / "scripts" / "sync_hf_to_website_data.py",
)
validate_website_data = load_module(
    "validate_website_data",
    ROOT / "scripts" / "validate_website_data.py",
)


class WebsitePromptRenderingTest(unittest.TestCase):
    def test_archive_contract_requires_raw_trajectory_payloads(self) -> None:
        detail = {
            "per_seed_results": [{"question_id": "q-0"}],
            "archive_contract": {
                "schema_version": 1,
                "contains_raw_scores": True,
                "contains_raw_trajectories": True,
                "contains_artifacts": True,
                "run_count": 1,
            },
        }

        validate_website_data.validate_submission_archive_contract(detail, "agent-a")
        detail["archive_contract"]["contains_raw_trajectories"] = False
        with self.assertRaises(validate_website_data.ValidationError):
            validate_website_data.validate_submission_archive_contract(detail, "agent-a")

    def question(self, *, desc_level: str, task_type: str, samples: int) -> dict[str, Any]:
        key = f"{desc_level}-{task_type}-{samples}"
        return {
            "recipe_info": {
                "desc_level": desc_level,
                "type_of_request": task_type,
                "number_train_samples_per_class": samples,
            },
            "question_text": {
                "sample_source": f"prompt source {key}",
                "ordered_field_agent_prompt": [["sample_source", ""]],
            },
        }

    def write_questions(self, path: Path) -> None:
        questions = {}
        for desc_level in ("high", "none"):
            for task_type in ("direct", "code"):
                for samples in (0, 1, 3):
                    questions[f"{desc_level}-{task_type}-{samples}"] = self.question(
                        desc_level=desc_level,
                        task_type=task_type,
                        samples=samples,
                    )
        path.write_text(json.dumps({"questions": questions}), encoding="utf-8")

    def test_prompt_combinations_are_rendered_from_question_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            questions_path = Path(tmp) / "questions.json"
            self.write_questions(questions_path)

            def fake_renderer(question_text: dict[str, Any], **kwargs: Any) -> str:
                return f"full::{kwargs['question_slug']}::{question_text['sample_source']}"

            combinations = sync_hf_to_website_data.rendered_prompt_combinations(questions_path, fake_renderer)

        self.assertEqual(len(combinations), 12)
        by_key = {
            (item["desc_level"], item["task_type"], item["training_samples"]): item["agent_instruction"]
            for item in combinations
        }
        self.assertEqual(
            by_key[("high", "direct", "none")],
            "full::high-direct-0::prompt source high-direct-0",
        )
        self.assertEqual(
            by_key[("none", "code", "one")],
            "full::none-code-1::prompt source none-code-1",
        )

    def test_homepage_prompt_combinations_are_rendered_in_tex_compact_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            questions_path = Path(tmp) / "questions.json"
            self.write_questions(questions_path)

            combinations = sync_hf_to_website_data.rendered_homepage_prompt_combinations(questions_path)

        self.assertEqual(len(combinations), 12)
        by_key = {
            (item["desc_level"], item["task_type"], item["training_samples"]): item["agent_instruction"]
            for item in combinations
        }
        self.assertEqual(
            by_key[("high", "direct", "none")],
            "prompt source high-direct-0\n\n"
            "For each file in test_samples/, return the correct label.\n"
            "The output must be valid JSON with exactly this structure:\n"
            "{\n\n"
            '  "<filename_1>": "<correct_label_filename_1>",\n'
            '  "<filename_2>": "<correct_label_filename_2>"\n\n'
            "}",
        )
        self.assertEqual(
            by_key[("none", "code", "one")],
            "prompt source none-code-1\n\n"
            "The input df is a pandas DataFrame containing one sample with one column, col1.\n"
            "The function will be called on samples inside test_samples/ and on additional held-out samples with the same schema and label set.\n\n"
            "To help with this task, you can use the labeled train_samples/ directory containing one labeled example per class while developing rule.py. "
            "The corresponding labels are available in train_labels.json.",
        )
        self.assertEqual(
            by_key[("none", "code", "multiple")],
            "prompt source none-code-3\n\n"
            "The input df is a pandas DataFrame containing one sample with one column, col1.\n"
            "The function will be called on samples inside test_samples/ and on additional held-out samples with the same schema and label set.\n\n"
            "To help with this task, you can use the labeled train_samples/ directory containing multiple labeled examples per class while developing rule.py. "
            "The corresponding labels are available in train_labels.json.",
        )

    def tex_prompt_question(self, *, desc_level: str, task_type: str, samples: int) -> dict[str, Any]:
        has_examples = samples > 0
        source_prefix = "Context:\n" if task_type == "code" or desc_level == "none" else ""
        sample_folder = "test_samples/ and train_samples/" if has_examples else "test_samples/"
        if desc_level == "high":
            sample_source = f"{source_prefix}The time series in the {sample_folder} folder were generated"
            environment_description = (
                "by simulating a 1D vertical point-mass ball model under gravity.\n"
                "This detailed physics paragraph should not appear on the website."
            )
            label_space = (
                'Allowed labels:\n["coefficient of restitution", "mass", "drag coefficient", '
                '"gravity acceleration", "no parameter change"]'
            )
            no_change = 'Use "no parameter change" if there is no evidence in the data of a parameter change during the observed interval.'
        else:
            sample_source = (
                f"{source_prefix}The time series in the {sample_folder} folder were generated by a simulator of an unknown physical phenomenon."
            )
            environment_description = "The column meanings are unknown, except for the last column, which represents time."
            label_space = '["label_0", "label_1", "label_2", "label_3"] denote different parameter changes, while "label_4" denotes that no parameter changed.'
            no_change = ""
        if task_type == "code":
            task_artifact = (
                "Task:\nCreate a Python script named rule.py in the current working directory.\n"
                "The script must define exactly this function:\ndef predict(df) -> list[str]:"
            )
            prediction_format = (
                "The input df is a pandas DataFrame containing one sample with columns col1, col2, col3, and col4.\n"
                "The function will be called on samples inside test_samples/ and on additional held-out samples with the same schema and label set.\n"
                "For each dataframe, predict(df) must return a ranked list of labels. The first label is the final top-1 prediction and should be the single label most likely to be correct. Additional labels are optional lower-confidence alternatives and should only be included when the evidence is genuinely ambiguous."
            )
        else:
            task_artifact = "Task:\nCreate a file named results.json in the current working directory."
            prediction_format = (
                "For each file in test_samples/, return a ranked list of labels.\n"
                "The first label is your final top-1 prediction and should be the single label you think is most likely correct.\n"
                "You may include additional labels only when the evidence is genuinely ambiguous.\n"
                "Additional labels are treated as lower-confidence alternatives."
            )
        if desc_level == "high":
            intervention_semantics = (
                "For each simulation, either no parameter changes occur, or exactly one parameter among the allowed labels changes during the observed simulation interval.\n"
                "If a parameter changes, it undergoes a single instantaneous step change at an unknown time during the observed interval."
            )
        else:
            intervention_semantics = (
                "For each simulation, either no parameter changes occur, or exactly one parameter corresponding to one of the allowed changes during the observed simulation interval.\n"
                "If a parameter changes, it undergoes a single instantaneous step change at an unknown time during the observed interval."
            )
        return {
            "recipe_info": {
                "desc_level": desc_level,
                "type_of_request": task_type,
                "number_train_samples_per_class": samples,
            },
            "question_text": {
                "sample_source": sample_source,
                "environment_description": environment_description,
                "observed_columns": "Observed Signals:\ncol1: ball height",
                "intervention_semantics": intervention_semantics,
                "label_space": label_space,
                "no_change_guidance": no_change,
                "task_artifact": task_artifact,
                "prediction_format": prediction_format,
                "mode_specific_requirements": "Requirements:\n- Full agent instruction.",
                "evaluation": "Evaluation:\nTop-1 accuracy.",
                "runtime_constraints": "Additional requirements:\n- Internet access is disabled.",
            },
        }

    def test_website_prompt_matches_tex_compact_shape(self) -> None:
        combinations = []
        for desc_level in ("high", "none"):
            for task_type in ("direct", "code"):
                for samples in (0, 1, 3):
                    training_samples = sync_hf_to_website_data.sample_bucket(samples)
                    question = self.tex_prompt_question(desc_level=desc_level, task_type=task_type, samples=samples)
                    combinations.append(
                        {
                            "key": (desc_level, task_type, training_samples),
                            "prompt": sync_hf_to_website_data.render_website_prompt(
                                question["question_text"],
                                question_slug=f"{desc_level}-{task_type}-{samples}",
                                questions_by_id={},
                                desc_level=desc_level,
                                task_type=task_type,
                                training_samples=training_samples,
                            ),
                        }
                    )

        by_key = {item["key"]: item["prompt"] for item in combinations}
        high_direct = by_key[("high", "direct", "none")]
        self.assertTrue(
            high_direct.startswith(
                "The time series in the test_samples/ folder were generated by simulating a 1D vertical point-mass ball model under gravity."
            )
        )
        self.assertIn("Task:\nCreate a file named results.json", high_direct)
        self.assertIn('"<filename_1>": "<correct_label_filename_1>"', high_direct)
        self.assertIn("For each file in test_samples/, return the correct label.", high_direct)
        self.assertNotIn("ranked list", high_direct)
        self.assertNotIn("<top_1_label>", high_direct)
        self.assertNotIn("Observed Signals:", high_direct)
        self.assertNotIn("Requirements:", high_direct)
        self.assertNotIn("Evaluation:", high_direct)
        self.assertNotIn("Internet access is disabled", high_direct)

        high_code = by_key[("high", "code", "none")]
        self.assertTrue(high_code.startswith("Context:\nThe time series in the test_samples/ folder were generated by simulating"))
        self.assertIn("Create a Python script named rule.py", high_code)
        self.assertIn("def predict(df) -> str:", high_code)
        self.assertIn("The input df is a pandas DataFrame containing one sample with one column, 'ball height'.", high_code)
        self.assertNotIn("The function must return the correct label as a string.", high_code)
        self.assertNotIn("def predict(df) -> list[str]:", high_code)
        self.assertNotIn("For each dataframe, predict(df)", high_code)
        self.assertNotIn("columns col1, col2, col3, and col4", high_code)
        self.assertNotIn("This detailed physics paragraph", high_code)

        none_code = by_key[("none", "code", "none")]
        self.assertIn("The input df is a pandas DataFrame containing one sample with one column, col1.", none_code)
        self.assertNotIn("'ball height'", none_code)
        self.assertNotIn("columns col1, col2, col3, and col4", none_code)

        none_direct = by_key[("none", "direct", "none")]
        self.assertTrue(none_direct.startswith("Context:\nThe time series in the test_samples/ folder were generated by a simulator of an unknown physical phenomenon."))
        self.assertIn('["label_0", "label_1", "label_2", "label_3"]', none_direct)

        self.assertIn(
            "containing one labeled example per class. The corresponding labels are available in train_labels.json.",
            by_key[("high", "direct", "one")],
        )
        self.assertIn(
            "containing multiple labeled examples per class while developing rule.py. The corresponding labels are available in train_labels.json.",
            by_key[("none", "code", "multiple")],
        )
        self.assertNotIn("For each dataframe, predict(df)", by_key[("high", "code", "one")])
        self.assertNotIn("For each dataframe, predict(df)", by_key[("none", "code", "one")])
        self.assertNotIn("For each dataframe, predict(df)", by_key[("high", "code", "multiple")])
        self.assertNotIn("For each dataframe, predict(df)", by_key[("none", "code", "multiple")])
        for prompt in by_key.values():
            self.assertNotIn("lower-confidence", prompt)
            self.assertNotIn("<optional_lower_confidence_label>", prompt)

    def test_missing_tracebench_checkout_uses_local_documented_renderer(self) -> None:
        original_candidate_roots = sync_hf_to_website_data.candidate_tracebench_roots
        sync_hf_to_website_data.candidate_tracebench_roots = lambda explicit_root: [Path("/does/not/exist")]
        try:
            renderer = sync_hf_to_website_data.load_prompt_renderer(None)
        finally:
            sync_hf_to_website_data.candidate_tracebench_roots = original_candidate_roots

        rendered = renderer(
            {
                "sample_source": "The time series were generated",
                "environment_description": "by simulating a test system.",
                "observed_columns": "",
                "ordered_field_agent_prompt": [
                    ["sample_source", " "],
                    ["environment_description", "\n\n"],
                    ["observed_columns", ""],
                ],
            }
        )

        self.assertEqual(rendered, "The time series were generated by simulating a test system.")

    def test_explicit_tracebench_root_imports_shared_prompt_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracebench_root = Path(tmp)
            shared_dir = tracebench_root / "shared"
            shared_dir.mkdir()
            (shared_dir / "__init__.py").write_text("", encoding="utf-8")
            (shared_dir / "prompts.py").write_text(
                "\n".join(
                    [
                        "def render_tsenv_agent_prompt(question_text, **kwargs):",
                        "    return 'external::' + question_text['sample_source']",
                    ]
                ),
                encoding="utf-8",
            )

            previous_shared = sys.modules.pop("shared", None)
            previous_prompts = sys.modules.pop("shared.prompts", None)
            try:
                renderer = sync_hf_to_website_data.load_prompt_renderer(tracebench_root)
            finally:
                sys.modules.pop("shared", None)
                sys.modules.pop("shared.prompts", None)
                if str(tracebench_root) in sys.path:
                    sys.path.remove(str(tracebench_root))
                if previous_shared is not None:
                    sys.modules["shared"] = previous_shared
                if previous_prompts is not None:
                    sys.modules["shared.prompts"] = previous_prompts

        self.assertEqual(renderer({"sample_source": "prompt source"}), "external::prompt source")

    def test_sync_overwrites_stale_hf_prompt_combinations_from_questions(self) -> None:
        def fake_download_file(url: str, destination: Path) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if "website/summary.json" in url:
                destination.write_text(json.dumps({"simulators": ["BallDrop"]}), encoding="utf-8")
            elif "website/leaderboard.json" in url:
                destination.write_text(json.dumps({"rows": [], "filters": {}}), encoding="utf-8")
            elif "website/environments/BallDrop/description.json" in url:
                destination.write_text(
                    json.dumps(
                        {
                            "name": "BallDrop",
                            "short_one_line_description": "short",
                            "prompt_combinations": [
                                {
                                    "desc_level": "high",
                                    "task_type": "direct",
                                    "training_samples": "none",
                                    "agent_instruction": "stale deployed prompt",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
            elif "questions/BallDrop/questions.json" in url:
                self.write_questions(destination)
            elif "website/environments/BallDrop/data_" in url:
                destination.write_text(json.dumps({"rows": [], "intervention_time": 0.0}), encoding="utf-8")
            elif "website/submissions_manifest.txt" in url:
                destination.write_text("", encoding="utf-8")
            else:
                self.fail(f"unexpected download URL: {url}")

        original_simulators = sync_hf_to_website_data.SIMULATORS
        original_download_file = sync_hf_to_website_data.download_file
        original_load_prompt_renderer = sync_hf_to_website_data.load_prompt_renderer
        sync_hf_to_website_data.SIMULATORS = ("BallDrop",)
        sync_hf_to_website_data.download_file = fake_download_file
        sync_hf_to_website_data.load_prompt_renderer = lambda tracebench_root: (
            lambda question_text, **kwargs: f"full::{kwargs['question_slug']}::{question_text['sample_source']}"
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_dir = Path(tmp) / "data"
                sync_hf_to_website_data.sync_from_hf("repo", "main", output_dir, None)
                description = json.loads(
                    (output_dir / "environments" / "BallDrop" / "description.json").read_text(encoding="utf-8")
                )
                homepage_data = json.loads(
                    (output_dir / "environments" / "data_main_page.json").read_text(encoding="utf-8")
                )
        finally:
            sync_hf_to_website_data.SIMULATORS = original_simulators
            sync_hf_to_website_data.download_file = original_download_file
            sync_hf_to_website_data.load_prompt_renderer = original_load_prompt_renderer

        self.assertEqual(homepage_data["source"], "programmatic:ball-drop-bounce-gif")
        self.assertTrue(homepage_data["rows"])
        combinations = description["prompt_combinations"]
        self.assertEqual(len(combinations), 12)
        self.assertNotEqual(combinations[0]["agent_instruction"], "stale deployed prompt")
        self.assertEqual(
            combinations[0]["agent_instruction"],
            "full::high-direct-0::prompt source high-direct-0",
        )
        homepage_combinations = description["homepage_prompt_combinations"]
        self.assertEqual(len(homepage_combinations), 12)
        self.assertIn("prompt source high-direct-0", homepage_combinations[0]["agent_instruction"])
        self.assertIn("For each file in test_samples/, return the correct label.", homepage_combinations[0]["agent_instruction"])

    def test_positive_prompt_bucket_falls_back_when_exact_count_missing(self) -> None:
        questions = {
            "high-code-0": self.question(desc_level="high", task_type="code", samples=0),
            "high-code-3": self.question(desc_level="high", task_type="code", samples=3),
        }

        selected_one_slug, _ = sync_hf_to_website_data.select_question(
            questions,
            desc_level="high",
            task_type="code",
            training_samples="one",
        )
        selected_multiple_slug, _ = sync_hf_to_website_data.select_question(
            questions,
            desc_level="high",
            task_type="code",
            training_samples="multiple",
        )

        self.assertEqual(selected_one_slug, "high-code-3")
        self.assertEqual(selected_multiple_slug, "high-code-3")

    def test_validator_rejects_missing_prompt_combination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "summary.json").write_text(
                json.dumps(
                    {
                        "simulators": ["BallDrop", "BounceBall", "MassSlide"],
                        "canonical_scope": {
                            "task_mode": "Code",
                            "noise": "Low",
                            "context": "High",
                            "examples": "Three Examples",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "leaderboard.json").write_text(json.dumps({"rows": [], "filters": {}}), encoding="utf-8")
            (root / "environments").mkdir()
            sync_hf_to_website_data.write_homepage_data(root / "environments")
            for simulator in ("BallDrop", "BounceBall", "MassSlide"):
                env_dir = root / "environments" / simulator
                env_dir.mkdir(parents=True)
                (env_dir / "description.json").write_text(
                    json.dumps(
                        {
                            "name": simulator,
                            "short_one_line_description": "short",
                            "download_link": (
                                "https://huggingface.co/datasets/eth-siplab/tracebench/tree/main/"
                                f"questions/{simulator}"
                            ),
                            "prompt_combinations": [
                                {
                                    "desc_level": "high",
                                    "task_type": "direct",
                                    "training_samples": "none",
                                    "agent_instruction": "rendered prompt",
                                }
                            ],
                            "sample_count": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                for sample_number in range(1, 6):
                    (env_dir / f"data_{sample_number}.json").write_text(
                        json.dumps({"rows": [{"time": 0.0}], "intervention_time": 0.0}),
                        encoding="utf-8",
                    )

            with self.assertRaises(validate_website_data.ValidationError):
                validate_website_data.validate(root)

    def test_validator_rejects_missing_homepage_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "summary.json").write_text(
                json.dumps(
                    {
                        "simulators": ["BallDrop", "BounceBall", "MassSlide"],
                        "canonical_scope": {
                            "task_mode": "Code",
                            "noise": "Low",
                            "context": "High",
                            "examples": "Three Examples",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "leaderboard.json").write_text(json.dumps({"rows": [], "filters": {}}), encoding="utf-8")
            (root / "environments").mkdir()

            with self.assertRaisesRegex(validate_website_data.ValidationError, "data_main_page.json"):
                validate_website_data.validate(root)

    def test_validator_rejects_stale_deployed_prompt_prefix(self) -> None:
        stale_prompt = (
            "You are given multivariate time-series observations from BallDrop.\n\n"
            "Identify which physical parameter changed, or answer that no intervention occurred."
        )
        with self.assertRaises(validate_website_data.ValidationError):
            validate_website_data.validate_prompt_combinations(
                [
                    {
                        "desc_level": desc_level,
                        "task_type": task_type,
                        "training_samples": training_samples,
                        "agent_instruction": stale_prompt if index == 0 else "rendered prompt",
                    }
                    for index, (desc_level, task_type, training_samples) in enumerate(
                        sorted(validate_website_data.EXPECTED_PROMPT_COMBINATIONS)
                    )
                ],
                "BallDrop",
            )

    def test_validator_allows_full_environment_prompts_but_rejects_full_homepage_prompts(self) -> None:
        combinations = [
            {
                "desc_level": desc_level,
                "task_type": task_type,
                "training_samples": training_samples,
                "agent_instruction": "Prompt body\n\nObserved Signals:\ncol1: height\n\nRequirements:\n- answer carefully",
            }
            for desc_level, task_type, training_samples in sorted(validate_website_data.EXPECTED_PROMPT_COMBINATIONS)
        ]

        validate_website_data.validate_prompt_combinations(combinations, "BallDrop")
        with self.assertRaises(validate_website_data.ValidationError):
            validate_website_data.validate_homepage_prompt_combinations(combinations, "BallDrop")


if __name__ == "__main__":
    unittest.main()

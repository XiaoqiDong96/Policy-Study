import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "orchestrator"
    / "cloud_queue_runner.py"
)
SPEC = importlib.util.spec_from_file_location("cloud_queue_runner", RUNNER_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
QueueRunner = MODULE.QueueRunner


def manifest() -> dict:
    return {
        "schema_version": 1,
        "tasks": [
            {
                "task_id": "T01",
                "order": 1,
                "task_name": "synthetic worker",
                "worker_status": "ready",
                "dependencies": [],
                "commands": [["python3", "synthetic_worker.py"]],
                "gates": [{"type": "file_nonempty", "path": "result.txt"}],
            }
        ],
    }


class QueueRunnerTests(unittest.TestCase):
    def make_runner(self, root: Path) -> QueueRunner:
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest()), encoding="utf-8")
        return QueueRunner(root, path)

    def test_ready_worker_promotes_stale_awaiting_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = self.make_runner(Path(temporary))
            runner.state["tasks"]["T01"]["status"] = "AWAITING_WORKER"
            runner._save_state()
            self.assertEqual(runner.state["tasks"]["T01"]["status"], "PENDING")

    def test_csv_gate_requires_declared_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = self.make_runner(root)
            panel = root / "panel.csv"
            with panel.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["city_code", "year", "value"])
                writer.writeheader()
                writer.writerow({"city_code": "110000", "year": 2020, "value": 1})
            result = runner._evaluate_gate(
                {
                    "type": "csv_shape",
                    "path": "panel.csv",
                    "rows": 1,
                    "unique_key": ["city_code", "year"],
                    "required_columns": ["city_code", "year", "network_metric"],
                }
            )
            self.assertFalse(result["passed"])
            self.assertIn("network_metric", result["detail"])

    def test_manifest_rejects_runnable_task_without_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = manifest()
            value["tasks"][0]["gates"] = []
            path = root / "manifest.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "lacks output gates"):
                QueueRunner(root, path)


if __name__ == "__main__":
    unittest.main()

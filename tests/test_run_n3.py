import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.automation.audit_n3 import OFFICIAL_BLOCK_A_BATCH, OFFICIAL_BLOCK_B_BATCH
from src.automation.locking import N3_TOKEN_ENV, assert_n3_access
from src.automation.run_n3 import N3Error, N3Executor, N3Lock, build_parser, mode_from_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ExecutorInterfaceTests(unittest.TestCase):
    def test_default_mode_is_verify_existing(self):
        arguments = build_parser().parse_args([])
        self.assertEqual(mode_from_arguments(arguments), "verify-existing")
        self.assertEqual(arguments.block_a_batch, OFFICIAL_BLOCK_A_BATCH)
        self.assertEqual(arguments.block_b_batch, OFFICIAL_BLOCK_B_BATCH)

    def test_explicit_verify_mode(self):
        arguments = build_parser().parse_args(["--verify-existing"])
        self.assertEqual(mode_from_arguments(arguments), "verify-existing")

    def test_execute_requires_explicit_flag(self):
        self.assertEqual(mode_from_arguments(build_parser().parse_args(["--execute"])), "execute")
        self.assertNotEqual(mode_from_arguments(build_parser().parse_args([])), "execute")

    def test_explicit_batch_selection(self):
        arguments = build_parser().parse_args(
            ["--block-a-batch", "chosen-a", "--block-b-batch", "chosen-b"]
        )
        self.assertEqual((arguments.block_a_batch, arguments.block_b_batch), ("chosen-a", "chosen-b"))

    def test_modes_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--verify-existing", "--execute"])


class LockingTests(unittest.TestCase):
    def test_prevents_two_integrated_executions(self):
        with tempfile.TemporaryDirectory() as directory:
            first = N3Lock(Path(directory))
            second = N3Lock(Path(directory))
            first.acquire()
            try:
                with self.assertRaisesRegex(N3Error, "outra execução"):
                    second.acquire()
            finally:
                first.release()

    def test_integrated_lock_refuses_active_block_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".block_a.lock").write_text("active", encoding="utf-8")
            lock = N3Lock(root)
            with self.assertRaisesRegex(N3Error, "runner individual"):
                lock.acquire()
            self.assertFalse((root / ".n3.lock").exists())

    def test_individual_runner_requires_matching_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".n3.lock").write_text(json.dumps({"token": "secret"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "integrada"):
                    assert_n3_access(root, RuntimeError)
            with mock.patch.dict(os.environ, {N3_TOKEN_ENV: "secret"}, clear=True):
                assert_n3_access(root, RuntimeError)


class ExecutorFlowTests(unittest.TestCase):
    def make_executor(self, root: Path, mode: str = "execute") -> N3Executor:
        return N3Executor(
            root=root,
            mode=mode,
            block_a_batch=OFFICIAL_BLOCK_A_BATCH,
            block_b_batch=OFFICIAL_BLOCK_B_BATCH,
            integration_id="test-integration",
        )

    def test_verify_mode_does_not_call_docker_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = self.make_executor(Path(directory), "verify-existing")
            expected = Path(directory) / "output"
            executor.consolidate = mock.Mock(return_value=expected)
            executor.run_process = mock.Mock(side_effect=AssertionError("não deveria executar"))
            self.assertEqual(executor.run(), expected)
            executor.run_process.assert_not_called()

    def test_execute_orchestrates_a_then_b_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = self.make_executor(Path(directory))
            events = []
            executor.validate_execute_environment = mock.Mock(side_effect=lambda: events.append("validate") or "ok")
            executor.reset_profiles = mock.Mock(side_effect=lambda: events.append("reset") or "clean")
            executor.run_block = mock.Mock(
                side_effect=lambda block: (events.append(block) or (f"new-{block.lower()}", "log"))
            )
            executor.consolidate = mock.Mock(side_effect=lambda a, b: events.append((a, b)) or Path(directory) / "done")
            with mock.patch("src.automation.run_n3.audit_block_a", side_effect=lambda *_: events.append("audit-a")), mock.patch(
                "src.automation.run_n3.audit_block_b", side_effect=lambda *_: events.append("audit-b")
            ):
                executor.execute()
            self.assertEqual(
                events,
                ["validate", "reset", "A", "audit-a", "reset", "B", "audit-b", "reset", ("new-a", "new-b")],
            )

    def test_runner_failure_triggers_cleanup_and_failure_record(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = self.make_executor(Path(directory))
            executor.validate_execute_environment = mock.Mock(return_value="ok")
            executor.reset_profiles = mock.Mock(return_value="clean")
            executor.run_block = mock.Mock(side_effect=N3Error("runner failed"))
            with self.assertRaisesRegex(N3Error, "runner failed"):
                executor.run()
            self.assertGreaterEqual(executor.reset_profiles.call_count, 2)
            failure = Path(directory) / "results" / "integrated" / "failed" / "test-integration.json"
            self.assertTrue(failure.is_file())
            self.assertFalse((Path(directory) / "results" / ".n3.lock").exists())

    def test_cleanup_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = self.make_executor(Path(directory))
            executor.validate_execute_environment = mock.Mock(return_value="ok")
            executor.reset_profiles = mock.Mock(side_effect=N3Error("cleanup failed"))
            with self.assertRaisesRegex(N3Error, "cleanup failed"):
                executor.run()
            self.assertFalse((Path(directory) / "results" / ".n3.lock").exists())

    def test_interruption_triggers_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = self.make_executor(Path(directory))
            executor.validate_execute_environment = mock.Mock(return_value="ok")
            executor.reset_profiles = mock.Mock(return_value="clean")
            executor.run_block = mock.Mock(side_effect=KeyboardInterrupt("stop"))
            with self.assertRaises(KeyboardInterrupt):
                executor.run()
            self.assertGreaterEqual(executor.reset_profiles.call_count, 2)
            self.assertFalse((Path(directory) / "results" / ".n3.lock").exists())

    def test_offline_audit_preserves_historical_hashes(self):
        paths = [
            PROJECT_ROOT / "results" / "processed" / "block_a_measurements.csv",
            PROJECT_ROOT / "results" / "processed" / "block_b_measurements.csv",
        ]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        from src.automation.audit_n3 import audit_batches

        audit_batches(PROJECT_ROOT, OFFICIAL_BLOCK_A_BATCH, OFFICIAL_BLOCK_B_BATCH)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

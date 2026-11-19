import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.automation import audit_n3


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class IntegratedAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = audit_n3.audit_batches(
            PROJECT_ROOT,
            audit_n3.OFFICIAL_BLOCK_A_BATCH,
            audit_n3.OFFICIAL_BLOCK_B_BATCH,
        )

    def test_recognizes_exactly_six_experiments(self):
        self.assertEqual({row["experiment_id"] for row in self.result.rows}, {f"E{i:02d}" for i in range(1, 7)})

    def test_validates_15_block_a_results(self):
        self.assertEqual(len(self.result.block_a.integrated_rows), 15)
        self.assertEqual({row["algorithm"] for row in self.result.block_a.integrated_rows}, {"cubic", "bbr"})

    def test_validates_15_block_b_results(self):
        self.assertEqual(len(self.result.block_b.integrated_rows), 15)
        e06 = [row for row in self.result.block_b.integrated_rows if row["experiment_id"] == "E06"]
        self.assertTrue(all(row["session_resumed"] == "confirmed" for row in e06))
        self.assertTrue(all(row["early_data_status"] == "confirmed" for row in e06))

    def test_global_rows_are_30_and_unique(self):
        keys = {(row["batch_id"], row["experiment_id"], row["repetition"]) for row in self.result.rows}
        self.assertEqual(len(self.result.rows), 30)
        self.assertEqual(len(keys), 30)

    def test_metric_units_are_not_mixed(self):
        self.assertEqual({row["metric_unit"] for row in self.result.block_a.integrated_rows}, {"Mbps"})
        self.assertEqual({row["metric_unit"] for row in self.result.block_b.integrated_rows}, {"ms"})

    def test_recalculated_means(self):
        means = {item["experiment_id"]: item["mean"] for item in self.result.summary["experiments"]}
        self.assertAlmostEqual(means["E01"], 24685.56335820445)
        self.assertAlmostEqual(means["E06"], 56.6664)

    def test_recalculated_sample_stdev(self):
        stdevs = {item["experiment_id"]: item["sample_stdev"] for item in self.result.summary["experiments"]}
        self.assertAlmostEqual(stdevs["E02"], 0.6387006547109926)
        self.assertAlmostEqual(stdevs["E05"], 1.3736981837361546)

    def test_retransmission_totals(self):
        totals = {item["experiment_id"]: item["total_retransmissions"] for item in self.result.block_a.summaries}
        self.assertEqual(totals, {"E01": 0, "E02": 400, "E03": 37832})

    def test_detects_incomplete_batch(self):
        rows = [
            {"batch_id": "x", "experiment_id": "E01", "repetition": value}
            for value in range(1, 5)
        ]
        with self.assertRaisesRegex(audit_n3.AuditError, "esperado"):
            audit_n3.select_batch_rows(rows, "x", ["E01"])

    def test_detects_duplicate_sample(self):
        rows = [
            {"batch_id": "x", "experiment_id": "E01", "repetition": value}
            for value in range(1, 6)
        ]
        rows[-1] = dict(rows[0])
        with self.assertRaisesRegex(audit_n3.AuditError, "duplicada"):
            audit_n3.select_batch_rows(rows, "x", ["E01"])

    def test_detects_divergent_reference_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.txt").write_text("real", encoding="utf-8")
            self.assertEqual(audit_n3.verify_reference_hashes(root, {"value.txt": "0" * 64}), ["value.txt"])

    def test_integrated_csv_schema_and_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.csv"
            audit_n3.write_csv(path, "integration-test", self.result.rows)
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, audit_n3.INTEGRATED_FIELDS)
            self.assertEqual(len(rows), 30)
            self.assertTrue(all(row["integration_id"] == "integration-test" for row in rows))

    def test_manifest_generation_is_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("immutable", encoding="utf-8")
            for block, batch in (("block_a", "a"), ("block_b", "b")):
                path = root / "results" / "raw" / block / "batches"
                path.mkdir(parents=True, exist_ok=True)
                (path / f"{batch}.manifest.json").write_text(
                    json.dumps({"automation_sha256": f"hash-{block}"}), encoding="utf-8"
                )
            block_a = audit_n3.BlockAudit("A", "a", [], [], [], {}, {source}, [])
            block_b = audit_n3.BlockAudit("B", "b", [], [], [], {}, {source}, [])
            mini = audit_n3.IntegratedAudit(
                block_a,
                block_b,
                [],
                {"block_a_batch_id": "a", "block_b_batch_id": "b", "experiments": []},
                {source},
                {"source.txt": audit_n3.sha256_file(source)},
                {"status": "passed", "valid_results": 30},
            )
            destination = audit_n3.write_integration(
                root, mini, "integration-one", "verify-existing", "version", "commit"
            )
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["valid_measurements"], 30)
            self.assertEqual(set(manifest["outputs"]), {"measurements", "summary", "audit"})


if __name__ == "__main__":
    unittest.main()

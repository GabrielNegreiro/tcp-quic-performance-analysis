import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.automation.block_a import (
    BlockAError,
    CSV_FIELDS,
    EXPERIMENTS,
    ensure_paths_absent,
    experiment_for_id,
    load_json_file,
    merge_measurements_csv,
    parse_iperf_document,
    parse_socket_evidence,
    validate_qdisc_evidence,
)


FIXTURE = Path(__file__).parent / "fixtures" / "iperf3_success.json"


class BlockAParserTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_experiment_mapping(self):
        self.assertEqual(experiment_for_id("E01").algorithm, "cubic")
        self.assertEqual(experiment_for_id("E01").profile, "lan")
        self.assertEqual(experiment_for_id("E02").algorithm, "cubic")
        self.assertEqual(experiment_for_id("E02").profile, "wan-degraded")
        self.assertEqual(experiment_for_id("E03").algorithm, "bbr")
        self.assertEqual(experiment_for_id("E03").profile, "wan-degraded")
        with self.assertRaises(BlockAError):
            experiment_for_id("E04")

    def test_extracts_throughput_retransmissions_and_duration(self):
        metrics = parse_iperf_document(self.document)
        self.assertEqual(metrics["throughput_sender_mbps"], 1000.0)
        self.assertEqual(metrics["throughput_receiver_mbps"], 999.0)
        self.assertEqual(metrics["retransmissions"], 42)
        self.assertAlmostEqual(metrics["duration_seconds"], 15.0002)

    def test_rejects_incomplete_execution(self):
        incomplete = {"start": self.document["start"], "end": {}}
        with self.assertRaisesRegex(BlockAError, "incompleta"):
            parse_iperf_document(incomplete)

    def test_invalid_json_is_detected_by_standard_parser(self):
        with self.assertRaises(json.JSONDecodeError):
            json.loads("{invalid")

    def test_invalid_json_file_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(BlockAError, "JSON inválido"):
                load_json_file(path)

    def test_wrong_duration_is_rejected(self):
        self.document["start"]["test_start"]["duration"] = 10
        with self.assertRaisesRegex(BlockAError, "duração solicitada"):
            parse_iperf_document(self.document)

    def test_missing_retransmissions_is_rejected(self):
        del self.document["end"]["sum_sent"]["retransmits"]
        with self.assertRaisesRegex(BlockAError, "retransmits"):
            parse_iperf_document(self.document)

    def test_algorithm_confirmation_uses_data_socket(self):
        evidence = """ESTAB 0 0 172.20.0.50:40000 172.20.0.10:5201 users:((\"iperf3\",pid=1,fd=3))
 cubic bytes_sent:185 bytes_acked:186
ESTAB 0 1 172.20.0.50:40002 172.20.0.10:5201 users:((\"iperf3\",pid=1,fd=5))
 bbr bytes_sent:50000000 bytes_acked:49900000 bbr:(bw:1Gbps)
"""
        socket = parse_socket_evidence(evidence, "bbr")
        self.assertEqual(socket["algorithm"], "bbr")
        with self.assertRaisesRegex(BlockAError, "não corresponde"):
            parse_socket_evidence(evidence, "cubic")

    def test_qdisc_profiles(self):
        lan = """=== client-bench ===
active_profile=lan
qdisc netem 1a2b: root delay 500us
=== server-tcp ===
active_profile=lan
qdisc netem 1a2b: root delay 500us
"""
        validate_qdisc_evidence(lan, EXPERIMENTS["E01"])
        wan = """=== client-bench ===
active_profile=wan-degraded
qdisc netem 1a2b: root delay 25ms loss 2%
=== server-tcp ===
active_profile=wan-degraded
qdisc netem 1a2b: root delay 25ms
"""
        validate_qdisc_evidence(wan, EXPERIMENTS["E02"])
        validate_qdisc_evidence(wan, EXPERIMENTS["E03"])

    def test_wrong_qdisc_profile_is_rejected(self):
        invalid = """=== client-bench ===
active_profile=wan-degraded
qdisc netem 1a2b: root delay 25ms
=== server-tcp ===
active_profile=wan-degraded
qdisc netem 1a2b: root delay 25ms
"""
        with self.assertRaisesRegex(BlockAError, "perda de 2%"):
            validate_qdisc_evidence(invalid, EXPERIMENTS["E02"])

    def test_existing_raw_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run_01.json"
            path.write_text("original", encoding="utf-8")
            with self.assertRaisesRegex(BlockAError, "sobrescrever"):
                ensure_paths_absent([path])
            self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_csv_preserves_existing_batches_and_rejects_duplicates(self):
        def row(batch, repetition):
            value = {field: "" for field in CSV_FIELDS}
            value.update(
                batch_id=batch,
                experiment_id="E01",
                repetition=repetition,
                status="valid",
            )
            return value

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.csv"
            merge_measurements_csv(path, [row("batch_old", 1)])
            merge_measurements_csv(path, [row("batch_new", 1)])
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(list(rows[0]), CSV_FIELDS)
            with self.assertRaisesRegex(BlockAError, "duplicada"):
                merge_measurements_csv(path, [row("batch_new", 1)])


if __name__ == "__main__":
    unittest.main()


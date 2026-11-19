import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.automation import block_b


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class BlockBParsingTests(unittest.TestCase):
    def test_experiment_mapping_e04_to_e06(self):
        self.assertEqual(set(block_b.EXPERIMENTS), {"E04", "E05", "E06"})
        self.assertEqual(block_b.experiment_for_id("e06").connection_mode, "fresh_quic_0rtt")

    def test_protocol_identification(self):
        h2 = block_b.parse_curl_document(fixture("curl_http2.json"), "HTTP/2")
        h3 = block_b.parse_curl_document(fixture("curl_http3.json"), "HTTP/3")
        self.assertEqual(h2["protocol_confirmed"], "HTTP/2")
        self.assertEqual(h3["protocol_confirmed"], "HTTP/3")

    def test_seconds_to_milliseconds(self):
        self.assertEqual(block_b.seconds_to_ms(0.1555, "test"), 155.5)

    def test_ttfb_extraction(self):
        metrics = block_b.parse_curl_document(fixture("curl_http3.json"), "HTTP/3")
        self.assertEqual(metrics["ttfb_ms"], 104.25)

    def test_rejects_non_200(self):
        document = fixture("curl_http2.json")
        document["response_code"] = 503
        with self.assertRaisesRegex(block_b.BlockBError, "código HTTP"):
            block_b.parse_curl_document(document, "HTTP/2")

    def test_rejects_wrong_object_size(self):
        document = fixture("curl_http3.json")
        document["size_download"] = 1000
        with self.assertRaisesRegex(block_b.BlockBError, "tamanho inesperado"):
            block_b.parse_curl_document(document, "HTTP/3")

    def test_detects_http2_fallback(self):
        with self.assertRaisesRegex(block_b.BlockBError, "fallback"):
            block_b.parse_curl_document(fixture("curl_http2.json"), "HTTP/3")

    def test_detects_connection_or_session_reuse_in_cold_sample(self):
        document = fixture("curl_http3.json")
        document["num_connects"] = 0
        with self.assertRaisesRegex(block_b.BlockBError, "nova conexão"):
            block_b.parse_curl_document(document, "HTTP/3")
        metrics = block_b.parse_curl_document(fixture("curl_http3.json"), "HTTP/3")
        with self.assertRaisesRegex(block_b.BlockBError, "reutilizou uma sessão"):
            block_b.validate_cold_evidence(metrics, "SSL reusing session", "not-used")

    def test_distinguishes_resumption_and_confirmed_0rtt(self):
        prep = block_b.parse_curl_document(fixture("curl_http3.json"), "HTTP/3")
        measured_document = fixture("curl_http3.json")
        measured_document["local_port"] = 42002
        measured_document["tls_earlydata"] = 84
        measured = block_b.parse_curl_document(measured_document, "HTTP/3")
        result = block_b.validate_e06_evidence(
            prep,
            measured,
            "SSL reusing session with ALPN 'h3'\nSSL session allows 4294967295 bytes of early data",
            "x-lab-quic-early-data: accepted\n",
            1948,
        )
        self.assertEqual(result, {"session_resumed": "confirmed", "early_data_status": "confirmed"})

    def test_rejects_unaccepted_early_data(self):
        prep = block_b.parse_curl_document(fixture("curl_http3.json"), "HTTP/3")
        measured = dict(prep, local_port=42002, tls_earlydata_bytes=84)
        with self.assertRaisesRegex(block_b.BlockBError, "Caddy não confirmou"):
            block_b.validate_e06_evidence(
                prep,
                measured,
                "SSL reusing session with ALPN 'h3'\nSSL session allows 100 bytes of early data",
                "x-lab-quic-early-data: not-used\n",
                100,
            )

    def test_rejects_incomplete_values(self):
        document = fixture("curl_http2.json")
        del document["time_starttransfer"]
        with self.assertRaisesRegex(block_b.BlockBError, "time_starttransfer"):
            block_b.parse_curl_document(document, "HTTP/2")

    def test_validates_wan_no_loss_qdisc(self):
        section = (
            "ownership=managed\nactive_profile=wan-no-loss\n"
            "qdisc netem 1a2b: root limit 1000 delay 25ms\n"
        )
        block_b.validate_qdisc_evidence(section + section)
        with self.assertRaises(block_b.BlockBError):
            block_b.validate_qdisc_evidence((section + section).replace("delay 25ms", "delay 20ms", 1))


class BlockBStorageTests(unittest.TestCase):
    def sample_row(self, batch: str = "batch", repetition: int = 1) -> dict:
        return {
            "batch_id": batch,
            "experiment_id": "E04",
            "repetition": repetition,
            "protocol_requested": "HTTP/2",
            "protocol_confirmed": "HTTP/2",
            "connection_mode": "fresh_tcp_tls",
            "status": "valid",
        }

    def test_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.json"
            path.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(block_b.BlockBError, "sobrescrever"):
                block_b.ensure_paths_absent([path])
            self.assertEqual(path.read_text(encoding="utf-8"), "preserve")

    def test_detects_duplicate_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.csv"
            row = self.sample_row()
            block_b.write_csv(path, [row])
            before = path.read_bytes()
            with self.assertRaisesRegex(block_b.BlockBError, "duplicada"):
                block_b.merge_measurements_csv(path, [row])
            self.assertEqual(path.read_bytes(), before)

    def test_generates_csv_with_required_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.csv"
            block_b.write_csv(path, [self.sample_row()])
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, block_b.CSV_FIELDS)
            self.assertEqual(rows[0]["experiment_id"], "E04")

    def test_cleanup_runs_after_failure(self):
        class FailingRunner(block_b.BlockBRunner):
            def __init__(self):
                super().__init__(["E04"], preflight_only=True)
                self.cleaned = False
                self.released = False

            def acquire_locks(self):
                pass

            def release_locks(self):
                self.released = True

            def preflight(self):
                raise block_b.BlockBError("falha simulada")

            def reset_profiles(self, *, check=True):
                self.cleaned = True
                return mock.Mock(returncode=0, stdout="clean")

        runner = FailingRunner()
        with self.assertRaisesRegex(block_b.BlockBError, "falha simulada"):
            runner.run()
        self.assertTrue(runner.cleaned)
        self.assertTrue(runner.released)


if __name__ == "__main__":
    unittest.main()

import hashlib
import unittest
from pathlib import Path

import numpy as np

from src.analysis import n3_analysis


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class N3StatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = n3_analysis.load_official_results(
            PROJECT_ROOT, n3_analysis.OFFICIAL_INTEGRATION_ID
        )
        cls.statistics = n3_analysis.calculate_statistics(PROJECT_ROOT, cls.loaded)

    def test_source_is_exactly_30_unique_samples(self):
        frame = self.loaded.measurements
        self.assertEqual(len(frame), 30)
        self.assertEqual(frame["experiment_id"].value_counts().to_dict(), {
            experiment_id: 5 for experiment_id in n3_analysis.EXPECTED_EXPERIMENTS
        })
        self.assertFalse(
            frame.duplicated(
                subset=["integration_id", "batch_id", "experiment_id", "repetition"]
            ).any()
        )

    def test_units_remain_separated_by_block(self):
        frame = self.loaded.measurements
        self.assertEqual(set(frame.loc[frame["block"] == "A", "metric_unit"]), {"Mbps"})
        self.assertEqual(set(frame.loc[frame["block"] == "B", "metric_unit"]), {"ms"})

    def test_official_means_and_sample_standard_deviations(self):
        indexed = self.statistics.set_index("experiment_id")
        expected = {
            "E01": (24685.56335820445, 67.53674485407781),
            "E02": (4.123983585811606, 0.6387006547109926),
            "E03": (291.0168291492672, 8.920271952034673),
            "E04": (158.9658, 0.33447526066960065),
            "E05": (109.4652, 1.3736981837361546),
            "E06": (56.6664, 0.5517619957916636),
        }
        for experiment_id, (mean, stdev) in expected.items():
            self.assertAlmostEqual(float(indexed.at[experiment_id, "mean"]), mean)
            self.assertAlmostEqual(float(indexed.at[experiment_id, "sample_stdev"]), stdev)

    def test_statistics_use_ddof_one_and_recompute_median_and_cv(self):
        indexed = self.statistics.set_index("experiment_id")
        for experiment_id in n3_analysis.EXPECTED_EXPERIMENTS:
            values = self.loaded.measurements.loc[
                self.loaded.measurements["experiment_id"] == experiment_id, "metric_value"
            ].astype(float)
            self.assertAlmostEqual(
                float(indexed.at[experiment_id, "sample_stdev"]),
                float(np.std(values.to_numpy(), ddof=1)),
            )
            self.assertAlmostEqual(
                float(indexed.at[experiment_id, "median"]), float(np.median(values.to_numpy()))
            )
            self.assertAlmostEqual(
                float(indexed.at[experiment_id, "coefficient_of_variation_percent"]),
                float(np.std(values.to_numpy(), ddof=1) / np.mean(values.to_numpy()) * 100),
            )

    def test_retransmission_totals_and_normalization(self):
        indexed = self.statistics.set_index("experiment_id")
        self.assertEqual(
            {key: int(indexed.at[key, "total_retransmissions"]) for key in ("E01", "E02", "E03")},
            {"E01": 0, "E02": 400, "E03": 37832},
        )
        self.assertEqual(float(indexed.at["E01", "retransmissions_per_gb"]), 0.0)
        self.assertGreater(float(indexed.at["E02", "retransmissions_per_gb"]), 0.0)
        self.assertGreater(float(indexed.at["E03", "retransmissions_per_gb"]), 0.0)
        self.assertEqual(indexed.at["E04", "retransmissions_per_gb"], "not_applicable")

    def test_comparisons_are_derived_from_unrounded_means(self):
        comparisons = n3_analysis.calculate_comparisons(self.statistics)
        self.assertAlmostEqual(
            comparisons["block_a"]["e02_vs_e03"]["mean_ratio_bbr_over_cubic"],
            70.56692227158675,
        )
        self.assertAlmostEqual(
            comparisons["block_b"]["e04_vs_e06"]["percent_reduction"],
            64.35308726782742,
        )

    def test_validation_rejects_duplicates_and_wrong_units(self):
        duplicate = self.loaded.measurements.copy().astype(str)
        duplicate.iloc[-1] = duplicate.iloc[-2]
        with self.assertRaisesRegex(n3_analysis.AnalysisError, "duplicadas"):
            n3_analysis.validate_measurements(duplicate, n3_analysis.OFFICIAL_INTEGRATION_ID)

        wrong_unit = self.loaded.measurements.copy().astype(str)
        wrong_unit.loc[wrong_unit["experiment_id"] == "E01", "metric_unit"] = "ms"
        with self.assertRaisesRegex(n3_analysis.AnalysisError, "unidade incorreta"):
            n3_analysis.validate_measurements(wrong_unit, n3_analysis.OFFICIAL_INTEGRATION_ID)

    def test_validation_rejects_missing_metric(self):
        missing = self.loaded.measurements.copy().astype(str)
        missing.loc[0, "metric_value"] = ""
        with self.assertRaisesRegex(n3_analysis.AnalysisError, "métrica principal"):
            n3_analysis.validate_measurements(missing, n3_analysis.OFFICIAL_INTEGRATION_ID)

    def test_statistics_are_reproducible(self):
        repeated = n3_analysis.calculate_statistics(PROJECT_ROOT, self.loaded)
        self.assertEqual(
            n3_analysis.statistics_records(repeated),
            n3_analysis.statistics_records(self.statistics),
        )

    def test_analysis_does_not_modify_integrated_inputs(self):
        paths = [
            self.loaded.integration_dir / filename
            for filename in ("manifest.json", "audit.json", "measurements.csv", "summary.json")
        ]
        before = {path: source_digest(path) for path in paths}
        n3_analysis.calculate_statistics(PROJECT_ROOT, self.loaded)
        after = {path: source_digest(path) for path in paths}
        self.assertEqual(after, before)

    def test_alternative_integration_uses_identified_output_names(self):
        paths = n3_analysis.output_paths(PROJECT_ROOT, "integration-alternative")
        self.assertEqual(paths.statistics_csv.name, "n3_statistics_integration-alternative.csv")
        self.assertEqual(
            paths.graph1_png.name, "graph_01_tcp_throughput_integration-alternative.png"
        )
        self.assertEqual(paths.analysis_manifest.parent.name, "analysis_integration-alternative")

    def test_completed_official_analysis_refuses_overwrite(self):
        with self.assertRaisesRegex(n3_analysis.AnalysisError, "recusa em sobrescrever"):
            n3_analysis.generate_analysis(PROJECT_ROOT, n3_analysis.OFFICIAL_INTEGRATION_ID)


if __name__ == "__main__":
    unittest.main()

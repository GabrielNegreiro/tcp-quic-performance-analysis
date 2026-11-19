import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.container import BarContainer, ErrorbarContainer
from PIL import Image

from src.analysis import n3_analysis


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class N3FigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = n3_analysis.load_official_results(
            PROJECT_ROOT, n3_analysis.OFFICIAL_INTEGRATION_ID
        )
        cls.statistics = n3_analysis.calculate_statistics(PROJECT_ROOT, loaded)

    def test_tcp_figure_has_both_scales_and_five_error_bars(self):
        figure, metadata = n3_analysis.create_tcp_figure(
            self.statistics, n3_analysis.OFFICIAL_INTEGRATION_ID
        )
        try:
            self.assertEqual(len(figure.axes), 2)
            self.assertEqual(figure.axes[0].get_yscale(), "log")
            self.assertEqual(figure.axes[1].get_yscale(), "linear")
            self.assertIn("Mbps", figure.axes[0].get_ylabel())
            self.assertIn("Mbps", figure.axes[1].get_ylabel())
            self.assertEqual(metadata["errorbar_containers"], 5)
            self.assertEqual(
                sum(
                    isinstance(container, ErrorbarContainer)
                    for axis in figure.axes
                    for container in axis.containers
                ),
                5,
            )
        finally:
            plt.close(figure)

    def test_http_figure_has_three_bars_error_bars_and_zero_origin(self):
        figure, metadata = n3_analysis.create_http_figure(
            self.statistics, n3_analysis.OFFICIAL_INTEGRATION_ID
        )
        try:
            axis = figure.axes[0]
            bar_containers = [item for item in axis.containers if isinstance(item, BarContainer)]
            error_containers = [item for item in axis.containers if isinstance(item, ErrorbarContainer)]
            self.assertEqual(sum(len(item) for item in bar_containers), 3)
            self.assertEqual(len(error_containers), 1)
            self.assertEqual(axis.get_ylim()[0], 0.0)
            self.assertEqual(axis.get_yscale(), "linear")
            self.assertIn("TTFB", axis.get_ylabel())
            self.assertEqual(metadata["bar_count"], 3)
            self.assertTrue(metadata["y_starts_at_zero"])
        finally:
            plt.close(figure)

    def test_png_exports_at_300_dpi_and_pdf_is_valid(self):
        figure, _ = n3_analysis.create_http_figure(
            self.statistics, n3_analysis.OFFICIAL_INTEGRATION_ID
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                png = Path(directory) / "figure.png"
                pdf = Path(directory) / "figure.pdf"
                figure.savefig(png, dpi=300, bbox_inches="tight")
                figure.savefig(pdf, bbox_inches="tight")
                with Image.open(png) as image:
                    self.assertGreaterEqual(image.width, 1500)
                    self.assertGreaterEqual(image.height, 900)
                    self.assertAlmostEqual(image.info["dpi"][0], 300, delta=1)
                    self.assertAlmostEqual(image.info["dpi"][1], 300, delta=1)
                self.assertEqual(pdf.read_bytes()[:4], b"%PDF")
        finally:
            plt.close(figure)


if __name__ == "__main__":
    unittest.main()

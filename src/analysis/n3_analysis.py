#!/usr/bin/env python3
"""Análise estatística e figuras científicas dos 30 resultados oficiais N3."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.container import ErrorbarContainer

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.automation.audit_n3 import INTEGRATED_FIELDS, sha256_file, verify_reference_hashes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_INTEGRATION_ID = "n3_verify_existing_20261119T005601Z_e6f56660"
EXPECTED_EXPERIMENTS = tuple(f"E{value:02d}" for value in range(1, 7))

EXPERIMENT_METADATA = {
    "E01": {
        "block": "A",
        "label": "E01\nCUBIC — LAN",
        "algorithm_protocol": "TCP CUBIC",
        "scenario": "LAN; RTT 1 ms; perda 0%",
        "metric_name": "throughput_sender",
        "metric_unit": "Mbps",
    },
    "E02": {
        "block": "A",
        "label": "E02\nCUBIC — WAN",
        "algorithm_protocol": "TCP CUBIC",
        "scenario": "WAN; RTT 50 ms; perda 2%",
        "metric_name": "throughput_sender",
        "metric_unit": "Mbps",
    },
    "E03": {
        "block": "A",
        "label": "E03\nBBR — WAN",
        "algorithm_protocol": "TCP BBR",
        "scenario": "WAN; RTT 50 ms; perda 2%",
        "metric_name": "throughput_sender",
        "metric_unit": "Mbps",
    },
    "E04": {
        "block": "B",
        "label": "E04\nHTTP/2",
        "algorithm_protocol": "HTTP/2 sobre TCP/TLS",
        "scenario": "WAN; RTT 50 ms; perda 0%",
        "metric_name": "ttfb",
        "metric_unit": "ms",
    },
    "E05": {
        "block": "B",
        "label": "E05\nHTTP/3 Cold",
        "algorithm_protocol": "HTTP/3 Cold",
        "scenario": "WAN; RTT 50 ms; perda 0%",
        "metric_name": "ttfb",
        "metric_unit": "ms",
    },
    "E06": {
        "block": "B",
        "label": "E06\nHTTP/3 0-RTT",
        "algorithm_protocol": "HTTP/3 Resumption + 0-RTT",
        "scenario": "WAN; RTT 50 ms; perda 0%",
        "metric_name": "ttfb",
        "metric_unit": "ms",
    },
}

STATISTICS_FIELDS = [
    "integration_id",
    "experiment_id",
    "block",
    "algorithm_protocol",
    "scenario",
    "metric_name",
    "metric_unit",
    "n",
    "mean",
    "sample_stdev",
    "minimum",
    "maximum",
    "median",
    "coefficient_of_variation_percent",
    "total_retransmissions",
    "total_bytes_sent",
    "retransmissions_per_gb",
]


class AnalysisError(RuntimeError):
    """Falha de integridade, estatística ou exportação da análise."""


@dataclass
class LoadedResults:
    integration_id: str
    integration_dir: Path
    manifest: dict[str, Any]
    audit: dict[str, Any]
    measurements: pd.DataFrame


@dataclass
class OutputPaths:
    statistics_csv: Path
    statistics_json: Path
    statistics_tex: Path
    comparisons_json: Path
    graph1_png: Path
    graph1_pdf: Path
    graph2_png: Path
    graph2_pdf: Path
    analysis_manifest: Path


def load_official_results(root: Path, integration_id: str) -> LoadedResults:
    integration_dir = (root / "results" / "integrated" / integration_id).resolve()
    required = {
        "manifest": integration_dir / "manifest.json",
        "audit": integration_dir / "audit.json",
        "measurements": integration_dir / "measurements.csv",
        "summary": integration_dir / "summary.json",
    }
    missing = [path for path in required.values() if not path.is_file()]
    if missing:
        raise AnalysisError(f"integração incompleta; arquivo ausente: {missing[0]}")
    try:
        manifest = json.loads(required["manifest"].read_text(encoding="utf-8"))
        audit = json.loads(required["audit"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"manifesto/auditoria inválido: {exc}") from exc
    if (
        manifest.get("integration_id") != integration_id
        or manifest.get("status") != "complete"
        or manifest.get("audit_status") != "passed"
        or int(manifest.get("valid_measurements", -1)) != 30
    ):
        raise AnalysisError("manifesto integrado não confirma 30 resultados válidos")
    if (
        audit.get("integration_id") != integration_id
        or audit.get("status") != "passed"
        or int(audit.get("valid_results", -1)) != 30
        or audit.get("missing_files")
        or audit.get("inconsistencies")
        or audit.get("hash_mismatches")
    ):
        raise AnalysisError("auditoria integrada não está integralmente aprovada")
    references = manifest.get("source_hashes_sha256")
    if not isinstance(references, dict) or not references:
        raise AnalysisError("manifesto integrado não possui hashes de origem")
    mismatches = verify_reference_hashes(root, references)
    if mismatches:
        raise AnalysisError(f"divergência SHA-256 no arquivo histórico: {mismatches[0]}")

    frame = pd.read_csv(required["measurements"], dtype=str, keep_default_na=False)
    if list(frame.columns) != INTEGRATED_FIELDS:
        raise AnalysisError("cabeçalho incompatível em measurements.csv")
    validate_measurements(frame, integration_id)
    frame = frame.copy()
    frame["repetition"] = pd.to_numeric(frame["repetition"], errors="raise").astype(int)
    frame["metric_value"] = pd.to_numeric(frame["metric_value"], errors="raise").astype(float)
    return LoadedResults(integration_id, integration_dir, manifest, audit, frame)


def validate_measurements(frame: pd.DataFrame, integration_id: str) -> None:
    if len(frame) != 30:
        raise AnalysisError(f"quantidade de amostras inválida: {len(frame)}")
    if set(frame["integration_id"]) != {integration_id}:
        raise AnalysisError("CSV mistura identificadores de integração")
    if set(frame["experiment_id"]) != set(EXPECTED_EXPERIMENTS):
        raise AnalysisError("CSV não contém exatamente E01–E06")
    if set(frame["status"]) != {"valid"}:
        raise AnalysisError("CSV contém registros não válidos")
    duplicate = frame.duplicated(
        subset=["integration_id", "batch_id", "experiment_id", "repetition"], keep=False
    )
    if duplicate.any():
        raise AnalysisError("CSV contém amostras duplicadas")
    counts = frame.groupby("experiment_id").size().to_dict()
    if counts != {experiment_id: 5 for experiment_id in EXPECTED_EXPERIMENTS}:
        raise AnalysisError(f"número de repetições incorreto: {counts}")
    numeric = pd.to_numeric(frame["metric_value"], errors="coerce")
    if numeric.isna().any() or not np.isfinite(numeric.to_numpy()).all() or (numeric <= 0).any():
        raise AnalysisError("métrica principal ausente, não finita ou não positiva")
    for experiment_id, metadata in EXPERIMENT_METADATA.items():
        selected = frame[frame["experiment_id"] == experiment_id]
        if set(selected["block"]) != {metadata["block"]}:
            raise AnalysisError(f"bloco incorreto em {experiment_id}")
        if set(selected["metric_name"]) != {metadata["metric_name"]}:
            raise AnalysisError(f"métrica incorreta em {experiment_id}")
        if set(selected["metric_unit"]) != {metadata["metric_unit"]}:
            raise AnalysisError(f"unidade incorreta em {experiment_id}")
        repetitions = {int(value) for value in selected["repetition"]}
        if repetitions != {1, 2, 3, 4, 5}:
            raise AnalysisError(f"repetições incorretas em {experiment_id}")


def _raw_bytes_sent(root: Path, source_file: str) -> int:
    path = (root / source_file).resolve()
    try:
        path.relative_to(root.resolve())
        document = json.loads(path.read_text(encoding="utf-8"))
        value = int(document["end"]["sum_sent"]["bytes"])
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"não foi possível extrair bytes transmitidos de {source_file}") from exc
    if value <= 0:
        raise AnalysisError(f"bytes transmitidos inválidos em {source_file}")
    return value


def calculate_statistics(root: Path, loaded: LoadedResults) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for experiment_id in EXPECTED_EXPERIMENTS:
        metadata = EXPERIMENT_METADATA[experiment_id]
        selected = loaded.measurements[loaded.measurements["experiment_id"] == experiment_id]
        values = selected["metric_value"].astype(float)
        mean = float(values.mean())
        sample_stdev = float(values.std(ddof=1))
        total_retransmissions: int | str = "not_applicable"
        total_bytes_sent: int | str = "not_applicable"
        retransmissions_per_gb: float | str = "not_applicable"
        if metadata["block"] == "A":
            retransmissions = pd.to_numeric(selected["retransmissions"], errors="raise").astype(int)
            total_retransmissions = int(retransmissions.sum())
            total_bytes_sent = sum(
                _raw_bytes_sent(root, source_file) for source_file in selected["source_file"]
            )
            retransmissions_per_gb = total_retransmissions / (total_bytes_sent / 1_000_000_000)
        records.append(
            {
                "integration_id": loaded.integration_id,
                "experiment_id": experiment_id,
                "block": metadata["block"],
                "algorithm_protocol": metadata["algorithm_protocol"],
                "scenario": metadata["scenario"],
                "metric_name": metadata["metric_name"],
                "metric_unit": metadata["metric_unit"],
                "n": int(values.count()),
                "mean": mean,
                "sample_stdev": sample_stdev,
                "minimum": float(values.min()),
                "maximum": float(values.max()),
                "median": float(values.median()),
                "coefficient_of_variation_percent": sample_stdev / mean * 100.0,
                "total_retransmissions": total_retransmissions,
                "total_bytes_sent": total_bytes_sent,
                "retransmissions_per_gb": retransmissions_per_gb,
            }
        )
    return pd.DataFrame(records, columns=STATISTICS_FIELDS)


def percentage_reduction(new_value: float, reference_value: float) -> float:
    if reference_value == 0:
        raise AnalysisError("referência zero em redução percentual")
    return (reference_value - new_value) / reference_value * 100.0


def calculate_comparisons(statistics_frame: pd.DataFrame) -> dict[str, Any]:
    indexed = statistics_frame.set_index("experiment_id")

    def mean(experiment_id: str) -> float:
        return float(indexed.at[experiment_id, "mean"])

    def retrans(experiment_id: str) -> int:
        return int(indexed.at[experiment_id, "total_retransmissions"])

    e01, e02, e03 = mean("E01"), mean("E02"), mean("E03")
    e04, e05, e06 = mean("E04"), mean("E05"), mean("E06")
    return {
        "block_a": {
            "e01_vs_e02": {
                "absolute_throughput_reduction_mbps": e01 - e02,
                "percent_reduction": percentage_reduction(e02, e01),
                "mean_ratio_e01_over_e02": e01 / e02,
                "retransmission_difference_e02_minus_e01": retrans("E02") - retrans("E01"),
            },
            "e02_vs_e03": {
                "absolute_throughput_gain_mbps": e03 - e02,
                "percent_gain_bbr_over_cubic": (e03 - e02) / e02 * 100.0,
                "mean_ratio_bbr_over_cubic": e03 / e02,
                "retransmission_difference_e03_minus_e02": retrans("E03") - retrans("E02"),
                "cv_e02_percent": float(indexed.at["E02", "coefficient_of_variation_percent"]),
                "cv_e03_percent": float(indexed.at["E03", "coefficient_of_variation_percent"]),
                "retransmissions_per_gb_e02": float(indexed.at["E02", "retransmissions_per_gb"]),
                "retransmissions_per_gb_e03": float(indexed.at["E03", "retransmissions_per_gb"]),
            },
        },
        "block_b": {
            "e04_vs_e05": {
                "absolute_ttfb_reduction_ms": e04 - e05,
                "percent_reduction": percentage_reduction(e05, e04),
                "mean_ratio_e04_over_e05": e04 / e05,
            },
            "e04_vs_e06": {
                "absolute_ttfb_reduction_ms": e04 - e06,
                "percent_reduction": percentage_reduction(e06, e04),
                "mean_ratio_e04_over_e06": e04 / e06,
            },
            "e05_vs_e06": {
                "absolute_ttfb_reduction_ms": e05 - e06,
                "percent_reduction": percentage_reduction(e06, e05),
                "mean_ratio_e05_over_e06": e05 / e06,
            },
        },
    }


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "figure.dpi": 120,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def create_tcp_figure(statistics_frame: pd.DataFrame, integration_id: str):
    configure_style()
    selected = statistics_frame.set_index("experiment_id").loc[["E01", "E02", "E03"]]
    means = selected["mean"].astype(float).to_numpy()
    errors = selected["sample_stdev"].astype(float).to_numpy()
    labels = [EXPERIMENT_METADATA[key]["label"] for key in ("E01", "E02", "E03")]
    colors = ["#4C78A8", "#F58518", "#54A24B"]
    markers = ["o", "s", "^"]

    figure, axes = plt.subplots(1, 2, figsize=(7.2, 3.55))
    figure.subplots_adjust(left=0.09, right=0.98, bottom=0.25, top=0.78, wspace=0.25)
    left, right = axes
    x = np.arange(3)
    for index in range(3):
        left.errorbar(
            x[index],
            means[index],
            yerr=errors[index],
            fmt=markers[index],
            color=colors[index],
            markeredgecolor="black",
            markeredgewidth=0.6,
            markersize=7,
            capsize=4,
            elinewidth=1.2,
            label=labels[index].replace("\n", " — "),
        )
        left.annotate(
            f"{means[index]:,.1f}",
            (x[index], means[index]),
            xytext=(0, 9),
            textcoords="offset points",
            ha="center",
            fontsize=7.5,
        )
    left.set_yscale("log")
    left.set_xticks(x, ["E01", "E02", "E03"])
    left.set_ylabel("Vazão média (Mbps; escala log)")
    left.set_title("Visão completa")
    left.grid(axis="y", which="both", linestyle=":", alpha=0.45)
    left.legend(loc="upper right", frameon=False)

    wan_x = np.arange(2)
    wan_means = means[1:]
    wan_errors = errors[1:]
    for index in range(2):
        right.errorbar(
            wan_x[index],
            wan_means[index],
            yerr=wan_errors[index],
            fmt=markers[index + 1],
            color=colors[index + 1],
            markeredgecolor="black",
            markeredgewidth=0.6,
            markersize=7,
            capsize=4,
            elinewidth=1.2,
        )
        right.annotate(
            f"{wan_means[index]:.2f}",
            (wan_x[index], wan_means[index]),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=7.5,
        )
    right.set_xticks(wan_x, labels[1:])
    right.set_xlim(-0.1, 1.1)
    right.set_ylabel("Vazão média (Mbps)")
    right.set_title("Ampliação — WAN degradada")
    right.set_ylim(0, (wan_means + wan_errors).max() * 1.14)
    right.grid(axis="y", linestyle=":", alpha=0.45)
    figure.suptitle("Gráfico 1 — Vazão média TCP CUBIC vs. BBR", fontweight="bold")
    figure.text(0.5, 0.025, f"Barras de erro: desvio-padrão amostral (n=5). Fonte: {integration_id}", ha="center", fontsize=7)
    metadata = {
        "experiments": ["E01", "E02", "E03"],
        "means": means.tolist(),
        "sample_stdev": errors.tolist(),
        "error_bar_measure": "sample_standard_deviation_ddof_1",
        "left_y_scale": "log",
        "right_y_scale": "linear",
        "left_y_label": left.get_ylabel(),
        "right_y_label": right.get_ylabel(),
        "errorbar_containers": sum(isinstance(item, ErrorbarContainer) for axis in axes for item in axis.containers),
    }
    return figure, metadata


def create_http_figure(statistics_frame: pd.DataFrame, integration_id: str):
    configure_style()
    selected = statistics_frame.set_index("experiment_id").loc[["E04", "E05", "E06"]]
    means = selected["mean"].astype(float).to_numpy()
    errors = selected["sample_stdev"].astype(float).to_numpy()
    labels = [EXPERIMENT_METADATA[key]["label"] for key in ("E04", "E05", "E06")]
    colors = ["#4C78A8", "#F58518", "#54A24B"]
    hatches = ["//", "..", "xx"]

    figure, axis = plt.subplots(figsize=(5.9, 3.55))
    figure.subplots_adjust(left=0.11, right=0.98, bottom=0.27, top=0.86)
    x = np.arange(3)
    bars = axis.bar(
        x,
        means,
        yerr=errors,
        capsize=5,
        color=colors,
        edgecolor="black",
        linewidth=0.7,
        error_kw={"elinewidth": 1.2, "capthick": 1.2},
    )
    for bar, hatch, value in zip(bars, hatches, means):
        bar.set_hatch(hatch)
        axis.annotate(
            f"{value:.2f} ms",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            fontsize=8,
        )
    axis.set_xticks(x, labels)
    axis.set_ylabel("TTFB médio (ms)")
    axis.set_xlabel("Experimento e modalidade de conexão")
    axis.set_title("Gráfico 2 — TTFB HTTP/2 vs. HTTP/3", fontweight="bold")
    axis.set_ylim(0, (means + errors).max() * 1.16)
    axis.grid(axis="y", linestyle=":", alpha=0.45)
    axis.set_axisbelow(True)
    figure.text(0.5, 0.025, f"Barras de erro: desvio-padrão amostral (n=5). Fonte: {integration_id}", ha="center", fontsize=7)
    metadata = {
        "experiments": ["E04", "E05", "E06"],
        "means": means.tolist(),
        "sample_stdev": errors.tolist(),
        "error_bar_measure": "sample_standard_deviation_ddof_1",
        "y_scale": "linear",
        "y_starts_at_zero": bool(axis.get_ylim()[0] == 0),
        "y_label": axis.get_ylabel(),
        "x_label": axis.get_xlabel(),
        "bar_count": len(bars),
        "errorbar_containers": sum(isinstance(item, ErrorbarContainer) for item in axis.containers),
    }
    return figure, metadata


def _safe_suffix(integration_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", integration_id)
    if not value:
        raise AnalysisError("identificador de integração não pode formar nome de arquivo")
    return value


def output_paths(root: Path, integration_id: str) -> OutputPaths:
    suffix = "" if integration_id == OFFICIAL_INTEGRATION_ID else f"_{_safe_suffix(integration_id)}"
    processed = root / "results" / "processed"
    figures = root / "results" / "figures"
    analysis_manifest = root / "results" / "analysis" / f"analysis_{_safe_suffix(integration_id)}" / "manifest.json"
    return OutputPaths(
        statistics_csv=processed / f"n3_statistics{suffix}.csv",
        statistics_json=processed / f"n3_statistics{suffix}.json",
        statistics_tex=processed / f"n3_statistics{suffix}.tex",
        comparisons_json=processed / f"n3_comparisons{suffix}.json",
        graph1_png=figures / f"graph_01_tcp_throughput{suffix}.png",
        graph1_pdf=figures / f"graph_01_tcp_throughput{suffix}.pdf",
        graph2_png=figures / f"graph_02_http_ttfb{suffix}.png",
        graph2_pdf=figures / f"graph_02_http_ttfb{suffix}.pdf",
        analysis_manifest=analysis_manifest,
    )


def statistics_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: (value.item() if isinstance(value, np.generic) else value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def write_statistics_csv(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATISTICS_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def latex_table(records: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "% Gerado automaticamente; valores apresentados com arredondamento, cálculos preservam precisão completa.",
        "\\begin{tabular}{llllrrr}",
        "\\hline",
        "ID & Algoritmo/Protocolo & Cenário & N & Média & Desvio-padrão & Retransmissões \\\\",
        "\\hline",
    ]
    for row in records:
        unit = row["metric_unit"]
        retransmissions = row["total_retransmissions"]
        retrans_text = "N/A" if retransmissions == "not_applicable" else f"{int(retransmissions):d}"
        lines.append(
            f"{row['experiment_id']} & {str(row['algorithm_protocol']).replace('&', '\\&')} & "
            f"{str(row['scenario']).replace(';', ',')} & {row['n']} & "
            f"{float(row['mean']):.4f} {unit} & {float(row['sample_stdev']):.4f} {unit} & "
            f"{retrans_text} \\\\" 
        )
    lines.extend(["\\hline", "\\end{tabular}", ""])
    return "\n".join(lines)


def analysis_version(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in (
        "src/analysis/n3_analysis.py",
        "scripts/analysis/generate_n3_analysis.sh",
    ):
        path = root / relative
        if not path.is_file():
            raise AnalysisError(f"arquivo da automação de análise ausente: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def generate_analysis(root: Path, integration_id: str) -> tuple[OutputPaths, dict[str, Any]]:
    loaded = load_official_results(root, integration_id)
    statistics_frame = calculate_statistics(root, loaded)
    comparisons = calculate_comparisons(statistics_frame)
    paths = output_paths(root, integration_id)
    destinations = [Path(value) for value in paths.__dict__.values()]
    existing = [path for path in destinations if path.exists()]
    if existing:
        raise AnalysisError(f"recusa em sobrescrever artefato existente: {existing[0]}")
    manifest_directory = paths.analysis_manifest.parent
    if manifest_directory.exists():
        raise AnalysisError(f"recusa em sobrescrever análise existente: {manifest_directory}")
    for path in destinations:
        if path != paths.analysis_manifest:
            path.parent.mkdir(parents=True, exist_ok=True)

    figure1, figure1_metadata = create_tcp_figure(statistics_frame, integration_id)
    figure2, figure2_metadata = create_http_figure(statistics_frame, integration_id)
    records = statistics_records(statistics_frame)
    manifest_directory.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".n3-analysis-", dir=manifest_directory.parent) as temporary:
        temporary_path = Path(temporary)
        temp_files = {
            name: temporary_path / path.name
            for name, path in paths.__dict__.items()
            if name != "analysis_manifest"
        }
        write_statistics_csv(temp_files["statistics_csv"], records)
        statistics_document = {
            "integration_id": integration_id,
            "source_manifest_sha256": sha256_file(loaded.integration_dir / "manifest.json"),
            "source_measurements_sha256": sha256_file(loaded.integration_dir / "measurements.csv"),
            "experiments": records,
        }
        temp_files["statistics_json"].write_text(
            json.dumps(statistics_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_files["statistics_tex"].write_text(latex_table(records), encoding="utf-8")
        temp_files["comparisons_json"].write_text(
            json.dumps(
                {"integration_id": integration_id, "comparisons": comparisons},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        save_metadata = {
            "Title": "Análise N3 UFPI",
            "Author": "tcp-quic-performance-analysis",
            "Subject": f"Fonte: {integration_id}; barras de erro: desvio-padrão amostral",
        }
        figure1.savefig(temp_files["graph1_png"], dpi=300, bbox_inches="tight", metadata={"Description": save_metadata["Subject"]})
        figure1.savefig(temp_files["graph1_pdf"], bbox_inches="tight", metadata=save_metadata)
        figure2.savefig(temp_files["graph2_png"], dpi=300, bbox_inches="tight", metadata={"Description": save_metadata["Subject"]})
        figure2.savefig(temp_files["graph2_pdf"], bbox_inches="tight", metadata=save_metadata)
        plt.close(figure1)
        plt.close(figure2)

        output_hashes = {
            getattr(paths, name).relative_to(root).as_posix(): sha256_file(temporary_file)
            for name, temporary_file in temp_files.items()
        }
        manifest = {
            "analysis_id": manifest_directory.name,
            "integration_id": integration_id,
            "status": "complete",
            "offline_analysis": True,
            "valid_measurements": 30,
            "source": {
                "manifest": loaded.integration_dir.relative_to(root).as_posix() + "/manifest.json",
                "measurements": loaded.integration_dir.relative_to(root).as_posix() + "/measurements.csv",
                "block_a_batch_id": loaded.manifest["block_a_batch_id"],
                "block_b_batch_id": loaded.manifest["block_b_batch_id"],
                "source_hashes_verified": len(loaded.manifest["source_hashes_sha256"]),
                "source_hash_mismatches": 0,
            },
            "statistics": {
                "sample_stdev_ddof": 1,
                "error_bars": "sample_standard_deviation",
                "rounding_policy": "full precision in calculations; rounding only in presentation",
            },
            "figures": {
                "graph_01": figure1_metadata,
                "graph_02": figure2_metadata,
            },
            "outputs_sha256": output_hashes,
            "analysis_automation_sha256": analysis_version(root),
            "python_packages": {
                "pandas": pd.__version__,
                "numpy": np.__version__,
                "matplotlib": matplotlib.__version__,
            },
        }
        temporary_manifest = temporary_path / "manifest.json"
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        manifest_directory.mkdir()
        for name, temporary_file in temp_files.items():
            os.replace(temporary_file, getattr(paths, name))
        os.replace(temporary_manifest, paths.analysis_manifest)
    return paths, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recalcula estatísticas e gera as figuras N3 sem executar benchmarks."
    )
    parser.add_argument(
        "--integration",
        default=OFFICIAL_INTEGRATION_ID,
        help="ID explícito de uma integração N3 válida",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        paths, manifest = generate_analysis(PROJECT_ROOT, arguments.integration)
    except AnalysisError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1
    print(
        f"[OK] Análise concluída: {manifest['analysis_id']}; "
        f"amostras={manifest['valid_measurements']}; hashes={manifest['source']['source_hashes_verified']}"
    )
    print(f"[FIGURA 1] {paths.graph1_png.relative_to(PROJECT_ROOT).as_posix()}")
    print(f"[FIGURA 2] {paths.graph2_png.relative_to(PROJECT_ROOT).as_posix()}")
    print(f"[TABELA] {paths.statistics_csv.relative_to(PROJECT_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

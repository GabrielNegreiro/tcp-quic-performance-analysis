#!/usr/bin/env python3
"""Auditoria offline e consolidação independente dos lotes oficiais N3."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.automation import block_a, block_b


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_BLOCK_A_BATCH = "block_a_20261118T195827Z_1ec19bbc"
OFFICIAL_BLOCK_B_BATCH = "block_b_20261118T235312Z_e440c163"

INTEGRATED_FIELDS = [
    "integration_id",
    "batch_id",
    "experiment_id",
    "block",
    "repetition",
    "protocol",
    "network_profile",
    "metric_name",
    "metric_value",
    "metric_unit",
    "algorithm",
    "retransmissions",
    "connection_mode",
    "session_resumed",
    "early_data_status",
    "status",
    "source_file",
]


class AuditError(RuntimeError):
    """Falha de integridade ou consistência dos resultados históricos."""


@dataclass
class BlockAudit:
    block: str
    batch_id: str
    rows: list[dict[str, Any]]
    integrated_rows: list[dict[str, Any]]
    summaries: list[dict[str, Any]]
    comparisons: dict[str, Any]
    source_paths: set[Path]
    checks: list[str]


@dataclass
class IntegratedAudit:
    block_a: BlockAudit
    block_b: BlockAudit
    rows: list[dict[str, Any]]
    summary: dict[str, Any]
    source_paths: set[Path]
    source_hashes: dict[str, str]
    audit: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_integration_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    normalized = mode.replace("-", "_")
    return f"n3_{normalized}_{stamp}_{uuid.uuid4().hex[:8]}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"JSON ausente ou inválido: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"JSON não é um objeto: {path}")
    return value


def load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise AuditError(f"CSV ausente ou inválido: {path}: {exc}") from exc
    return fields, rows


def resolve_project_path(root: Path, relative: str) -> Path:
    root_resolved = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise AuditError(f"referência sai do workspace: {relative}") from exc
    return path


def require_files(paths: Iterable[Path], sources: set[Path]) -> None:
    for path in paths:
        if not path.is_file():
            raise AuditError(f"arquivo de evidência ausente: {path}")
        sources.add(path.resolve())


def select_batch_rows(
    rows: Sequence[Mapping[str, Any]],
    batch_id: str,
    experiment_ids: Sequence[str],
    per_experiment: int = 5,
) -> list[dict[str, Any]]:
    selected = [dict(row) for row in rows if str(row.get("batch_id")) == batch_id]
    expected = len(experiment_ids) * per_experiment
    if len(selected) != expected:
        raise AuditError(f"lote {batch_id} possui {len(selected)} amostras; esperado={expected}")
    keys: set[tuple[str, int]] = set()
    for row in selected:
        experiment_id = str(row.get("experiment_id"))
        try:
            repetition = int(str(row.get("repetition")))
        except ValueError as exc:
            raise AuditError(f"repetição inválida no lote {batch_id}: {row.get('repetition')}") from exc
        key = (experiment_id, repetition)
        if key in keys:
            raise AuditError(f"amostra duplicada no lote {batch_id}: {key}")
        keys.add(key)
        if experiment_id not in experiment_ids or not 1 <= repetition <= per_experiment:
            raise AuditError(f"identificador/repetição inesperado no lote {batch_id}: {key}")
    expected_keys = {
        (experiment_id, repetition)
        for experiment_id in experiment_ids
        for repetition in range(1, per_experiment + 1)
    }
    if keys != expected_keys:
        raise AuditError(f"lote incompleto: {batch_id}")
    return sorted(selected, key=lambda row: (str(row["experiment_id"]), int(row["repetition"])))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_sources(root: Path, paths: Iterable[Path]) -> dict[str, str]:
    root_resolved = root.resolve()
    result: dict[str, str] = {}
    for path in sorted({item.resolve() for item in paths}, key=lambda item: item.as_posix()):
        try:
            relative = path.relative_to(root_resolved).as_posix()
        except ValueError as exc:
            raise AuditError(f"arquivo auditado fora do workspace: {path}") from exc
        result[relative] = sha256_file(path)
    return result


def verify_reference_hashes(root: Path, references: Mapping[str, str]) -> list[str]:
    mismatches: list[str] = []
    for relative, expected in references.items():
        path = resolve_project_path(root, relative)
        if not path.is_file() or sha256_file(path) != expected:
            mismatches.append(relative)
    return mismatches


def _float(row: Mapping[str, Any], field: str) -> float:
    try:
        value = float(str(row[field]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"valor numérico inválido: {field}") from exc
    if not math.isfinite(value):
        raise AuditError(f"valor numérico não finito: {field}")
    return value


def _int(row: Mapping[str, Any], field: str) -> int:
    value = _float(row, field)
    if not value.is_integer():
        raise AuditError(f"valor inteiro inválido: {field}")
    return int(value)


def _close(observed: float, expected: float, label: str, tolerance: float = 1e-9) -> None:
    if not math.isclose(observed, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AuditError(f"divergência estatística/métrica em {label}: {observed} != {expected}")


def _validate_manifest(
    path: Path, batch_id: str, experiments: Sequence[str], sources: set[Path]
) -> dict[str, Any]:
    require_files([path], sources)
    manifest = load_json(path)
    if manifest.get("batch_id") != batch_id or manifest.get("status") != "complete":
        raise AuditError(f"manifesto incompleto ou associado ao lote errado: {path}")
    if manifest.get("official_experiment") is not True:
        raise AuditError(f"manifesto não identifica experimento oficial: {path}")
    if set(manifest.get("experiment_ids", [])) != set(experiments):
        raise AuditError(f"experimentos incorretos no manifesto: {path}")
    if int(manifest.get("valid_measurements", -1)) != len(experiments) * 5:
        raise AuditError(f"quantidade incorreta no manifesto: {path}")
    return manifest


def audit_block_a(root: Path, batch_id: str) -> BlockAudit:
    root = root.resolve()
    sources: set[Path] = set()
    checks: list[str] = []
    global_csv = root / "results" / "processed" / "block_a_measurements.csv"
    fields, all_rows = load_csv(global_csv)
    if fields != block_a.CSV_FIELDS:
        raise AuditError("cabeçalho incompatível no CSV consolidado do Bloco A")
    sources.add(global_csv.resolve())
    rows = select_batch_rows(all_rows, batch_id, ("E01", "E02", "E03"))
    checks.append("block_a_csv_15_unique_samples")

    manifest_path = root / "results" / "raw" / "block_a" / "batches" / f"{batch_id}.manifest.json"
    _validate_manifest(manifest_path, batch_id, ("E01", "E02", "E03"), sources)
    checks.append("block_a_manifest_complete")

    batch_csv = root / "results" / "processed" / "block_a" / batch_id / "block_a_measurements.csv"
    batch_fields, batch_rows = load_csv(batch_csv)
    if batch_fields != fields or select_batch_rows(batch_rows, batch_id, ("E01", "E02", "E03")) != rows:
        raise AuditError("CSV do lote A diverge do consolidado histórico")
    sources.add(batch_csv.resolve())

    integrated: list[dict[str, Any]] = []
    config = {
        "E01": ("cubic", "lan"),
        "E02": ("cubic", "wan-degraded"),
        "E03": ("bbr", "wan-degraded"),
    }
    for row in rows:
        experiment_id = row["experiment_id"]
        algorithm, profile = config[experiment_id]
        repetition = _int(row, "repetition")
        if row.get("status") != "valid" or _int(row, "exit_code") != 0:
            raise AuditError(f"amostra inválida no Bloco A: {experiment_id}/{repetition}")
        if row.get("algorithm_requested") != algorithm or row.get("algorithm_confirmed") != algorithm:
            raise AuditError(f"algoritmo incorreto: {experiment_id}/{repetition}")
        if row.get("network_profile") != profile:
            raise AuditError(f"perfil incorreto: {experiment_id}/{repetition}")

        raw = resolve_project_path(root, row["raw_json_path"])
        metadata_path = resolve_project_path(root, row["metadata_path"])
        socket_path = resolve_project_path(root, row["socket_evidence_path"])
        qdisc_path = resolve_project_path(root, row["qdisc_evidence_path"])
        stem = raw.with_suffix("")
        stderr_path = stem.with_suffix(".stderr.txt")
        apply_path = stem.with_suffix(".profile-apply.txt")
        require_files(
            [raw, metadata_path, socket_path, qdisc_path, stderr_path, apply_path], sources
        )
        parsed = block_a.parse_iperf_file(raw)
        _close(parsed["duration_seconds"], _float(row, "duration_seconds"), "duration_seconds")
        _close(parsed["throughput_sender_mbps"], _float(row, "throughput_sender_mbps"), "throughput_sender_mbps")
        _close(parsed["throughput_receiver_mbps"], _float(row, "throughput_receiver_mbps"), "throughput_receiver_mbps")
        if int(parsed["retransmissions"]) != _int(row, "retransmissions"):
            raise AuditError(f"retransmissões divergentes: {experiment_id}/{repetition}")
        socket = block_a.parse_socket_evidence(socket_path.read_text(encoding="utf-8"), algorithm)
        if socket["algorithm"] != algorithm:
            raise AuditError(f"socket não confirma algoritmo: {experiment_id}/{repetition}")
        block_a.validate_qdisc_evidence(
            qdisc_path.read_text(encoding="utf-8"), block_a.EXPERIMENTS[experiment_id]
        )
        metadata = load_json(metadata_path)
        if (
            metadata.get("status") != "valid"
            or metadata.get("algorithm_confirmed") != algorithm
            or int(metadata.get("cleanup_exit_code", -1)) != 0
        ):
            raise AuditError(f"metadados inválidos: {experiment_id}/{repetition}")
        if stderr_path.read_text(encoding="utf-8", errors="replace").strip():
            raise AuditError(f"stderr histórico não vazio: {experiment_id}/{repetition}")

        integrated.append(
            {
                "batch_id": batch_id,
                "experiment_id": experiment_id,
                "block": "A",
                "repetition": repetition,
                "protocol": "TCP",
                "network_profile": profile,
                "metric_name": "throughput_sender",
                "metric_value": _float(row, "throughput_sender_mbps"),
                "metric_unit": "Mbps",
                "algorithm": algorithm,
                "retransmissions": _int(row, "retransmissions"),
                "connection_mode": "single_tcp_flow",
                "session_resumed": "not_applicable",
                "early_data_status": "not_applicable",
                "status": "valid",
                "source_file": raw.relative_to(root).as_posix(),
            }
        )
    checks.extend(
        ["block_a_iperf_json_valid", "block_a_socket_algorithms_confirmed", "block_a_qdisc_profiles_confirmed"]
    )

    summaries: list[dict[str, Any]] = []
    for experiment_id in ("E01", "E02", "E03"):
        selected = [row for row in integrated if row["experiment_id"] == experiment_id]
        values = [float(row["metric_value"]) for row in selected]
        summaries.append(
            {
                "experiment_id": experiment_id,
                "block": "A",
                "protocol_or_algorithm": selected[0]["algorithm"],
                "network_profile": selected[0]["network_profile"],
                "metric_name": "throughput_sender",
                "metric_unit": "Mbps",
                "valid_samples": len(values),
                "mean": statistics.mean(values),
                "sample_stdev": statistics.stdev(values),
                "minimum": min(values),
                "maximum": max(values),
                "total_retransmissions": sum(int(row["retransmissions"]) for row in selected),
            }
        )
    means = {item["experiment_id"]: item["mean"] for item in summaries}
    comparisons = {
        "e02_vs_e01_percent": block_a.percentage_difference(means["E02"], means["E01"]),
        "e03_vs_e02_percent": block_a.percentage_difference(means["E03"], means["E02"]),
    }
    original_summary_path = root / "results" / "processed" / "block_a" / batch_id / "summary.json"
    require_files([original_summary_path], sources)
    original = load_json(original_summary_path)
    original_by_id = {item["experiment_id"]: item for item in original.get("summaries", [])}
    for item in summaries:
        expected = original_by_id.get(item["experiment_id"], {})
        _close(item["mean"], float(expected.get("mean_throughput_mbps", math.nan)), f"{item['experiment_id']} mean")
        _close(item["sample_stdev"], float(expected.get("sample_stdev_throughput_mbps", math.nan)), f"{item['experiment_id']} stdev")
        if item["total_retransmissions"] != int(expected.get("total_retransmissions", -1)):
            raise AuditError(f"resumo histórico diverge em {item['experiment_id']} retransmissions")
    for key, value in comparisons.items():
        _close(value, float(original.get("comparisons", {}).get(key, math.nan)), key)
    checks.append("block_a_summary_recalculated_matches")
    return BlockAudit("A", batch_id, rows, integrated, summaries, comparisons, sources, checks)


def audit_block_b(root: Path, batch_id: str) -> BlockAudit:
    root = root.resolve()
    sources: set[Path] = set()
    checks: list[str] = []
    global_csv = root / "results" / "processed" / "block_b_measurements.csv"
    fields, all_rows = load_csv(global_csv)
    if fields != block_b.CSV_FIELDS:
        raise AuditError("cabeçalho incompatível no CSV consolidado do Bloco B")
    sources.add(global_csv.resolve())
    rows = select_batch_rows(all_rows, batch_id, ("E04", "E05", "E06"))
    checks.append("block_b_csv_15_unique_samples")

    manifest_path = root / "results" / "raw" / "block_b" / "batches" / f"{batch_id}.manifest.json"
    _validate_manifest(manifest_path, batch_id, ("E04", "E05", "E06"), sources)
    checks.append("block_b_manifest_complete")
    batch_csv = root / "results" / "processed" / "block_b" / batch_id / "block_b_measurements.csv"
    batch_fields, batch_rows = load_csv(batch_csv)
    if batch_fields != fields or select_batch_rows(batch_rows, batch_id, ("E04", "E05", "E06")) != rows:
        raise AuditError("CSV do lote B diverge do consolidado histórico")
    sources.add(batch_csv.resolve())

    integrated: list[dict[str, Any]] = []
    for row in rows:
        experiment_id = row["experiment_id"]
        experiment = block_b.EXPERIMENTS[experiment_id]
        repetition = _int(row, "repetition")
        if row.get("status") != "valid" or _int(row, "exit_code") != 0:
            raise AuditError(f"amostra inválida no Bloco B: {experiment_id}/{repetition}")
        if row.get("network_profile") != "wan-no-loss":
            raise AuditError(f"perfil incorreto no Bloco B: {experiment_id}/{repetition}")
        if row.get("connection_mode") != experiment.connection_mode:
            raise AuditError(f"modo de conexão incorreto: {experiment_id}/{repetition}")

        raw = resolve_project_path(root, row["raw_json_path"])
        metadata_path = resolve_project_path(root, row["metadata_path"])
        trace_path = resolve_project_path(root, row["trace_path"])
        headers_path = resolve_project_path(root, row["headers_path"])
        qdisc_path = resolve_project_path(root, row["qdisc_evidence_path"])
        stem = raw.with_suffix("")
        apply_path = stem.with_suffix(".profile-apply.txt")
        required = [raw, metadata_path, trace_path, headers_path, qdisc_path, apply_path]
        if experiment_id == "E06":
            required.extend(
                [
                    stem.with_suffix(".prep.json"),
                    stem.with_suffix(".prep.trace.txt"),
                    stem.with_suffix(".prep.headers.txt"),
                ]
            )
        require_files(required, sources)
        document = block_b.load_curl_json(raw.read_text(encoding="utf-8"))
        metrics = block_b.parse_curl_document(document, experiment.protocol_requested)
        _close(metrics["ttfb_ms"], _float(row, "ttfb_ms"), "ttfb_ms")
        trace = trace_path.read_text(encoding="utf-8", errors="replace")
        headers = headers_path.read_text(encoding="utf-8", errors="replace")
        metadata = load_json(metadata_path)
        if metadata.get("status") != "valid" or int(metadata.get("cleanup_exit_code", -1)) != 0:
            raise AuditError(f"metadados inválidos: {experiment_id}/{repetition}")
        block_b.validate_qdisc_evidence(qdisc_path.read_text(encoding="utf-8"))

        if experiment_id in {"E04", "E05"}:
            block_b.validate_cold_evidence(metrics, trace, headers)
            if row.get("session_resumed") != "not_used" or row.get("early_data_status") != "not_used":
                raise AuditError(f"estado Cold inconsistente: {experiment_id}/{repetition}")
        else:
            prep_path = stem.with_suffix(".prep.json")
            prep_document = block_b.load_curl_json(prep_path.read_text(encoding="utf-8"))
            prep_metrics = block_b.parse_curl_document(prep_document, "HTTP/3")
            classification = block_b.validate_e06_evidence(
                prep_metrics,
                metrics,
                trace,
                headers,
                int(metadata.get("ticket_size_bytes", 0)),
            )
            if (
                classification["session_resumed"] != row.get("session_resumed")
                or classification["early_data_status"] != row.get("early_data_status")
                or int(metadata.get("prep_local_port", 0)) == int(metadata.get("measured_local_port", 0))
            ):
                raise AuditError(f"prova E06 inconsistente: {experiment_id}/{repetition}")

        integrated.append(
            {
                "batch_id": batch_id,
                "experiment_id": experiment_id,
                "block": "B",
                "repetition": repetition,
                "protocol": row["protocol_confirmed"],
                "network_profile": "wan-no-loss",
                "metric_name": "ttfb",
                "metric_value": _float(row, "ttfb_ms"),
                "metric_unit": "ms",
                "algorithm": "not_applicable",
                "retransmissions": "not_applicable",
                "connection_mode": row["connection_mode"],
                "session_resumed": row["session_resumed"],
                "early_data_status": row["early_data_status"],
                "status": "valid",
                "source_file": raw.relative_to(root).as_posix(),
            }
        )
    checks.extend(
        ["block_b_curl_json_valid", "block_b_protocols_and_cold_state_confirmed", "block_b_e06_resumption_and_0rtt_confirmed", "block_b_qdisc_profiles_confirmed"]
    )

    summaries: list[dict[str, Any]] = []
    for experiment_id in ("E04", "E05", "E06"):
        selected = [row for row in integrated if row["experiment_id"] == experiment_id]
        values = [float(row["metric_value"]) for row in selected]
        summaries.append(
            {
                "experiment_id": experiment_id,
                "block": "B",
                "protocol_or_algorithm": selected[0]["protocol"],
                "network_profile": "wan-no-loss",
                "metric_name": "ttfb",
                "metric_unit": "ms",
                "valid_samples": len(values),
                "mean": statistics.mean(values),
                "sample_stdev": statistics.stdev(values),
                "minimum": min(values),
                "maximum": max(values),
                "total_retransmissions": "not_applicable",
            }
        )
    means = {item["experiment_id"]: item["mean"] for item in summaries}
    comparisons: dict[str, dict[str, float]] = {}
    for first, second in (("E04", "E05"), ("E04", "E06"), ("E05", "E06")):
        comparisons[f"{second.lower()}_vs_{first.lower()}"] = {
            "absolute_difference_ms": means[second] - means[first],
            "percent_difference": block_b.percentage_difference(means[second], means[first]),
        }
    original_summary_path = root / "results" / "processed" / "block_b" / batch_id / "summary.json"
    require_files([original_summary_path], sources)
    original = load_json(original_summary_path)
    original_by_id = {item["experiment_id"]: item for item in original.get("summaries", [])}
    for item in summaries:
        expected = original_by_id.get(item["experiment_id"], {})
        _close(item["mean"], float(expected.get("mean_ttfb_ms", math.nan)), f"{item['experiment_id']} mean")
        _close(item["sample_stdev"], float(expected.get("sample_stdev_ttfb_ms", math.nan)), f"{item['experiment_id']} stdev")
    for key, values in comparisons.items():
        original_values = original.get("comparisons", {}).get(key, {})
        for field, value in values.items():
            _close(value, float(original_values.get(field, math.nan)), f"{key}.{field}")
    checks.append("block_b_summary_recalculated_matches")
    return BlockAudit("B", batch_id, rows, integrated, summaries, comparisons, sources, checks)


def audit_batches(root: Path, block_a_batch: str, block_b_batch: str) -> IntegratedAudit:
    block_a_audit = audit_block_a(root, block_a_batch)
    block_b_audit = audit_block_b(root, block_b_batch)
    rows = [*block_a_audit.integrated_rows, *block_b_audit.integrated_rows]
    experiment_ids = {row["experiment_id"] for row in rows}
    keys = {(row["block"], row["batch_id"], row["experiment_id"], row["repetition"]) for row in rows}
    if len(rows) != 30 or len(keys) != 30 or experiment_ids != set(block_a.EXPERIMENTS) | set(block_b.EXPERIMENTS):
        raise AuditError("validação global não encontrou exatamente E01–E06 e 30 amostras únicas")
    source_paths = block_a_audit.source_paths | block_b_audit.source_paths
    source_hashes = hash_sources(root, source_paths)
    summary = {
        "block_a_batch_id": block_a_batch,
        "block_b_batch_id": block_b_batch,
        "experiments": [*block_a_audit.summaries, *block_b_audit.summaries],
        "comparisons": {
            "block_a": block_a_audit.comparisons,
            "block_b": block_b_audit.comparisons,
        },
        "formula": "sample_stdev = sqrt(sum((x - mean)^2) / (n - 1))",
    }
    checks = [*block_a_audit.checks, *block_b_audit.checks, "global_6_experiments_30_unique_samples"]
    audit = {
        "status": "passed",
        "audited_at": utc_now(),
        "valid_results": 30,
        "rejected_results": 0,
        "experiments": 6,
        "files_analyzed": len(source_paths),
        "hashes_computed": len(source_hashes),
        "reference_hashes_available": 0,
        "hash_mismatches": [],
        "missing_files": [],
        "inconsistencies": [],
        "checks": checks,
        "explicit_conclusion": "all_30_official_results_are_valid",
    }
    return IntegratedAudit(block_a_audit, block_b_audit, rows, summary, source_paths, source_hashes, audit)


def write_csv(path: Path, integration_id: str, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INTEGRATED_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: integration_id if field == "integration_id" else row.get(field, "") for field in INTEGRATED_FIELDS})


def write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_integration(
    root: Path,
    result: IntegratedAudit,
    integration_id: str,
    mode: str,
    automation_version: str,
    git_commit: str | None,
) -> Path:
    integrated_root = root / "results" / "integrated"
    integrated_root.mkdir(parents=True, exist_ok=True)
    destination = integrated_root / integration_id
    working = integrated_root / f".working_{integration_id}"
    if destination.exists() or working.exists():
        raise AuditError(f"recusa em sobrescrever integração existente: {integration_id}")
    working.mkdir()
    try:
        write_csv(working / "measurements.csv", integration_id, result.rows)
        summary = {"integration_id": integration_id, **result.summary}
        write_json(working / "summary.json", summary)
        audit_document = {"integration_id": integration_id, **result.audit}
        write_json(working / "audit.json", audit_document)
        manifest = {
            "integration_id": integration_id,
            "created_at": utc_now(),
            "mode": mode,
            "status": "complete",
            "block_a_batch_id": result.block_a.batch_id,
            "block_b_batch_id": result.block_b.batch_id,
            "block_a_manifest": f"results/raw/block_a/batches/{result.block_a.batch_id}.manifest.json",
            "block_b_manifest": f"results/raw/block_b/batches/{result.block_b.batch_id}.manifest.json",
            "experiments": 6,
            "valid_measurements": 30,
            "audit_status": result.audit["status"],
            "source_hashes_sha256": result.source_hashes,
            "source_hash_count": len(result.source_hashes),
            "historical_reference_hashes": {
                "block_a_automation_sha256": load_json(
                    root / "results" / "raw" / "block_a" / "batches" / f"{result.block_a.batch_id}.manifest.json"
                ).get("automation_sha256"),
                "block_b_automation_sha256": load_json(
                    root / "results" / "raw" / "block_b" / "batches" / f"{result.block_b.batch_id}.manifest.json"
                ).get("automation_sha256"),
            },
            "automation_version_sha256": automation_version,
            "git_commit": git_commit,
            "outputs": {
                "measurements": "measurements.csv",
                "summary": "summary.json",
                "audit": "audit.json",
            },
        }
        write_json(working / "manifest.json", manifest)
        if hash_sources(root, result.source_paths) != result.source_hashes:
            raise AuditError("arquivos históricos mudaram durante a consolidação")
        os.replace(working, destination)
    except BaseException:
        for path in working.iterdir() if working.exists() else []:
            path.unlink()
        if working.exists():
            working.rmdir()
        raise
    return destination

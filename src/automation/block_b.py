#!/usr/bin/env python3
"""Orquestração reproduzível dos experimentos HTTP oficiais E04–E06."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import signal
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.automation.locking import assert_n3_access


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = PROJECT_ROOT / "results"
RAW_ROOT = RESULTS_ROOT / "raw" / "block_b"
PROCESSED_ROOT = RESULTS_ROOT / "processed"
LOCK_PATHS = (RESULTS_ROOT / ".block_a.lock", RESULTS_ROOT / ".block_b.lock")
CLIENT_SERVICE = "client-bench"
SERVER_SERVICE = "server-http3"
CURL = "/opt/curl-h3/bin/curl"
TARGET_URL = "https://server-http3/health.txt"
CA_CERT = "/certs/server-http3.crt"
RUNS_PER_EXPERIMENT = 5
SETTLE_SECONDS = 2
MAX_ATTEMPTS_PER_REPETITION = 3
EXPECTED_SIZE = 1024


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    directory: str
    protocol_requested: str
    connection_mode: str


EXPERIMENTS: dict[str, Experiment] = {
    "E04": Experiment("E04", "e04_http2", "HTTP/2", "fresh_tcp_tls"),
    "E05": Experiment("E05", "e05_http3_cold", "HTTP/3", "fresh_quic_cold"),
    "E06": Experiment("E06", "e06_http3_warm", "HTTP/3", "fresh_quic_0rtt"),
}

CSV_FIELDS = [
    "batch_id",
    "experiment_id",
    "repetition",
    "protocol_requested",
    "protocol_confirmed",
    "connection_mode",
    "session_resumed",
    "early_data_status",
    "tls_earlydata_bytes",
    "network_profile",
    "target_rtt_ms",
    "configured_loss_percent",
    "ttfb_ms",
    "time_namelookup_ms",
    "time_connect_ms",
    "time_appconnect_ms",
    "time_pretransfer_ms",
    "time_total_ms",
    "num_connects",
    "response_code",
    "size_download_bytes",
    "local_port",
    "timestamp",
    "exit_code",
    "status",
    "raw_json_path",
    "metadata_path",
    "trace_path",
    "headers_path",
    "qdisc_evidence_path",
]


class BlockBError(RuntimeError):
    """Erro de validação, execução ou integridade do Bloco B."""


class CommandError(BlockBError):
    """Falha de um comando externo."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_batch_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"block_b_{stamp}_{uuid.uuid4().hex[:8]}"


def experiment_for_id(experiment_id: str) -> Experiment:
    try:
        return EXPERIMENTS[experiment_id.upper()]
    except KeyError as exc:
        raise BlockBError(f"experimento desconhecido: {experiment_id}") from exc


def seconds_to_ms(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockBError(f"campo temporal ausente/inválido: {field}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise BlockBError(f"campo temporal ausente/inválido: {field}")
    return number * 1000.0


def load_curl_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text.strip())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BlockBError(f"saída JSON do curl inválida: {exc}") from exc
    if not isinstance(value, dict):
        raise BlockBError("saída JSON do curl não é um objeto")
    return value


def _required_int(document: Mapping[str, Any], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockBError(f"campo inteiro essencial ausente/inválido: {field}")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise BlockBError(f"campo inteiro essencial ausente/inválido: {field}")
    return int(number)


def parse_curl_document(document: Mapping[str, Any], expected_protocol: str) -> dict[str, Any]:
    """Extrai as métricas e rejeita fallback, resposta parcial ou conexão reutilizada."""
    expected_version = {"HTTP/2": "2", "HTTP/3": "3"}[expected_protocol]
    actual_version = str(document.get("http_version", ""))
    if actual_version != expected_version:
        raise BlockBError(
            f"protocolo incorreto/fallback: esperado={expected_version}, observado={actual_version}"
        )
    response_code = _required_int(document, "response_code")
    if response_code != 200:
        raise BlockBError(f"código HTTP inesperado: {response_code}")
    size_download = _required_int(document, "size_download")
    if size_download != EXPECTED_SIZE:
        raise BlockBError(f"tamanho inesperado: {size_download}; esperado={EXPECTED_SIZE}")
    num_connects = _required_int(document, "num_connects")
    if num_connects != 1:
        raise BlockBError(f"a requisição não abriu exatamente uma nova conexão: {num_connects}")
    if document.get("method") != "GET":
        raise BlockBError(f"método não permitido: {document.get('method')}")
    exit_code = _required_int(document, "exitcode")
    if exit_code != 0:
        raise BlockBError(f"curl registrou exitcode={exit_code}")
    local_port = _required_int(document, "local_port")
    if not 1 <= local_port <= 65535:
        raise BlockBError(f"porta local inválida: {local_port}")

    return {
        "protocol_confirmed": f"HTTP/{actual_version}",
        "response_code": response_code,
        "size_download_bytes": size_download,
        "num_connects": num_connects,
        "local_port": local_port,
        "tls_earlydata_bytes": _required_int(document, "tls_earlydata"),
        "ttfb_ms": seconds_to_ms(document.get("time_starttransfer"), "time_starttransfer"),
        "time_namelookup_ms": seconds_to_ms(document.get("time_namelookup"), "time_namelookup"),
        "time_connect_ms": seconds_to_ms(document.get("time_connect"), "time_connect"),
        "time_appconnect_ms": seconds_to_ms(document.get("time_appconnect"), "time_appconnect"),
        "time_pretransfer_ms": seconds_to_ms(document.get("time_pretransfer"), "time_pretransfer"),
        "time_total_ms": seconds_to_ms(document.get("time_total"), "time_total"),
        "exit_code": exit_code,
    }


def validate_cold_evidence(metrics: Mapping[str, Any], trace: str, headers: str) -> None:
    if int(metrics["tls_earlydata_bytes"]) != 0:
        raise BlockBError("amostra Cold enviou early data")
    if "SSL reusing session" in trace or "SSL session allows" in trace:
        raise BlockBError("amostra Cold reutilizou uma sessão TLS")
    if "x-lab-quic-early-data: accepted" in headers.lower():
        raise BlockBError("servidor classificou indevidamente a amostra Cold como early data")


def validate_e06_evidence(
    prep_metrics: Mapping[str, Any],
    measured_metrics: Mapping[str, Any],
    measured_trace: str,
    measured_headers: str,
    ticket_size: int,
) -> dict[str, str]:
    """Exige evidências independentes de nova conexão, resumption e 0-RTT aceito."""
    if ticket_size <= 0:
        raise BlockBError("arquivo temporário de sessão não contém ticket utilizável")
    if int(prep_metrics["tls_earlydata_bytes"]) != 0:
        raise BlockBError("requisição preparatória inesperadamente usou early data")
    if int(prep_metrics["local_port"]) == int(measured_metrics["local_port"]):
        raise BlockBError("não foi possível distinguir a conexão QUIC medida da preparatória")
    if "SSL reusing session with ALPN 'h3'" not in measured_trace:
        raise BlockBError("trace não confirmou retomada da sessão HTTP/3")
    if "SSL session allows" not in measured_trace or "early data" not in measured_trace:
        raise BlockBError("trace não confirmou capacidade de early data do ticket")
    if int(measured_metrics["tls_earlydata_bytes"]) <= 0:
        raise BlockBError("curl não confirmou bytes enviados/aceitos como early data")
    if "x-lab-quic-early-data: accepted" not in measured_headers.lower():
        raise BlockBError("Caddy não confirmou processamento da requisição antes do handshake")
    return {"session_resumed": "confirmed", "early_data_status": "confirmed"}


def validate_qdisc_evidence(text: str) -> None:
    if text.count("active_profile=wan-no-loss") != 2:
        raise BlockBError("perfil wan-no-loss não foi confirmado nos dois serviços")
    if text.count("qdisc netem 1a2b: root") != 2:
        raise BlockBError("qdisc netem gerenciada não foi confirmada nos dois serviços")
    if text.count("delay 25ms") != 2:
        raise BlockBError("atraso de 25 ms por direção não foi confirmado")
    if " loss " in text:
        raise BlockBError("perda inesperada no perfil wan-no-loss")


def write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def ensure_paths_absent(paths: Iterable[Path]) -> None:
    existing = [path for path in paths if path.exists()]
    if existing:
        raise BlockBError(f"recusa em sobrescrever resultado existente: {existing[0]}")


def normalize_csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in CSV_FIELDS}


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(normalize_csv_row(row))
    os.replace(temporary, path)


def merge_measurements_csv(path: Path, new_rows: Sequence[Mapping[str, Any]]) -> None:
    existing: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != CSV_FIELDS:
                raise BlockBError(f"cabeçalho incompatível no CSV existente: {path}")
            existing = list(reader)
    combined: list[Mapping[str, Any]] = [*existing, *new_rows]
    seen: set[tuple[str, str, str]] = set()
    for row in combined:
        key = (str(row["batch_id"]), str(row["experiment_id"]), str(row["repetition"]))
        if key in seen:
            raise BlockBError(f"medição duplicada no CSV: {key}")
        seen.add(key)
    write_csv(path, combined)


def percentage_difference(new_value: float, reference_value: float) -> float:
    if reference_value == 0:
        raise BlockBError("referência zero em diferença percentual")
    return (new_value - reference_value) / reference_value * 100.0


def summarize_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for experiment_id in EXPERIMENTS:
        selected = [row for row in rows if row["experiment_id"] == experiment_id]
        if not selected:
            continue
        values = [float(row["ttfb_ms"]) for row in selected]
        summaries.append(
            {
                "experiment_id": experiment_id,
                "protocol": selected[0]["protocol_confirmed"],
                "connection_mode": selected[0]["connection_mode"],
                "valid_samples": len(values),
                "mean_ttfb_ms": statistics.mean(values),
                "sample_stdev_ttfb_ms": statistics.stdev(values) if len(values) > 1 else 0.0,
                "min_ttfb_ms": min(values),
                "max_ttfb_ms": max(values),
            }
        )
    return summaries


class BlockBRunner:
    def __init__(self, experiment_ids: Sequence[str], preflight_only: bool = False):
        self.experiment_ids = [experiment_for_id(value).experiment_id for value in experiment_ids]
        self.preflight_only = preflight_only
        self.batch_id = make_batch_id()
        self.locks_acquired: list[Path] = []
        self.rows: list[dict[str, Any]] = []
        self.manifest_path = RAW_ROOT / "batches" / f"{self.batch_id}.manifest.json"
        self.processed_batch_dir = PROCESSED_ROOT / "block_b" / self.batch_id
        self.manifest: dict[str, Any] = {}

    @property
    def docker_base(self) -> list[str]:
        return ["docker", "compose", "--project-directory", str(PROJECT_ROOT)]

    def run_command(
        self, command: Sequence[str], *, check: bool = True, timeout: int = 120
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            list(command),
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        )
        if check and result.returncode != 0:
            raise CommandError(
                f"comando falhou ({result.returncode}): {' '.join(command)}\n{result.stdout}"
            )
        return result

    def docker(self, *arguments: str, check: bool = True, timeout: int = 120):
        return self.run_command([*self.docker_base, *arguments], check=check, timeout=timeout)

    def network_script(self, name: str, *arguments: str, check: bool = True):
        return self.run_command(
            ["sh", f"scripts/network/{name}", *arguments], check=check, timeout=180
        )

    def acquire_locks(self) -> None:
        RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
        assert_n3_access(RESULTS_ROOT, BlockBError)
        try:
            for path in LOCK_PATHS:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(f"pid={os.getpid()} batch_id={self.batch_id} started={utc_now()}\n")
                self.locks_acquired.append(path)
        except FileExistsError as exc:
            owner = path.read_text(encoding="utf-8", errors="replace").strip()
            self.release_locks()
            raise BlockBError(f"outra medição ou lock residual existe em {path}: {owner}") from exc

    def release_locks(self) -> None:
        for path in reversed(self.locks_acquired):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self.locks_acquired.clear()

    def reset_profiles(self, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.network_script("reset_profile.sh", "--all", check=check)

    def ensure_no_client_curl(self) -> None:
        result = self.docker(
            "exec", "-T", CLIENT_SERVICE, "sh", "-c",
            "pids=$(pidof curl 2>/dev/null || true); [ -z \"$pids\" ]",
            check=False,
        )
        if result.returncode != 0:
            raise BlockBError(f"curl residual ou concorrente no cliente: {result.stdout.strip()}")

    def remove_client_files(self, paths: Sequence[str]) -> None:
        safe = [path for path in paths if path.startswith("/tmp/block_b_")]
        if len(safe) != len(paths):
            raise BlockBError("recusa em remover caminho temporário fora do namespace do Bloco B")
        if safe:
            self.docker("exec", "-T", CLIENT_SERVICE, "rm", "-f", *safe, check=False)

    def execute_curl(
        self,
        experiment: Experiment,
        token: str,
        *,
        session_file: str | None = None,
        early_data: bool = False,
    ) -> tuple[int, str, str, str, dict[str, Any]]:
        header_path = f"/tmp/block_b_{token}.headers"
        protocol_flag = "--http2" if experiment.experiment_id == "E04" else "--http3-only"
        arguments = [
            CURL,
            protocol_flag,
            "--silent",
            "--show-error",
            "--cacert", CA_CERT,
            "--request", "GET",
            "--output", "/dev/null",
            "--dump-header", header_path,
            "--trace-ascii", "%",
            "--trace-config", "ssl",
            "--write-out", "%{json}\n",
        ]
        if experiment.experiment_id in {"E04", "E05"}:
            arguments.append("--no-sessionid")
        if session_file is not None:
            arguments.extend(["--ssl-sessions", session_file])
        if early_data:
            arguments.append("--tls-earlydata")
        arguments.append(TARGET_URL)

        process = subprocess.run(
            [*self.docker_base, "exec", "-T", CLIENT_SERVICE, *arguments],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        )
        headers = self.docker(
            "exec", "-T", CLIENT_SERVICE, "cat", header_path, check=False
        ).stdout
        self.remove_client_files([header_path])
        if process.returncode != 0:
            raise CommandError(
                f"curl falhou ({process.returncode})\nstdout={process.stdout}\nstderr={process.stderr}"
            )
        document = load_curl_json(process.stdout)
        return process.returncode, process.stdout, process.stderr, headers, document

    def inspect_session_file(self, path: str) -> int:
        result = self.docker(
            "exec", "-T", CLIENT_SERVICE, "stat", "-c", "%s", path, check=False
        )
        if result.returncode != 0:
            raise BlockBError("curl não criou o arquivo temporário de sessão")
        try:
            return int(result.stdout.strip())
        except ValueError as exc:
            raise BlockBError("tamanho do arquivo temporário de sessão é inválido") from exc

    def run_e06_pair(self, token: str) -> dict[str, Any]:
        experiment = EXPERIMENTS["E06"]
        session_file = f"/tmp/block_b_{token}.sessions"
        self.remove_client_files([session_file])
        try:
            prep = self.execute_curl(
                experiment, f"{token}_prep", session_file=session_file, early_data=True
            )
            prep_metrics = parse_curl_document(prep[4], experiment.protocol_requested)
            validate_cold_evidence(prep_metrics, prep[2], prep[3])
            ticket_size = self.inspect_session_file(session_file)
            measured = self.execute_curl(
                experiment, f"{token}_measured", session_file=session_file, early_data=True
            )
            measured_metrics = parse_curl_document(measured[4], experiment.protocol_requested)
            classification = validate_e06_evidence(
                prep_metrics, measured_metrics, measured[2], measured[3], ticket_size
            )
            return {
                "prep": prep,
                "prep_metrics": prep_metrics,
                "measured": measured,
                "measured_metrics": measured_metrics,
                "ticket_size_bytes": ticket_size,
                **classification,
            }
        finally:
            self.remove_client_files([session_file])

    def preflight(self) -> None:
        print("[PRÉ-VALIDAÇÃO] Verificando Docker, HTTP/2, HTTP/3, sessões e estado de rede...")
        self.run_command(["docker", "info"], timeout=60)
        self.docker("config", "--quiet")
        running = set(self.docker("ps", "--services", "--filter", "status=running").stdout.splitlines())
        expected = {CLIENT_SERVICE, "server-tcp", SERVER_SERVICE}
        if running != expected:
            raise BlockBError(f"serviços em execução {running}; esperado {expected}")
        self.ensure_no_client_curl()
        version = self.docker("exec", "-T", CLIENT_SERVICE, CURL, "--version").stdout
        for marker in ("HTTP2", "HTTP3", "GnuTLS", "ngtcp2", "nghttp3", "SSLS-EXPORT"):
            if marker not in version:
                raise BlockBError(f"curl sem recurso obrigatório: {marker}")
        help_all = self.docker("exec", "-T", CLIENT_SERVICE, CURL, "--help", "all").stdout
        for option in ("--ssl-sessions", "--tls-earlydata"):
            if option not in help_all:
                raise BlockBError(f"curl sem opção obrigatória: {option}")
        if (PROJECT_ROOT / "site" / "health.txt").stat().st_size != EXPECTED_SIZE:
            raise BlockBError("site/health.txt não possui exatamente 1024 bytes")

        self.reset_profiles()
        inspection = self.network_script("inspect_profile.sh", "--all").stdout
        if inspection.count("ownership=default") != 3 or inspection.count("active_profile=none") != 3:
            raise BlockBError(f"estado inicial de qdisc inesperado:\n{inspection}")

        for experiment_id in ("E04", "E05"):
            experiment = EXPERIMENTS[experiment_id]
            result = self.execute_curl(experiment, f"preflight_{experiment_id.lower()}_{uuid.uuid4().hex}")
            metrics = parse_curl_document(result[4], experiment.protocol_requested)
            validate_cold_evidence(metrics, result[2], result[3])
        diagnostic = self.run_e06_pair(f"preflight_e06_{uuid.uuid4().hex}")
        print(
            "[OK] Resumption/0-RTT: nova porta "
            f"{diagnostic['prep_metrics']['local_port']} -> "
            f"{diagnostic['measured_metrics']['local_port']}, "
            f"early_data={diagnostic['measured_metrics']['tls_earlydata_bytes']} bytes, "
            "Caddy=accepted."
        )
        print("[OK] Pré-validação aprovada; nenhuma medição oficial foi iniciada.")

    def source_hash(self) -> str:
        digest = hashlib.sha256()
        for relative in ("src/automation/block_b.py", "scripts/experiments/run_block_b.sh"):
            digest.update((PROJECT_ROOT / relative).read_bytes())
        return digest.hexdigest()

    def execution_order(self) -> list[dict[str, Any]]:
        selected = set(self.experiment_ids)
        order: list[dict[str, Any]] = []
        for repetition in range(1, RUNS_PER_EXPERIMENT + 1):
            for experiment_id in ("E04", "E05", "E06"):
                if experiment_id in selected:
                    order.append({"experiment_id": experiment_id, "repetition": repetition})
        return order

    def collect_manifest(self) -> dict[str, Any]:
        git_commit = self.run_command(["git", "rev-parse", "HEAD"], check=False).stdout.strip()
        git_dirty = bool(self.run_command(["git", "status", "--porcelain"], check=False).stdout.strip())
        curl_version = self.docker("exec", "-T", CLIENT_SERVICE, CURL, "--version").stdout
        caddy_version = self.docker("exec", "-T", SERVER_SERVICE, "caddy", "version").stdout.strip()
        return {
            "batch_id": self.batch_id,
            "status": "running",
            "official_experiment": True,
            "started_at": utc_now(),
            "experiment_ids": self.experiment_ids,
            "execution_order": self.execution_order(),
            "runs_per_experiment": RUNS_PER_EXPERIMENT,
            "settle_seconds": SETTLE_SECONDS,
            "max_attempts_per_repetition": MAX_ATTEMPTS_PER_REPETITION,
            "target_url": TARGET_URL,
            "object_size_bytes": EXPECTED_SIZE,
            "network_profile": "wan-no-loss",
            "target_rtt_ms": 50,
            "configured_loss_percent": 0,
            "flow": "client-bench (172.20.0.50) -> server-http3 (172.20.0.30)",
            "e06_method": "separate curl processes + exported TLS session + --tls-earlydata",
            "e06_proof": [
                "different local UDP ports",
                "curl trace: SSL reusing session with ALPN h3",
                "tls_earlydata > 0",
                "Caddy tls early_data matcher response header=accepted",
            ],
            "ticket_storage": "ephemeral /tmp inside client; never copied to host",
            "curl": curl_version.strip().splitlines(),
            "caddy": caddy_version,
            "kernel": self.docker("exec", "-T", CLIENT_SERVICE, "uname", "-r").stdout.strip(),
            "architecture": self.docker("exec", "-T", CLIENT_SERVICE, "uname", "-m").stdout.strip(),
            "docker_engine": self.run_command(
                ["docker", "version", "--format", "{{.Server.Version}}"]
            ).stdout.strip(),
            "docker_compose": self.run_command(
                ["docker", "compose", "version", "--short"]
            ).stdout.strip(),
            "containers": {
                service: self.docker("ps", "-q", service).stdout.strip()
                for service in (CLIENT_SERVICE, "server-tcp", SERVER_SERVICE)
            },
            "host_platform": platform.platform(),
            "git_commit": git_commit or None,
            "git_dirty": git_dirty,
            "automation_sha256": self.source_hash(),
        }

    def batch_directory(self, experiment: Experiment) -> Path:
        return RAW_ROOT / experiment.directory / self.batch_id

    def capture_qdisc(self) -> str:
        client = self.network_script("inspect_profile.sh", CLIENT_SERVICE).stdout
        server = self.network_script("inspect_profile.sh", SERVER_SERVICE).stdout
        evidence = f"{client.rstrip()}\n{server.rstrip()}\n"
        validate_qdisc_evidence(evidence)
        return evidence

    def execute_attempt(
        self, experiment: Experiment, repetition: int, attempt_number: int
    ) -> dict[str, Any]:
        batch_dir = self.batch_directory(experiment)
        working = batch_dir / f".working_run_{repetition:02d}_attempt_{attempt_number:02d}"
        working.mkdir(parents=True, exist_ok=False)
        started_at = utc_now()
        token = f"{self.batch_id}_{experiment.experiment_id.lower()}_{repetition}_{attempt_number}_{uuid.uuid4().hex}"
        metadata: dict[str, Any] = {
            "batch_id": self.batch_id,
            "experiment_id": experiment.experiment_id,
            "repetition": repetition,
            "attempt": attempt_number,
            "protocol_requested": experiment.protocol_requested,
            "connection_mode": experiment.connection_mode,
            "network_profile": "wan-no-loss",
            "target_rtt_ms": 50,
            "configured_loss_percent": 0,
            "timestamp": started_at,
            "status": "invalid",
            "official_experiment": True,
        }
        error: BaseException | None = None
        measured: tuple[int, str, str, str, dict[str, Any]] | None = None
        prep: tuple[int, str, str, str, dict[str, Any]] | None = None
        qdisc_path = working / "qdisc.txt"
        apply_path = working / "profile-apply.txt"

        try:
            self.ensure_no_client_curl()
            self.reset_profiles()
            applied = self.network_script("apply_profile.sh", "wan-no-loss", SERVER_SERVICE)
            apply_path.write_text(applied.stdout, encoding="utf-8")
            qdisc = self.capture_qdisc()
            qdisc_path.write_text(qdisc, encoding="utf-8")
            time.sleep(SETTLE_SECONDS)

            if experiment.experiment_id == "E06":
                pair = self.run_e06_pair(token)
                prep = pair["prep"]
                measured = pair["measured"]
                metrics = dict(pair["measured_metrics"])
                metadata.update(
                    {
                        "session_resumed": pair["session_resumed"],
                        "early_data_status": pair["early_data_status"],
                        "ticket_size_bytes": pair["ticket_size_bytes"],
                        "ticket_persisted_to_repository": False,
                        "preparation_is_official_sample": False,
                        "separate_curl_processes": True,
                        "prep_local_port": pair["prep_metrics"]["local_port"],
                        "measured_local_port": pair["measured_metrics"]["local_port"],
                    }
                )
            else:
                measured = self.execute_curl(experiment, token)
                metrics = parse_curl_document(measured[4], experiment.protocol_requested)
                validate_cold_evidence(metrics, measured[2], measured[3])
                metadata.update(
                    {"session_resumed": "not_used", "early_data_status": "not_used"}
                )

            metadata.update(metrics)
            metadata.update({"status": "valid", "completed_at": utc_now()})
        except BaseException as exc:
            error = exc
            metadata["failure_reason"] = str(exc)
            metadata["completed_at"] = utc_now()
            (working / "failure.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        finally:
            cleanup = self.reset_profiles(check=False)
            metadata["cleanup_exit_code"] = cleanup.returncode
            metadata["cleanup_output"] = cleanup.stdout
            if cleanup.returncode != 0:
                cleanup_error = BlockBError("limpeza segura falhou após a tentativa")
                metadata["failure_reason"] = (
                    f"{metadata.get('failure_reason', '')}; {cleanup_error}".strip("; ")
                )
                metadata["status"] = "invalid"
                error = cleanup_error

        if measured is not None:
            (working / "curl.json").write_text(measured[1], encoding="utf-8")
            (working / "curl.trace.txt").write_text(measured[2], encoding="utf-8")
            (working / "curl.headers.txt").write_text(measured[3], encoding="utf-8")
        if prep is not None:
            (working / "prep.curl.json").write_text(prep[1], encoding="utf-8")
            (working / "prep.curl.trace.txt").write_text(prep[2], encoding="utf-8")
            (working / "prep.curl.headers.txt").write_text(prep[3], encoding="utf-8")
        write_json_atomic(working / "metadata.json", metadata)

        if error is not None or metadata["status"] != "valid":
            attempts_dir = batch_dir / "attempts"
            attempts_dir.mkdir(parents=True, exist_ok=True)
            destination = attempts_dir / f"run_{repetition:02d}_attempt_{attempt_number:02d}_invalid"
            working.rename(destination)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise error
            raise BlockBError(str(error or metadata.get("failure_reason", "tentativa inválida")))

        suffix_sources = {
            "json": working / "curl.json",
            "trace.txt": working / "curl.trace.txt",
            "headers.txt": working / "curl.headers.txt",
            "metadata.json": working / "metadata.json",
            "qdisc.txt": qdisc_path,
            "profile-apply.txt": apply_path,
        }
        if experiment.experiment_id == "E06":
            suffix_sources.update(
                {
                    "prep.json": working / "prep.curl.json",
                    "prep.trace.txt": working / "prep.curl.trace.txt",
                    "prep.headers.txt": working / "prep.curl.headers.txt",
                }
            )
        destinations = {
            source: batch_dir / f"run_{repetition:02d}.{suffix}"
            for suffix, source in suffix_sources.items()
        }
        ensure_paths_absent(destinations.values())
        for source, destination in destinations.items():
            source.rename(destination)
        working.rmdir()

        relative = lambda path: path.relative_to(PROJECT_ROOT).as_posix()
        return {
            "batch_id": self.batch_id,
            "experiment_id": experiment.experiment_id,
            "repetition": repetition,
            "protocol_requested": experiment.protocol_requested,
            "protocol_confirmed": metadata["protocol_confirmed"],
            "connection_mode": experiment.connection_mode,
            "session_resumed": metadata["session_resumed"],
            "early_data_status": metadata["early_data_status"],
            "tls_earlydata_bytes": metadata["tls_earlydata_bytes"],
            "network_profile": "wan-no-loss",
            "target_rtt_ms": 50,
            "configured_loss_percent": 0,
            "ttfb_ms": metadata["ttfb_ms"],
            "time_namelookup_ms": metadata["time_namelookup_ms"],
            "time_connect_ms": metadata["time_connect_ms"],
            "time_appconnect_ms": metadata["time_appconnect_ms"],
            "time_pretransfer_ms": metadata["time_pretransfer_ms"],
            "time_total_ms": metadata["time_total_ms"],
            "num_connects": metadata["num_connects"],
            "response_code": metadata["response_code"],
            "size_download_bytes": metadata["size_download_bytes"],
            "local_port": metadata["local_port"],
            "timestamp": started_at,
            "exit_code": metadata["exit_code"],
            "status": "valid",
            "raw_json_path": relative(destinations[suffix_sources["json"]]),
            "metadata_path": relative(destinations[suffix_sources["metadata.json"]]),
            "trace_path": relative(destinations[suffix_sources["trace.txt"]]),
            "headers_path": relative(destinations[suffix_sources["headers.txt"]]),
            "qdisc_evidence_path": relative(destinations[suffix_sources["qdisc.txt"]]),
        }

    def run_repetition(self, experiment: Experiment, repetition: int) -> dict[str, Any]:
        for attempt in range(1, MAX_ATTEMPTS_PER_REPETITION + 1):
            print(
                f"[MEDIÇÃO] {experiment.experiment_id} repetição {repetition}/5 "
                f"tentativa {attempt}/{MAX_ATTEMPTS_PER_REPETITION}"
            )
            try:
                row = self.execute_attempt(experiment, repetition, attempt)
                print(
                    f"[OK] {experiment.experiment_id}/{repetition}: "
                    f"TTFB={float(row['ttfb_ms']):.3f} ms, "
                    f"protocolo={row['protocol_confirmed']}, "
                    f"early_data={row['tls_earlydata_bytes']}"
                )
                return row
            except BlockBError as exc:
                print(f"[FALHA REGISTRADA] {experiment.experiment_id}/{repetition}: {exc}")
                if "limpeza segura falhou" in str(exc):
                    raise
        raise BlockBError(
            f"{experiment.experiment_id}/{repetition} não obteve amostra válida após "
            f"{MAX_ATTEMPTS_PER_REPETITION} tentativas"
        )

    def write_processed_outputs(self) -> None:
        batch_csv = self.processed_batch_dir / "block_b_measurements.csv"
        write_csv(batch_csv, self.rows)
        merge_measurements_csv(PROCESSED_ROOT / "block_b_measurements.csv", self.rows)
        summaries = summarize_rows(self.rows)
        means = {item["experiment_id"]: float(item["mean_ttfb_ms"]) for item in summaries}
        comparisons: dict[str, dict[str, float]] = {}
        for first, second in (("E04", "E05"), ("E04", "E06"), ("E05", "E06")):
            if first in means and second in means:
                comparisons[f"{second.lower()}_vs_{first.lower()}"] = {
                    "absolute_difference_ms": means[second] - means[first],
                    "percent_difference": percentage_difference(means[second], means[first]),
                }
        write_json_atomic(
            self.processed_batch_dir / "summary.json",
            {
                "batch_id": self.batch_id,
                "formula": "sample_stdev = sqrt(sum((x - mean)^2) / (n - 1))",
                "summaries": summaries,
                "comparisons": comparisons,
            },
        )
        fields = [
            "experiment_id", "protocol", "connection_mode", "valid_samples",
            "mean_ttfb_ms", "sample_stdev_ttfb_ms", "min_ttfb_ms", "max_ttfb_ms",
        ]
        summary_path = self.processed_batch_dir / "summary.csv"
        with summary_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(summaries)

    def validate_final_integrity(self) -> None:
        expected_count = len(self.experiment_ids) * RUNS_PER_EXPERIMENT
        if len(self.rows) != expected_count:
            raise BlockBError(f"{len(self.rows)} linhas válidas; esperado {expected_count}")
        keys = {(row["experiment_id"], int(row["repetition"])) for row in self.rows}
        if len(keys) != expected_count:
            raise BlockBError("duplicação detectada nas amostras finais")
        for experiment_id in self.experiment_ids:
            selected = [row for row in self.rows if row["experiment_id"] == experiment_id]
            if len(selected) != RUNS_PER_EXPERIMENT:
                raise BlockBError(f"{experiment_id} não possui cinco amostras")
            for row in selected:
                document = load_curl_json((PROJECT_ROOT / str(row["raw_json_path"])).read_text(encoding="utf-8"))
                parse_curl_document(document, EXPERIMENTS[experiment_id].protocol_requested)
                if experiment_id == "E06":
                    if row["session_resumed"] != "confirmed" or row["early_data_status"] != "confirmed":
                        raise BlockBError("E06 sem confirmação integral de resumption/0-RTT")

    def run(self) -> int:
        self.acquire_locks()
        self.manifest = {
            "batch_id": self.batch_id,
            "status": "preflight",
            "official_experiment": not self.preflight_only,
            "started_at": utc_now(),
        }
        failure: BaseException | None = None
        try:
            self.preflight()
            if self.preflight_only:
                print("[RESUMO] Pré-validação concluída; nenhum resultado oficial foi criado.")
                return 0
            for experiment_id in self.experiment_ids:
                self.batch_directory(EXPERIMENTS[experiment_id]).mkdir(parents=True, exist_ok=False)
            self.manifest = self.collect_manifest()
            write_json_atomic(self.manifest_path, self.manifest)
            for job in self.execution_order():
                experiment = EXPERIMENTS[str(job["experiment_id"])]
                self.rows.append(self.run_repetition(experiment, int(job["repetition"])))
            self.validate_final_integrity()
            self.write_processed_outputs()
            self.manifest.update(
                {
                    "status": "complete",
                    "completed_at": utc_now(),
                    "valid_measurements": len(self.rows),
                    "batch_csv": (self.processed_batch_dir / "block_b_measurements.csv").relative_to(PROJECT_ROOT).as_posix(),
                    "summary_json": (self.processed_batch_dir / "summary.json").relative_to(PROJECT_ROOT).as_posix(),
                }
            )
            write_json_atomic(self.manifest_path, self.manifest)
            print(f"[RESUMO] Lote oficial completo: {self.batch_id}; amostras={len(self.rows)}")
            return 0
        except BaseException as exc:
            failure = exc
            self.manifest.update({"status": "failed", "failed_at": utc_now(), "failure_reason": str(exc)})
            if not self.preflight_only:
                write_json_atomic(self.manifest_path, self.manifest)
            raise
        finally:
            cleanup = self.reset_profiles(check=False)
            if cleanup.returncode != 0:
                print(f"[ERRO] Limpeza final falhou:\n{cleanup.stdout}", file=sys.stderr)
                if failure is None:
                    self.release_locks()
                    raise BlockBError("limpeza final das qdiscs falhou")
            self.release_locks()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Executa o Bloco B oficial (E04–E06).")
    parser.add_argument(
        "--experiment", choices=sorted(EXPERIMENTS),
        help="executa somente um experimento com cinco repetições",
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="valida o ambiente e o 0-RTT sem criar medições oficiais",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    selected = [arguments.experiment] if arguments.experiment else list(EXPERIMENTS)
    runner = BlockBRunner(selected, preflight_only=arguments.preflight)

    def interrupt_handler(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("execução interrompida")

    signal.signal(signal.SIGTERM, interrupt_handler)
    try:
        return runner.run()
    except KeyboardInterrupt:
        print("[ERRO] Execução interrompida; limpeza segura solicitada.", file=sys.stderr)
        return 130
    except BlockBError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

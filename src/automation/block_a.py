#!/usr/bin/env python3
"""Orquestração reproduzível dos experimentos TCP oficiais E01–E03."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.automation.locking import assert_n3_access


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = PROJECT_ROOT / "results"
RAW_ROOT = RESULTS_ROOT / "raw" / "block_a"
PROCESSED_ROOT = RESULTS_ROOT / "processed"
LOCK_PATH = RESULTS_ROOT / ".block_a.lock"
TCP_SERVER_IP = "172.20.0.10"
RUNS_PER_EXPERIMENT = 5
RUN_DURATION_SECONDS = 15
SETTLE_SECONDS = 2
SOCKET_CAPTURE_SECONDS = 2
MAX_ATTEMPTS_PER_REPETITION = 3


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    directory: str
    algorithm: str
    profile: str
    target_rtt_ms: int
    configured_loss_percent: int


EXPERIMENTS: dict[str, Experiment] = {
    "E01": Experiment("E01", "e01_tcp_cubic_lan", "cubic", "lan", 1, 0),
    "E02": Experiment(
        "E02", "e02_tcp_cubic_wan_degraded", "cubic", "wan-degraded", 50, 2
    ),
    "E03": Experiment(
        "E03", "e03_tcp_bbr_wan_degraded", "bbr", "wan-degraded", 50, 2
    ),
}

CSV_FIELDS = [
    "batch_id",
    "experiment_id",
    "repetition",
    "algorithm_requested",
    "algorithm_confirmed",
    "network_profile",
    "target_rtt_ms",
    "configured_loss_percent",
    "duration_seconds",
    "bytes_sent",
    "throughput_sender_mbps",
    "throughput_receiver_mbps",
    "retransmissions",
    "exit_code",
    "status",
    "timestamp",
    "raw_json_path",
    "metadata_path",
    "socket_evidence_path",
    "qdisc_evidence_path",
]


class BlockAError(RuntimeError):
    """Erro de validação, execução ou integridade do Bloco A."""


class CommandError(BlockAError):
    """Falha de um comando externo."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_batch_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"block_a_{stamp}_{uuid.uuid4().hex[:8]}"


def experiment_for_id(experiment_id: str) -> Experiment:
    try:
        return EXPERIMENTS[experiment_id.upper()]
    except KeyError as exc:
        raise BlockAError(f"experimento desconhecido: {experiment_id}") from exc


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlockAError(f"JSON inválido em {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BlockAError(f"JSON em {path} não é um objeto")
    return value


def _required_number(mapping: Mapping[str, Any], field: str) -> float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BlockAError(f"campo numérico essencial ausente/inválido: {field}")
    return float(value)


def parse_iperf_document(
    document: Mapping[str, Any], expected_duration: float = RUN_DURATION_SECONDS
) -> dict[str, Any]:
    """Extrai e valida as métricas essenciais do JSON real do iperf3."""
    try:
        test_start = document["start"]["test_start"]
        sum_sent = document["end"]["sum_sent"]
        sum_received = document["end"]["sum_received"]
    except (KeyError, TypeError) as exc:
        raise BlockAError("execução incompleta: start/end/sum_sent/sum_received ausente") from exc

    if test_start.get("protocol") != "TCP":
        raise BlockAError("protocolo não é TCP")
    if int(test_start.get("num_streams", 0)) != 1:
        raise BlockAError("a execução não possui exatamente um fluxo")
    if int(test_start.get("reverse", 0)) != 0 or int(test_start.get("bidir", 0)) != 0:
        raise BlockAError("direção reversa ou bidirecional não permitida")
    if int(test_start.get("omit", 0)) != 0:
        raise BlockAError("período de omissão não permitido")
    if int(test_start.get("fqrate", 0)) != 0 or int(test_start.get("target_bitrate", 0)) != 0:
        raise BlockAError("limite artificial de vazão detectado")

    requested_duration = _required_number(test_start, "duration")
    duration = _required_number(sum_sent, "seconds")
    bytes_sent = int(_required_number(sum_sent, "bytes"))
    sender_bps = _required_number(sum_sent, "bits_per_second")
    receiver_bps = _required_number(sum_received, "bits_per_second")
    retransmits_value = sum_sent.get("retransmits")
    if isinstance(retransmits_value, bool) or not isinstance(retransmits_value, int):
        raise BlockAError("campo essencial retransmits ausente/inválido")

    if abs(requested_duration - expected_duration) > 0.001:
        raise BlockAError(
            f"duração solicitada {requested_duration} difere de {expected_duration} segundos"
        )
    if not expected_duration - 0.5 <= duration <= expected_duration + 1.5:
        raise BlockAError(f"duração efetiva incompatível: {duration} segundos")
    if bytes_sent <= 0 or sender_bps <= 0 or receiver_bps <= 0:
        raise BlockAError("execução sem bytes/vazão positivos")
    if retransmits_value < 0:
        raise BlockAError("retransmissões negativas")

    return {
        "duration_seconds": duration,
        "bytes_sent": bytes_sent,
        "throughput_sender_mbps": sender_bps / 1_000_000.0,
        "throughput_receiver_mbps": receiver_bps / 1_000_000.0,
        "retransmissions": retransmits_value,
    }


def parse_iperf_file(path: Path, expected_duration: float = RUN_DURATION_SECONDS) -> dict[str, Any]:
    return parse_iperf_document(load_json_file(path), expected_duration)


def parse_socket_evidence(text: str, expected_algorithm: str) -> dict[str, Any]:
    """Seleciona o socket de dados pelo maior bytes_sent e confirma seu algoritmo."""
    blocks: list[dict[str, Any]] = []
    current_header: str | None = None
    current_details: list[str] = []

    def finish_block() -> None:
        nonlocal current_header, current_details
        if current_header is None:
            return
        detail = " ".join(part.strip() for part in current_details)
        bytes_matches = [int(value) for value in re.findall(r"\bbytes_sent:(\d+)", detail)]
        algorithm_match = re.match(r"\s*([a-zA-Z0-9_]+)\b", detail)
        blocks.append(
            {
                "header": current_header,
                "detail": detail,
                "bytes_sent": max(bytes_matches, default=0),
                "algorithm": algorithm_match.group(1).lower() if algorithm_match else None,
            }
        )
        current_header = None
        current_details = []

    for line in text.splitlines():
        if line.startswith("ESTAB ") or line.startswith("ESTAB\t"):
            finish_block()
            current_header = line
        elif current_header is not None:
            current_details.append(line)
    finish_block()

    if not blocks:
        raise BlockAError("ss não encontrou sockets TCP estabelecidos do iperf3")
    data_socket = max(blocks, key=lambda item: item["bytes_sent"])
    if data_socket["bytes_sent"] < 1_000_000:
        raise BlockAError("ss não identificou um socket com volume compatível com dados")
    if data_socket["algorithm"] != expected_algorithm.lower():
        raise BlockAError(
            "algoritmo do socket de dados não corresponde ao solicitado: "
            f"esperado={expected_algorithm}, observado={data_socket['algorithm']}"
        )
    return data_socket


def validate_qdisc_evidence(text: str, experiment: Experiment) -> None:
    """Confirma perfil, handle, atraso e perda nos dois serviços do fluxo TCP."""
    marker = f"active_profile={experiment.profile}"
    if text.count(marker) != 2:
        raise BlockAError(f"perfil {experiment.profile} não confirmado nos dois serviços")
    if text.count("qdisc netem 1a2b: root") != 2:
        raise BlockAError("hierarquia root netem gerenciada não confirmada duas vezes")

    sections = text.split("=== server-tcp ===")
    if len(sections) != 2:
        raise BlockAError("evidência de qdisc não separa cliente e servidor TCP")
    client, server = sections
    if experiment.profile == "lan":
        if "delay 500us" not in client or "delay 500us" not in server:
            raise BlockAError("atraso LAN de 500us por direção não confirmado")
        if " loss " in client or " loss " in server:
            raise BlockAError("perda inesperada no perfil LAN")
    elif experiment.profile == "wan-degraded":
        if "delay 25ms" not in client or "delay 25ms" not in server:
            raise BlockAError("atraso WAN de 25ms por direção não confirmado")
        if "loss 2%" not in client:
            raise BlockAError("perda de 2% no egress do cliente não confirmada")
        if " loss " in server:
            raise BlockAError("perda inesperada no egress do servidor")
    else:
        raise BlockAError(f"perfil não permitido no Bloco A: {experiment.profile}")


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
        raise BlockAError(f"recusa em sobrescrever resultado existente: {existing[0]}")


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
    """Preserva lotes anteriores e rejeita chaves repetidas."""
    existing: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != CSV_FIELDS:
                raise BlockAError(f"cabeçalho incompatível no CSV existente: {path}")
            existing = list(reader)

    combined: list[Mapping[str, Any]] = [*existing, *new_rows]
    seen: set[tuple[str, str, str]] = set()
    for row in combined:
        key = (str(row["batch_id"]), str(row["experiment_id"]), str(row["repetition"]))
        if key in seen:
            raise BlockAError(f"medição duplicada no CSV: {key}")
        seen.add(key)
    write_csv(path, combined)


def summarize_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for experiment_id in EXPERIMENTS:
        selected = [row for row in rows if row["experiment_id"] == experiment_id]
        if not selected:
            continue
        throughputs = [float(row["throughput_sender_mbps"]) for row in selected]
        summaries.append(
            {
                "experiment_id": experiment_id,
                "algorithm": selected[0]["algorithm_requested"],
                "network_profile": selected[0]["network_profile"],
                "valid_samples": len(selected),
                "mean_throughput_mbps": statistics.mean(throughputs),
                "sample_stdev_throughput_mbps": statistics.stdev(throughputs),
                "total_retransmissions": sum(int(row["retransmissions"]) for row in selected),
            }
        )
    return summaries


def percentage_difference(new_value: float, reference_value: float) -> float:
    if reference_value == 0:
        raise BlockAError("referência zero em diferença percentual")
    return (new_value - reference_value) / reference_value * 100.0


class BlockARunner:
    def __init__(self, experiment_ids: Sequence[str], preflight_only: bool = False):
        self.experiment_ids = [experiment_for_id(value).experiment_id for value in experiment_ids]
        self.preflight_only = preflight_only
        self.batch_id = make_batch_id()
        self.lock_acquired = False
        self.rows: list[dict[str, Any]] = []
        self.manifest_path = RAW_ROOT / "batches" / f"{self.batch_id}.manifest.json"
        self.processed_batch_dir = PROCESSED_ROOT / "block_a" / self.batch_id
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

    def acquire_lock(self) -> None:
        RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
        assert_n3_access(RESULTS_ROOT, BlockAError)
        try:
            descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            owner = LOCK_PATH.read_text(encoding="utf-8", errors="replace").strip()
            raise BlockAError(
                f"outra medição ou lock residual existe em {LOCK_PATH}: {owner}"
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} batch_id={self.batch_id} started={utc_now()}\n")
        self.lock_acquired = True

    def release_lock(self) -> None:
        if self.lock_acquired:
            try:
                LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
            self.lock_acquired = False

    def reset_profiles(self, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.network_script("reset_profile.sh", "--all", check=check)

    def ensure_no_client_iperf(self) -> None:
        result = self.docker(
            "exec",
            "-T",
            "client-bench",
            "sh",
            "-c",
            "if pidof iperf3 >/dev/null 2>&1; then pidof iperf3; exit 1; fi",
            check=False,
        )
        if result.returncode != 0:
            raise BlockAError(f"iperf3 residual ou concorrente no cliente: {result.stdout.strip()}")

    def available_algorithms(self) -> str:
        return self.docker(
            "exec",
            "-T",
            "client-bench",
            "sysctl",
            "-n",
            "net.ipv4.tcp_available_congestion_control",
        ).stdout.strip()

    def preflight(self) -> None:
        print("[PRÉ-VALIDAÇÃO] Verificando Docker, serviços, algoritmos e estado de rede...")
        self.run_command(["docker", "info"], timeout=60)
        self.docker("config", "--quiet")
        running = set(
            self.docker("ps", "--services", "--filter", "status=running").stdout.splitlines()
        )
        expected = {"client-bench", "server-tcp", "server-http3"}
        if running != expected:
            raise BlockAError(f"serviços em execução {running}; esperado {expected}")
        self.ensure_no_client_iperf()
        self.docker("exec", "-T", "client-bench", "ss", "-V")
        self.docker("exec", "-T", "client-bench", "nc", "-z", "-w", "3", "server-tcp", "5201")

        algorithms = self.available_algorithms().split()
        if "bbr" not in algorithms:
            print("[PRÉ-VALIDAÇÃO] Solicitando BBR em um socket diagnóstico de 1 segundo...")
            self.docker(
                "exec",
                "-T",
                "client-bench",
                "iperf3",
                "-c",
                TCP_SERVER_IP,
                "-t",
                "1",
                "-C",
                "bbr",
                "-J",
                timeout=15,
            )
            algorithms = self.available_algorithms().split()
        if not {"cubic", "bbr"}.issubset(algorithms):
            raise BlockAError(f"algoritmos insuficientes: {algorithms}")

        self.reset_profiles()
        inspection = self.network_script("inspect_profile.sh", "--all").stdout
        if inspection.count("ownership=default") != 3 or inspection.count("active_profile=none") != 3:
            raise BlockAError(f"estado inicial de qdisc inesperado:\n{inspection}")
        print("[OK] Pré-validação leve aprovada; nenhuma medição oficial iniciada.")

    def source_hash(self) -> str:
        digest = hashlib.sha256()
        for relative in (
            "src/automation/block_a.py",
            "scripts/experiments/run_block_a.sh",
        ):
            digest.update((PROJECT_ROOT / relative).read_bytes())
        return digest.hexdigest()

    def collect_manifest(self) -> dict[str, Any]:
        git_commit = self.run_command(["git", "rev-parse", "HEAD"], check=False).stdout.strip()
        git_dirty = bool(self.run_command(["git", "status", "--porcelain"], check=False).stdout.strip())
        client_version = self.docker(
            "exec", "-T", "client-bench", "iperf3", "--version"
        ).stdout.splitlines()[0]
        return {
            "batch_id": self.batch_id,
            "status": "running",
            "official_experiment": True,
            "started_at": utc_now(),
            "experiment_ids": self.experiment_ids,
            "execution_order": self.execution_order(),
            "runs_per_experiment": RUNS_PER_EXPERIMENT,
            "duration_seconds": RUN_DURATION_SECONDS,
            "settle_seconds": SETTLE_SECONDS,
            "socket_capture_seconds": SOCKET_CAPTURE_SECONDS,
            "max_attempts_per_repetition": MAX_ATTEMPTS_PER_REPETITION,
            "flow": "client-bench (172.20.0.50) -> server-tcp (172.20.0.10)",
            "parallel_streams": 1,
            "iperf_flags": ["-c", TCP_SERVER_IP, "-t", "15", "-C", "<algorithm>", "-J"],
            "qdisc_method": "root netem handle 1a2b via scripts/network; no sch_fq",
            "kernel": self.docker("exec", "-T", "client-bench", "uname", "-r").stdout.strip(),
            "architecture": self.docker("exec", "-T", "client-bench", "uname", "-m").stdout.strip(),
            "docker_engine": self.run_command(
                ["docker", "version", "--format", "{{.Server.Version}}"]
            ).stdout.strip(),
            "docker_compose": self.run_command(
                ["docker", "compose", "version", "--short"]
            ).stdout.strip(),
            "iperf3": client_version,
            "available_congestion_control": self.available_algorithms(),
            "default_congestion_control": self.docker(
                "exec", "-T", "client-bench", "sysctl", "-n", "net.ipv4.tcp_congestion_control"
            ).stdout.strip(),
            "containers": {
                service: self.docker("ps", "-q", service).stdout.strip()
                for service in ("client-bench", "server-tcp", "server-http3")
            },
            "host_platform": platform.platform(),
            "git_commit": git_commit or None,
            "git_dirty": git_dirty,
            "automation_sha256": self.source_hash(),
        }

    def execution_order(self) -> list[dict[str, Any]]:
        selected = set(self.experiment_ids)
        order: list[dict[str, Any]] = []
        if "E01" in selected:
            order.extend({"experiment_id": "E01", "repetition": value} for value in range(1, 6))
        if {"E02", "E03"}.issubset(selected):
            for repetition in range(1, 6):
                order.append({"experiment_id": "E02", "repetition": repetition})
                order.append({"experiment_id": "E03", "repetition": repetition})
        else:
            for experiment_id in ("E02", "E03"):
                if experiment_id in selected:
                    order.extend(
                        {"experiment_id": experiment_id, "repetition": value}
                        for value in range(1, 6)
                    )
        return order

    def batch_directory(self, experiment: Experiment) -> Path:
        return RAW_ROOT / experiment.directory / self.batch_id

    def capture_qdisc(self, experiment: Experiment) -> str:
        client = self.network_script("inspect_profile.sh", "client-bench").stdout
        server = self.network_script("inspect_profile.sh", "server-tcp").stdout
        evidence = f"{client.rstrip()}\n{server.rstrip()}\n"
        validate_qdisc_evidence(evidence, experiment)
        return evidence

    def stop_residual_iperf(self) -> None:
        self.docker(
            "exec",
            "-T",
            "client-bench",
            "sh",
            "-c",
            'pids="$(pidof iperf3 2>/dev/null || true)"; [ -z "$pids" ] || kill $pids',
            check=False,
        )

    def execute_iperf(
        self, algorithm: str, json_path: Path, stderr_path: Path, ss_path: Path
    ) -> tuple[int, dict[str, Any]]:
        command = [
            *self.docker_base,
            "exec",
            "-T",
            "client-bench",
            "iperf3",
            "-c",
            TCP_SERVER_IP,
            "-t",
            str(RUN_DURATION_SECONDS),
            "-C",
            algorithm,
            "-J",
        ]
        with json_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env={**os.environ, "MSYS_NO_PATHCONV": "1"},
            )
            try:
                time.sleep(SOCKET_CAPTURE_SECONDS)
                socket_output = self.docker(
                    "exec",
                    "-T",
                    "client-bench",
                    "ss",
                    "-tinp",
                    "dst",
                    f"{TCP_SERVER_IP}:5201",
                ).stdout
                ss_path.write_text(socket_output, encoding="utf-8")
                socket = parse_socket_evidence(socket_output, algorithm)
                return_code = process.wait(timeout=RUN_DURATION_SECONDS + 15)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                self.stop_residual_iperf()
                raise
        return return_code, socket

    def execute_attempt(
        self, experiment: Experiment, repetition: int, attempt_number: int
    ) -> dict[str, Any]:
        batch_dir = self.batch_directory(experiment)
        working = batch_dir / f".working_run_{repetition:02d}_attempt_{attempt_number:02d}"
        working.mkdir(parents=True, exist_ok=False)
        started_at = utc_now()
        metadata: dict[str, Any] = {
            "batch_id": self.batch_id,
            "experiment_id": experiment.experiment_id,
            "repetition": repetition,
            "attempt": attempt_number,
            "algorithm_requested": experiment.algorithm,
            "network_profile": experiment.profile,
            "target_rtt_ms": experiment.target_rtt_ms,
            "configured_loss_percent": experiment.configured_loss_percent,
            "duration_requested_seconds": RUN_DURATION_SECONDS,
            "settle_seconds": SETTLE_SECONDS,
            "timestamp": started_at,
            "status": "invalid",
            "official_experiment": True,
        }
        json_path = working / "iperf3.json"
        stderr_path = working / "iperf3.stderr.txt"
        ss_path = working / "socket.ss.txt"
        qdisc_path = working / "qdisc.txt"
        apply_path = working / "profile-apply.txt"
        error: BaseException | None = None

        try:
            self.ensure_no_client_iperf()
            self.reset_profiles()
            apply_result = self.network_script(
                "apply_profile.sh", experiment.profile, "server-tcp"
            )
            apply_path.write_text(apply_result.stdout, encoding="utf-8")
            qdisc = self.capture_qdisc(experiment)
            qdisc_path.write_text(qdisc, encoding="utf-8")
            time.sleep(SETTLE_SECONDS)

            return_code, socket = self.execute_iperf(
                experiment.algorithm, json_path, stderr_path, ss_path
            )
            metadata["exit_code"] = return_code
            if return_code != 0:
                raise BlockAError(f"iperf3 terminou com código {return_code}")
            if stderr_path.read_text(encoding="utf-8", errors="replace").strip():
                raise BlockAError("iperf3 registrou conteúdo no stderr")

            metrics = parse_iperf_file(json_path)
            metadata.update(metrics)
            metadata.update(
                {
                    "algorithm_confirmed": socket["algorithm"],
                    "socket_local_remote": socket["header"],
                    "socket_bytes_sent_at_capture": socket["bytes_sent"],
                    "status": "valid",
                    "completed_at": utc_now(),
                }
            )
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
                cleanup_error = BlockAError("limpeza segura falhou após a tentativa")
                metadata["failure_reason"] = (
                    f"{metadata.get('failure_reason', '')}; {cleanup_error}".strip("; ")
                )
                metadata["status"] = "invalid"
                error = cleanup_error

        write_json_atomic(working / "metadata.json", metadata)
        if error is not None or metadata["status"] != "valid":
            attempts_dir = batch_dir / "attempts"
            attempts_dir.mkdir(parents=True, exist_ok=True)
            destination = attempts_dir / f"run_{repetition:02d}_attempt_{attempt_number:02d}_invalid"
            working.rename(destination)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise error
            raise BlockAError(str(error or metadata.get("failure_reason", "tentativa inválida")))

        destinations = {
            json_path: batch_dir / f"run_{repetition:02d}.json",
            stderr_path: batch_dir / f"run_{repetition:02d}.stderr.txt",
            ss_path: batch_dir / f"run_{repetition:02d}.ss.txt",
            qdisc_path: batch_dir / f"run_{repetition:02d}.qdisc.txt",
            apply_path: batch_dir / f"run_{repetition:02d}.profile-apply.txt",
            working / "metadata.json": batch_dir / f"run_{repetition:02d}.metadata.json",
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
            "algorithm_requested": experiment.algorithm,
            "algorithm_confirmed": metadata["algorithm_confirmed"],
            "network_profile": experiment.profile,
            "target_rtt_ms": experiment.target_rtt_ms,
            "configured_loss_percent": experiment.configured_loss_percent,
            "duration_seconds": metadata["duration_seconds"],
            "bytes_sent": metadata["bytes_sent"],
            "throughput_sender_mbps": metadata["throughput_sender_mbps"],
            "throughput_receiver_mbps": metadata["throughput_receiver_mbps"],
            "retransmissions": metadata["retransmissions"],
            "exit_code": metadata["exit_code"],
            "status": "valid",
            "timestamp": started_at,
            "raw_json_path": relative(destinations[json_path]),
            "metadata_path": relative(destinations[working / "metadata.json"]),
            "socket_evidence_path": relative(destinations[ss_path]),
            "qdisc_evidence_path": relative(destinations[qdisc_path]),
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
                    f"{row['throughput_sender_mbps']:.3f} Mbps, "
                    f"retransmissões={row['retransmissions']}, "
                    f"algoritmo={row['algorithm_confirmed']}"
                )
                return row
            except BlockAError as exc:
                print(f"[FALHA REGISTRADA] {experiment.experiment_id}/{repetition}: {exc}")
                if "limpeza segura falhou" in str(exc):
                    raise
        raise BlockAError(
            f"{experiment.experiment_id}/{repetition} não obteve amostra válida após "
            f"{MAX_ATTEMPTS_PER_REPETITION} tentativas"
        )

    def write_processed_outputs(self) -> None:
        batch_csv = self.processed_batch_dir / "block_a_measurements.csv"
        write_csv(batch_csv, self.rows)
        merge_measurements_csv(PROCESSED_ROOT / "block_a_measurements.csv", self.rows)

        summaries = summarize_rows(self.rows)
        means = {item["experiment_id"]: item["mean_throughput_mbps"] for item in summaries}
        comparisons: dict[str, float] = {}
        if "E01" in means and "E02" in means:
            comparisons["e02_vs_e01_percent"] = percentage_difference(means["E02"], means["E01"])
        if "E02" in means and "E03" in means:
            comparisons["e03_vs_e02_percent"] = percentage_difference(means["E03"], means["E02"])

        write_json_atomic(
            self.processed_batch_dir / "summary.json",
            {
                "batch_id": self.batch_id,
                "formula": "sample_stdev = sqrt(sum((x - mean)^2) / (n - 1))",
                "summaries": summaries,
                "comparisons": comparisons,
            },
        )
        summary_fields = [
            "experiment_id",
            "algorithm",
            "network_profile",
            "valid_samples",
            "mean_throughput_mbps",
            "sample_stdev_throughput_mbps",
            "total_retransmissions",
        ]
        summary_path = self.processed_batch_dir / "summary.csv"
        with summary_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=summary_fields)
            writer.writeheader()
            writer.writerows(summaries)

    def validate_final_integrity(self) -> None:
        expected_count = len(self.experiment_ids) * RUNS_PER_EXPERIMENT
        if len(self.rows) != expected_count:
            raise BlockAError(f"{len(self.rows)} linhas válidas; esperado {expected_count}")
        keys = {
            (row["experiment_id"], int(row["repetition"]))
            for row in self.rows
        }
        if len(keys) != expected_count:
            raise BlockAError("duplicação detectada nas amostras finais")
        for experiment_id in self.experiment_ids:
            experiment = experiment_for_id(experiment_id)
            selected = [row for row in self.rows if row["experiment_id"] == experiment_id]
            if len(selected) != RUNS_PER_EXPERIMENT:
                raise BlockAError(f"{experiment_id} não possui cinco amostras")
            for row in selected:
                if row["algorithm_confirmed"] != experiment.algorithm:
                    raise BlockAError(f"algoritmo incorreto na linha {row}")
                parse_iperf_file(PROJECT_ROOT / str(row["raw_json_path"]))

    def run(self) -> int:
        self.acquire_lock()
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
                self.batch_directory(experiment_for_id(experiment_id)).mkdir(
                    parents=True, exist_ok=False
                )
            self.manifest = self.collect_manifest()
            write_json_atomic(self.manifest_path, self.manifest)

            for job in self.execution_order():
                experiment = experiment_for_id(job["experiment_id"])
                self.rows.append(self.run_repetition(experiment, int(job["repetition"])))

            self.validate_final_integrity()
            self.write_processed_outputs()
            self.manifest.update(
                {
                    "status": "complete",
                    "completed_at": utc_now(),
                    "valid_measurements": len(self.rows),
                    "batch_csv": (
                        self.processed_batch_dir / "block_a_measurements.csv"
                    ).relative_to(PROJECT_ROOT).as_posix(),
                    "summary_json": (
                        self.processed_batch_dir / "summary.json"
                    ).relative_to(PROJECT_ROOT).as_posix(),
                }
            )
            write_json_atomic(self.manifest_path, self.manifest)
            print(f"[RESUMO] Lote oficial completo: {self.batch_id}; amostras={len(self.rows)}")
            return 0
        except BaseException as exc:
            failure = exc
            self.manifest.update(
                {"status": "failed", "failed_at": utc_now(), "failure_reason": str(exc)}
            )
            if not self.preflight_only:
                write_json_atomic(self.manifest_path, self.manifest)
            raise
        finally:
            self.stop_residual_iperf()
            cleanup = self.reset_profiles(check=False)
            if cleanup.returncode != 0:
                print(f"[ERRO] Limpeza final falhou:\n{cleanup.stdout}", file=sys.stderr)
                if failure is None:
                    self.release_lock()
                    raise BlockAError("limpeza final das qdiscs falhou")
            self.release_lock()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Executa o Bloco A oficial (E01–E03).")
    parser.add_argument(
        "--experiment",
        choices=sorted(EXPERIMENTS),
        help="executa somente um experimento com cinco repetições",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="valida o ambiente sem criar medições oficiais",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    selected = [arguments.experiment] if arguments.experiment else list(EXPERIMENTS)
    runner = BlockARunner(selected, preflight_only=arguments.preflight)

    def interrupt_handler(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("execução interrompida")

    signal.signal(signal.SIGTERM, interrupt_handler)
    try:
        return runner.run()
    except KeyboardInterrupt:
        print("[ERRO] Execução interrompida; limpeza segura solicitada.", file=sys.stderr)
        return 130
    except BlockAError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


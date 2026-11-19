#!/usr/bin/env python3
"""Executor integrado N3: auditoria offline por padrão e execução somente explícita."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.automation.audit_n3 import (
    OFFICIAL_BLOCK_A_BATCH,
    OFFICIAL_BLOCK_B_BATCH,
    AuditError,
    audit_batches,
    audit_block_a,
    audit_block_b,
    make_integration_id,
    utc_now,
    write_integration,
)
from src.automation.locking import N3_TOKEN_ENV


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = PROJECT_ROOT / "results"
INTEGRATED_ROOT = RESULTS_ROOT / "integrated"
N3_LOCK_PATH = RESULTS_ROOT / ".n3.lock"
BLOCK_LOCK_PATHS = (RESULTS_ROOT / ".block_a.lock", RESULTS_ROOT / ".block_b.lock")


class N3Error(RuntimeError):
    """Falha de coordenação, execução, limpeza ou consolidação N3."""


class N3Lock:
    """Lock global com token que autoriza os runners filhos sem deadlock."""

    def __init__(self, results_root: Path = RESULTS_ROOT):
        self.results_root = results_root
        self.path = results_root / ".n3.lock"
        self.block_paths = (results_root / ".block_a.lock", results_root / ".block_b.lock")
        self.token = uuid.uuid4().hex
        self.acquired = False

    def acquire(self) -> None:
        self.results_root.mkdir(parents=True, exist_ok=True)
        document = {
            "pid": os.getpid(),
            "token": self.token,
            "started_at": utc_now(),
        }
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            owner = self.path.read_text(encoding="utf-8", errors="replace").strip()
            raise N3Error(f"outra execução integrada ou lock residual existe: {owner}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        self.acquired = True
        existing = [path for path in self.block_paths if path.exists()]
        if existing:
            self.release()
            raise N3Error(f"runner individual já está em execução ou deixou lock residual: {existing[0]}")

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.acquired = False


def automation_version(root: Path) -> str:
    digest = hashlib.sha256()
    relatives = (
        "run_n3.sh",
        "scripts/experiments/run_n3.sh",
        "src/automation/run_n3.py",
        "src/automation/audit_n3.py",
        "src/automation/locking.py",
        "src/automation/block_a.py",
        "src/automation/block_b.py",
    )
    for relative in relatives:
        path = root / relative
        if not path.is_file():
            raise N3Error(f"arquivo da automação ausente: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def git_commit(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def complete_batches(root: Path, block: str) -> set[str]:
    directory = root / "results" / "raw" / block / "batches"
    result: set[str] = set()
    if not directory.is_dir():
        return result
    for path in directory.glob("*.manifest.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("status") == "complete" and document.get("batch_id"):
            result.add(str(document["batch_id"]))
    return result


class N3Executor:
    def __init__(
        self,
        *,
        root: Path,
        mode: str,
        block_a_batch: str,
        block_b_batch: str,
        integration_id: str | None = None,
    ):
        self.root = root.resolve()
        self.mode = mode
        self.block_a_batch = block_a_batch
        self.block_b_batch = block_b_batch
        self.integration_id = integration_id or make_integration_id(mode)
        self.lock = N3Lock(self.root / "results")
        self.cleanup_required = False
        self.failure_context: dict[str, Any] = {}

    def run_process(
        self,
        command: Sequence[str],
        *,
        timeout: int | None = None,
        child_runner: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "MSYS_NO_PATHCONV": "1"}
        if child_runner:
            environment[N3_TOKEN_ENV] = self.lock.token
        result = subprocess.run(
            list(command),
            cwd=self.root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=environment,
        )
        if result.returncode != 0:
            raise N3Error(
                f"comando falhou ({result.returncode}): {' '.join(command)}\n{result.stdout}"
            )
        return result

    def reset_profiles(self) -> str:
        return self.run_process(
            ["sh", "scripts/network/reset_profile.sh", "--all"], timeout=180
        ).stdout

    def validate_execute_environment(self) -> str:
        return self.run_process(
            ["sh", "scripts/validation/validate-infrastructure.sh"], timeout=180
        ).stdout

    def run_block(self, block: str) -> tuple[str, str]:
        if block == "A":
            script = "scripts/experiments/run_block_a.sh"
            manifest_block = "block_a"
        elif block == "B":
            script = "scripts/experiments/run_block_b.sh"
            manifest_block = "block_b"
        else:
            raise N3Error(f"bloco desconhecido: {block}")
        before = complete_batches(self.root, manifest_block)
        result = self.run_process(["sh", script], child_runner=True)
        after = complete_batches(self.root, manifest_block)
        created = sorted(after - before)
        if len(created) != 1:
            raise N3Error(
                f"runner do Bloco {block} deveria criar exatamente um lote completo; criou={created}"
            )
        return created[0], result.stdout

    def record_execute_failure(self, exc: BaseException) -> None:
        failed_root = self.root / "results" / "integrated" / "failed"
        failed_root.mkdir(parents=True, exist_ok=True)
        path = failed_root / f"{self.integration_id}.json"
        if path.exists():
            return
        document = {
            "integration_id": self.integration_id,
            "mode": self.mode,
            "status": "failed",
            "failed_at": utc_now(),
            "error_type": type(exc).__name__,
            "failure_reason": str(exc),
            "context": self.failure_context,
        }
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def consolidate(self, block_a_batch: str, block_b_batch: str) -> Path:
        audited = audit_batches(self.root, block_a_batch, block_b_batch)
        return write_integration(
            self.root,
            audited,
            self.integration_id,
            self.mode,
            automation_version(self.root),
            git_commit(self.root),
        )

    def verify_existing(self) -> Path:
        return self.consolidate(self.block_a_batch, self.block_b_batch)

    def execute(self) -> Path:
        self.cleanup_required = True
        self.failure_context["infrastructure_validation"] = self.validate_execute_environment()
        self.failure_context["initial_cleanup"] = self.reset_profiles()

        new_block_a, block_a_log = self.run_block("A")
        self.failure_context.update({"block_a_batch_id": new_block_a, "block_a_log": block_a_log})
        audit_block_a(self.root, new_block_a)
        self.failure_context["between_blocks_cleanup"] = self.reset_profiles()

        new_block_b, block_b_log = self.run_block("B")
        self.failure_context.update({"block_b_batch_id": new_block_b, "block_b_log": block_b_log})
        audit_block_b(self.root, new_block_b)
        self.failure_context["final_cleanup"] = self.reset_profiles()
        self.cleanup_required = False
        return self.consolidate(new_block_a, new_block_b)

    def run(self) -> Path:
        self.lock.acquire()
        failure: BaseException | None = None
        try:
            if self.mode == "verify-existing":
                return self.verify_existing()
            if self.mode == "execute":
                return self.execute()
            raise N3Error(f"modo inválido: {self.mode}")
        except BaseException as exc:
            failure = exc
            if self.mode == "execute":
                self.record_execute_failure(exc)
            raise
        finally:
            cleanup_error: BaseException | None = None
            if self.mode == "execute" and self.cleanup_required:
                try:
                    self.failure_context["emergency_cleanup"] = self.reset_profiles()
                    self.cleanup_required = False
                except BaseException as exc:
                    cleanup_error = exc
                    self.record_execute_failure(exc)
            self.lock.release()
            if cleanup_error is not None and failure is None:
                raise N3Error(f"limpeza final falhou: {cleanup_error}") from cleanup_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida/consolida E01–E06 offline; executa novos benchmarks somente com --execute."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--verify-existing",
        action="store_true",
        help="audita os lotes indicados sem Docker nem novas medições (padrão)",
    )
    modes.add_argument(
        "--execute",
        action="store_true",
        help="executa novos lotes A e B; nunca é ativado implicitamente",
    )
    parser.add_argument(
        "--block-a-batch",
        default=OFFICIAL_BLOCK_A_BATCH,
        help="lote completo do Bloco A a auditar no modo offline",
    )
    parser.add_argument(
        "--block-b-batch",
        default=OFFICIAL_BLOCK_B_BATCH,
        help="lote completo do Bloco B a auditar no modo offline",
    )
    return parser


def mode_from_arguments(arguments: argparse.Namespace) -> str:
    return "execute" if arguments.execute else "verify-existing"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    mode = mode_from_arguments(arguments)
    executor = N3Executor(
        root=PROJECT_ROOT,
        mode=mode,
        block_a_batch=arguments.block_a_batch,
        block_b_batch=arguments.block_b_batch,
    )

    def interrupt_handler(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("execução integrada interrompida")

    signal.signal(signal.SIGTERM, interrupt_handler)
    signal.signal(signal.SIGINT, interrupt_handler)
    try:
        output = executor.run()
        print(f"[OK] Integração concluída: {output.relative_to(PROJECT_ROOT).as_posix()}")
        print("[RESUMO] experimentos=6 amostras_válidas=30 auditoria=passed")
        return 0
    except KeyboardInterrupt:
        print("[ERRO] Execução interrompida; limpeza segura solicitada.", file=sys.stderr)
        return 130
    except (N3Error, AuditError) as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Coordenação do lock global N3 com os locks próprios dos runners."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Type


N3_TOKEN_ENV = "N3_ORCHESTRATOR_TOKEN"


def assert_n3_access(results_root: Path, error_type: Type[RuntimeError]) -> None:
    """Bloqueia runners individuais enquanto um orquestrador N3 é o proprietário."""
    lock_path = results_root / ".n3.lock"
    if not lock_path.exists():
        return
    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
        expected = str(document["token"])
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise error_type(f"lock integrado inválido ou ilegível: {lock_path}") from exc
    supplied = os.environ.get(N3_TOKEN_ENV, "")
    if not supplied or supplied != expected:
        raise error_type(f"execução integrada em andamento: {lock_path}")

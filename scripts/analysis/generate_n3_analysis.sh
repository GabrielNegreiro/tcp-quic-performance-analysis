#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

command -v python >/dev/null 2>&1 || {
    printf '%s\n' '[ERRO] Python 3 não está disponível no PATH.' >&2
    exit 1
}

cd "$PROJECT_ROOT" || exit 1
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec python src/analysis/n3_analysis.py "$@"

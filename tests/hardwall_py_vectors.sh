#!/usr/bin/env bash
# 硬墙判定向量(测 Python hardwall 对冻结期望)—— 收口后替代 test_hardwall 的判定部分。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HERE/hardwall_py_vectors.py"


#!/usr/bin/env bash
# 硬墙差分语料闸门：bash `hard_deny_cmd` 对 44 条语料的判定必须与基线一致。
# 这是"Python 版 hard_deny_cmd"将来做 parity 的 oracle；改动 wchat.sh 硬墙行为后此闸门会红，
# 需复跑 tests/hdcmd_probe.py 重新固化基线（并说明理由）。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HERE/hdcmd_probe.py" --check


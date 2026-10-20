#!/usr/bin/env bash
# B 阶段 1d：`wchat_py/tools_run.py` 五工具 parity 闸门（list_dir/read_file/grep/write_file/edit_file）。
# 判据：content + 屏幕状态行 + 落盘文件字节 三者与 bash 一致。末行 `RESULT ... fail=0`。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HERE/tools_run_parity.py"


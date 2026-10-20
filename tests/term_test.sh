#!/usr/bin/env bash
# 终端层地基闸门：raw_mode 必须在正常/异常/非 tty 三路都恢复 termios。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HERE/term_test.py"


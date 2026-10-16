#!/usr/bin/env bash
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

echo "=== D 开关与回退 ==="
mkdir -p "$W/fakepy/wchat_py"
: > "$W/fakepy/wchat_py/__init__.py"
: > "$W/fakepy/wchat_py/_contract.py"
cp "$REAL_DIR/wchat_py/_run.py" "$W/fakepy/wchat_py/_run.py"   # 启动器照抄,哨兵包才装得上
printf '%s\n' 'import sys' 'sys.stdout.buffer.write(b"SENTINEL\n")' 'sys.exit(0)' > "$W/fakepy/wchat_py/textutil.py"
mkdir -p "$W/empty"
printf 'x' > "$W/in"
cw 1 "$W/fakepy" 200 '…' "$W/in" "$W/out"
ck "默认走 Python 活实现(哨兵命中)" "$(cat "$W/out")" "SENTINEL"
cw 0 "$W/fakepy" 200 '…' "$W/in" "$W/out"
ck "WCHAT_PY=0 退回 bash(哨兵不出现)" "$(cat "$W/out")" "x"
cw 1 "$W/fakepy" 200 '…' "$W/in" "$W/out" "WCHAT_PY_TEXTUTIL=0"
ck "分模块开关 WCHAT_PY_TEXTUTIL=0 退回 bash" "$(cat "$W/out")" "x"
cw 1 "$W/empty" 200 '…' "$W/in" "$W/out"
ck "迁移包缺失自动退回(不打死功能)" "$(cat "$W/out")" "x"


finish

#!/usr/bin/env bash
# 硬墙判定 Python 路径闸门（收口后版）：
#   A) 跑 hardwall_py_vectors（判定向量 → Python hardwall 对冻结期望）→ 期望 235/235。
#   B) 负对照：复制一份 wchat_py 并把 hardwall.rule 打坏（恒放行）→ 该表必须变红。
#      证明"表确实在跑 Python hardwall，不是摆设"。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
p=0; f=0
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT

run_vec() {  # <py_pkg_path 或 ""> <log>
    if [[ -n "$1" ]]; then
        HW_VEC_PYPATH="$1" python3 "$HERE/hardwall_py_vectors.py" > "$2" 2>&1
    else
        python3 "$HERE/hardwall_py_vectors.py" > "$2" 2>&1
    fi
}

echo "=== A 判定向量(Python hardwall) 全绿 ==="
run_vec "" "$W/a.log"; rca=$?
resa=$(grep -aoE 'RESULT total=[0-9]+ pass=[0-9]+ fail=[0-9]+' "$W/a.log" | tail -1)
if [[ $rca -eq 0 && "$resa" == *" fail=0"* ]]; then
    echo "  ✓ $resa"; p=$((p + 1))
else
    echo "  FAIL ✗ rc=$rca res=[$resa]"; f=$((f + 1)); tail -4 "$W/a.log" | sed 's/^/      /'
fi

echo "=== B 接线证明:弄坏 hardwall.rule → 表必须变红 ==="
mkdir -p "$W/pypkg"
cp -R "$RLDIR/wchat_py" "$W/pypkg/wchat_py"
cat >> "$W/pypkg/wchat_py/hardwall.py" <<'PY'


def rule(cmd, auto=False, work_root=None, home=None):   # BROKEN(负对照)
    return "", ""
PY
run_vec "$W/pypkg" "$W/b.log"; rcb=$?
resb=$(grep -aoE 'RESULT total=[0-9]+ pass=[0-9]+ fail=[0-9]+' "$W/b.log" | tail -1)
if [[ -n "$resb" && "$resb" != *" fail=0"* ]]; then
    echo "  ✓ 弄坏 hardwall.rule 后表变红:$resb"; p=$((p + 1))
else
    echo "  FAIL ✗ 弄坏后仍全绿(rc=$rcb res=[$resb]) —— Python 路径没被执行"; f=$((f + 1))
    tail -4 "$W/b.log" | sed 's/^/      /'
fi

echo "RESULT total=$((p + f)) pass=$p fail=$f"
[[ $f -eq 0 ]]
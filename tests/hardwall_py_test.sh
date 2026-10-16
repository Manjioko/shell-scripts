#!/usr/bin/env bash
# 硬墙判据"Python 路径"闸门 —— 同一批 569 向量,让**已迁移的谓词改走 Python**再跑一遍。
#
# 为什么需要它(方案「铁律」+ W2b 注释):tests/test_hardwall.sh 默认给 _py_on 打回退桩,
# 只测 bash 回退路径。**安全核心不能只有 parity** —— 两条路"一致地错"会双双漏过。
# 所以这里:
#   A) HW_PY=1(真实 _py_on/py_run)跑整表 → 期望 569/569;既覆盖已迁移谓词的 Python 路径,
#      又保证未迁移的照旧 bash;
#   B) 用一份**被打坏的 wchat_py**(mask_readonly 原样返回)再跑一遍 → 期望**变红**。
#      这就是"表确实在跑 Python、不是静默退回 bash"的接线证明 —— 少了 B,A 可能只是假绿。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
BASHX="${BASH:-bash}"
if [[ "${BASH_VERSINFO[0]:-0}" -lt 5 ]]; then
    for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$b" ]] && BASHX="$b" && break
    done
fi
p=0; f=0
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT

run_hw() {  # <hw_py_dir 或 ""> <log>
    if [[ -n "$1" ]]; then
        HW_PY=1 HW_PY_DIR="$1" "$BASHX" tests/test_hardwall.sh > "$2" 2>&1
    else
        HW_PY=1 "$BASHX" tests/test_hardwall.sh > "$2" 2>&1
    fi
}

echo "=== A Python 路径:同一批向量全绿 ==="
run_hw "" "$W/a.log"; rca=$?
resa=$(grep -aoE 'RESULT total=[0-9]+ pass=[0-9]+ fail=[0-9]+ expect=[0-9]+' "$W/a.log" | tail -1)
if [[ $rca -eq 0 && "$resa" == *" fail=0"* && "$resa" == *"pass=569"* ]]; then
    echo "  ✓ $resa"; p=$((p + 1))
else
    echo "  FAIL ✗ rc=$rca res=[$resa]"; f=$((f + 1)); tail -4 "$W/a.log" | sed 's/^/      /'
fi

echo "=== B 接线证明:弄坏 wchat_py/hd.py → 表必须变红 ==="
mkdir -p "$W/pypkg"
cp -R wchat_py "$W/pypkg/wchat_py"
# 追加一个"原样返回"的 cmd_mask_readonly 并重新登记(比字符串替换稳,不怕实现挪行)
cat >> "$W/pypkg/wchat_py/hd.py" <<'PY'


def cmd_mask_readonly(argv):            # BROKEN(负对照用)
    _contract.write_text(_contract.read_stdin_text())
    return _contract.EXIT_OK
_COMMANDS["mask_readonly"] = cmd_mask_readonly
PY
run_hw "$W/pypkg" "$W/b.log"; rcb=$?
resb=$(grep -aoE 'RESULT total=[0-9]+ pass=[0-9]+ fail=[0-9]+ expect=[0-9]+' "$W/b.log" | tail -1)
if [[ -n "$resb" && "$resb" != *" fail=0"* ]]; then
    echo "  ✓ 弄坏 hd.py 后表变红:$resb"; p=$((p + 1))
else
    echo "  FAIL ✗ 弄坏 hd.py 后表仍全绿(rc=$rcb res=[$resb]) —— Python 路径没被真正执行"; f=$((f + 1))
    tail -4 "$W/b.log" | sed 's/^/      /'
fi

echo "RESULT total=$((p + f)) pass=$p fail=$f"
[[ $f -eq 0 ]]


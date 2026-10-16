#!/usr/bin/env bash
# Python 迁移通道闸门(W0/W1)—— 开关 / 回退 / parity / 接线。
#
# 这道闸门存在的理由(docs/migration-to-python.md「铁律」):
#   迁移期最危险的状态不是"迁错了",而是 bash 旧实现与 Python 新实现
#   **各自被调用、各自漂移、且都没人测**。所以每迁一个模块,必须当场钉住四件事:
#     ① 默认真的走 Python(用哨兵证明,不是 grep 源码);
#     ② 开关关闭真的退回 bash;
#     ③ 两条路输出**逐字节一致**(parity);
#     ④ 环境缺失(python 包不在)时自动退回,功能不被打死。
#
# 装载方式沿用 mcp_channel_test.sh:主脚本自己的 _WCHAT_LIB_ONLY 口整文件装载,
#   沙箱用临时 HOME/TMPDIR(否则 source 会在真实 ~/.ct 下建目录)。
# 被测文件可由第一个参数指定(变异装置会把副本路径传进来)。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
SRC="${1:-wchat.sh}"
[[ -f "$SRC" ]] || { echo "找不到 $SRC"; exit 2; }
SRC=$(cd "$(dirname "$SRC")" && pwd -P)/$(basename "$SRC")
REAL_DIR=$(dirname "$SRC")
BASHX="${BASH:-bash}"
if [[ "${BASH_VERSINFO[0]:-0}" -lt 5 ]]; then
    for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$b" ]] && BASHX="$b" && break
    done
fi

pass=0; fail=0
ck()    { if [[ "$2" == "$3" ]]; then echo "  ✓ $1"; pass=$((pass+1)); else echo "  FAIL ✗ $1"; echo "      期望 [$3] / 实际 [$2]"; fail=$((fail+1)); fi; }
cksame() {  # <名字> <文件1> <文件2>  逐字节比对
    if cmp -s "$2" "$3"; then echo "  ✓ $1"; pass=$((pass+1)); else
        echo "  FAIL ✗ $1(逐字节不一致)"; fail=$((fail+1))
        echo "      f1: $(od -An -c "$2" 2>/dev/null | tr -s ' ' | head -4)"
        echo "      f2: $(od -An -c "$3" 2>/dev/null | tr -s ' ' | head -4)"
    fi
}

W=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$W"' EXIT
# 保留**真实** HOME:被测依赖(如 openai)可能装在 user-site(`$HOME/…`),覆写 HOME 后
# import 不到 → 闸门会假报"依赖缺失"。需要真实 HOME 的用例读 $REAL_HOME。
REAL_HOME="${HOME:-}"; export REAL_HOME
export HOME="$W/home"; mkdir -p "$HOME" "$W/proj" "$W/tmp"
export TMPDIR="$W/tmp"
export WCHAT_API_KEY="${WCHAT_API_KEY:-sk-stub}"
export BASE_URL="${BASE_URL:-https://stub.invalid/v1}"
REAL="$SRC"
RLDIR="$(dirname "$REAL")"

# 跑一次 clip_line_width,把**函数 stdout 原样字节**落 $outf。
# 参数:<WCHAT_PY> <WCHAT_PY_DIR> <max> <mark|__NOMARK__> <in> <out> [extra-env]
# mark=__NOMARK__ 时不传 mark 实参,用来测"缺省 mark"。
cw() {
    local py="$1" pdir="$2" max="$3" mark="$4" inf="$5" outf="$6" extra="${7:-}"
    local call
    if [[ "$mark" == "__NOMARK__" ]]; then
        call="clip_line_width '$max' < '$inf' > '$outf'"
    else
        call="clip_line_width '$max' '$mark' < '$inf' > '$outf'"
    fi
    env $extra WCHAT_PY="$py" WCHAT_PY_DIR="$pdir" "$BASHX" -c "
set +u
cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load
$call
" >/dev/null 2>&1
}

# 跑一次 truncate_output(正文经变量原样传入,保留嵌入与结尾换行)。
ct() {  # <py> <max_col> <max_lines> <in> <out>  —— max_* 覆盖全局(函数读全局,不读入参)
    local py="$1" mc="$2" ml="$3" inf="$4" outf="$5"
    WCHAT_PY="$py" "$BASHX" -c "
set +u
cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load
TOOL_OUTPUT_MAX_COL='$mc'; TOOL_OUTPUT_MAX_LINES='$ml'
IFS= read -r -d '' _t < '$inf'
truncate_output \"\$_t\" > '$outf'
" >/dev/null 2>&1
}

finish() { echo "RESULT total=$((pass + fail)) pass=$pass fail=$fail"; [[ "$fail" -eq 0 ]]; }
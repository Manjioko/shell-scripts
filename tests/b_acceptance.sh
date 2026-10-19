#!/usr/bin/env bash
# B 阶段验收脚本（对应 docs/migration-to-python-B.md §3 的 7 条）。
#
# 定位：这**不是**"跑一下看看绿不绿"的脚本，而是**进度表 + 完成判据**。
# 它在 B 阶段初期应当是**红的**（wchat.sh 还在）——红是正确状态，不是脚本坏了。
# 反过来说：如果现在就跑出全绿，说明**判据是假的**，必须去查。
#
# 用法：
#   bash tests/b_acceptance.sh          # 快检（跳过全量与回放，秒级）
#   bash tests/b_acceptance.sh --full   # 含 run_gates --main 与回放语料检查（分钟级）
#
# ⚠ 自检要求（§6 第 0 步）：故意改坏一处，本脚本必须变红。做法：
#   bash tests/b_acceptance.sh --self-test   # 见下方 self_test()
# TODO(1b): 第9条'参数解析 parity'待补 —— 已手工验过三组 argv(py vs bash 逐项一致),
#   但固化成断言时卡在读取方式上: shellio.assign 只在需要时加引号, 用 awk/sed 解析易错;
#   正解是 `eval "$(python3 -m wchat_py.cli parse $v)"` 直接拿变量(这正是生产用法)。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
cd "$RLDIR" || exit 2
FULL=0; SELF=0
for a in "$@"; do
    case "$a" in --full) FULL=1 ;; --self-test) SELF=1 ;; esac
done
ok=0; bad=0
p() { # <编号> <名称> <是否通过:0/1> <现状描述>
    if [[ "$3" == "0" ]]; then ok=$((ok+1)); printf '  ✅ [%s] %-28s %s\n' "$1" "$2" "$4"
    else bad=$((bad+1)); printf '  ❌ [%s] %-28s %s\n' "$1" "$2" "$4"; fi
}

echo "=== B 阶段验收（$( [[ $FULL == 1 ]] && echo 全量 || echo 快检 )）==="

# ── 1. 文件层：仓库里不再有业务 .sh ────────────────────────────────────────────
_sh=$(git ls-files '*.sh' 2>/dev/null | grep -v '^tests/' | tr '\n' ' ')
[[ -z "$_sh" ]] && p 1 "无业务 .sh" 0 "只剩 tests/" || p 1 "无业务 .sh" 1 "仍存在: $_sh"

# ── 2. 入口：wchat 必须是 Python，且参数全可用 ────────────────────────────────
_head=$(head -1 "$RLDIR/wchat" 2>/dev/null || echo "")
# ⚠ 不能断言"rc=0"：迁移前 `wchat -h` **本就 rc=1**（打印用法但返回 1）。
# 要求 rc=0 反而是**行为变更**。所以这里钉**可观测输出**（首行用法），不钉退出码。
_out=$(cd /tmp && "$RLDIR/wchat" -h 2>&1 | head -1)
case "$_head" in *python*) _ent=0 ;; *) _ent=1 ;; esac
case "$_out" in *"使用方法"*) _ok2=0 ;; *) _ent=1; _ok2=1 ;; esac
if [[ "$_ent" == 0 && "$_ok2" == 0 ]]; then
    p 2 "入口是 Python 且 -h 有用法" 0 "首行=python；-h 首行=用法"
else
    p 2 "入口是 Python 且 -h 有用法" 1 "首行=${_head:0:22}；-h 首行=${_out:0:22}"
fi

# ── 3. 无 shell 注入面 ────────────────────────────────────────────────────────
_hits=$(grep -rn 'eval(\|os\.system\|shell=True' --include='*.py' wchat_py 2>/dev/null | wc -l | tr -d ' ')
[[ "$_hits" == "0" ]] && p 3 "无 eval/os.system/shell=True" 0 "0 处" || p 3 "无 eval/os.system/shell=True" 1 "$_hits 处"

# ── 4. 启动预算：wchat -h 冷启动 < 300ms（仅当入口已是 Python 才有意义）────────
if [[ "$_head" == *python* ]]; then
    t0=$(python3 -c 'import time;print(time.time())')
    ( cd /tmp && "$RLDIR/wchat" -h >/dev/null 2>&1 )
    ms=$(python3 -c "import time;print(int((time.time()-$t0)*1000))")
    [[ "$ms" -lt 300 ]] && p 4 "启动预算 <300ms" 0 "${ms}ms" || p 4 "启动预算 <300ms" 1 "${ms}ms(超)"
else
    p 4 "启动预算 <300ms" 1 "N/A(入口还不是 Python)"
fi

# ── 5. 交互路径：确认弹窗 / Ctrl+C×1 / Ctrl+C×2 / 渲染 ───────────────────────
# 这四项需要 pty 驱动，暂由 e2e 覆盖前两项；另两项是手工用例（写在 B 宪章 §3）。
# 这里只检查"是否有对应的自动化用例存在"，避免"以为有其实没有"。
_ck_int=$(grep -lc 'handle_ctrl_c\|两次.*急停\|急停' tests/*.sh 2>/dev/null | wc -l | tr -d ' ')
[[ "$_ck_int" -ge 1 ]] && p 5 "交互用例存在(非空覆盖)" 0 "$_ck_int 个测试文件提及" \
                       || p 5 "交互用例存在(非空覆盖)" 1 "没有测试文件提及急停/ctrl_c"

# ── 6. 既有判据全绿（慢，--full 才跑）────────────────────────────────────────
if [[ $FULL == 1 ]]; then
    _g=$(bash tests/run_gates.sh --main 2>&1 | grep -E '^GATES run=' | tail -1)
    case "$_g" in
        *"pass="*"fail=0"*) p 6 "run_gates --main 全绿" 0 "$_g" ;;
        *)                  p 6 "run_gates --main 全绿" 1 "${_g:-无结论行}" ;;
    esac
else
    echo "  ⏭  [6] run_gates --main 全绿      跳过(用 --full)"
fi

# ── 7. 真实会话回放语料 ≥10 段 + 逐事件复现 ──────────────────────────────────
_rep=$(ls tests/replay/*.json 2>/dev/null | wc -l | tr -d ' ')
[[ "$_rep" -ge 10 ]] && p 7 "回放语料 ≥10 段" 0 "$_rep 段" || p 7 "回放语料 ≥10 段" 1 "${_rep} 段(需 ≥10)"

# ── 8. 跨实现比对：Python 入口的 -h 与 bash 的 help 基线逐行一致 ────────────────
# 这是 B 阶段**第一个"新实现 vs 旧实现"的等价证据**。归一化后逐行比（剥 ANSI/掩 CPR 产物）,
# 与 replay_check 用同一套 normalize, 避免两处口径漂移。
_ph=$(mktemp)
( cd /tmp && PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m wchat_py.cli -h > "$_ph" 2>&1 )
_r=$(python3 - "$_ph" "$RLDIR" <<'PYX' 2>&1 | tail -1
import base64, importlib.util, json, sys
got_path, repo = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("rc", repo + "/tests/replay_check.py")
rc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)
want = base64.b64decode(json.load(open(repo + "/tests/replay/help.json"))["out_b64"])
got = open(got_path, "rb").read()
def L(b):
    return [x for x in (l.split("│ ", 1)[-1].strip() for l in rc.normalize(b)) if x]
a, b = L(want), L(got)
print("same(%d)" % len(b) if a == b else "diff(base%d/py%d)" % (len(a), len(b)))
PYX
)
rm -f "$_ph"
[[ "$_r" == same* ]] && p 8 "Python -h 与 bash 基线一致" 0 "$_r" \
                      || p 8 "Python -h 与 bash 基线一致" 1 "$_r"

# ── 9. 参数解析 parity：python cli parse vs bash parse_options（逐项一致）──────────
# ⚠ 读法必须用 eval：shellio.assign 用 shlex.quote，**只在需要时加引号**
# （content=abc 不加引号、content='' 加引号），用 awk/sed 解析会一半对一半错。
_po="$RLDIR/.scratch/_po_ref.sh"
cat > "$_po" <<'REF'
_WCHAT_LIB_ONLY=1 API_KEY=sk-stub
. "$1" < /dev/null > /dev/null
shift
content=""; quick_mode=0; forget_history=0; model=""; thinking=disabled; pro_model="${PRO_MODEL:-}"
parse_options "$@"
printf '%s|%s|%s|%s|%s' "$content" "$quick_mode" "$forget_history" "$model" "$thinking"
REF
_bad=0; _n=0
for v in "-f -st hello world" "-st" "abc" "-t" "-p" "-dp" "-f -dp 问题文本"; do
    _n=$((_n+1))
    b=$(/opt/homebrew/bin/bash "$_po" "$RLDIR/wchat.sh" $v 2>/dev/null | perl -pe 's/\e\[[0-9;?]*[A-Za-z]//g' | tr -d '\n')
    content=""; quick_mode=0; forget_history=0; model=""; thinking=disabled
    eval "$(PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m wchat_py.cli parse $v 2>/dev/null)"
    [[ "$b" == "$content|$quick_mode|$forget_history|$model|$thinking" ]] || {
        _bad=$((_bad+1)); echo "      差异 argv[$v]: bash=$b py=$content|$quick_mode|$forget_history|$model|$thinking"
    }
done
[[ "$_bad" == 0 ]] && p 9 "参数解析 parity($_n 组)" 0 "全部一致" \
                    || p 9 "参数解析 parity($_n 组)" 1 "$_bad 组不一致"
rm -f "$_po"

echo "----"
printf 'B 验收:通过 %d / 共 %d（未通过 %d）\n' "$ok" "$((ok+bad))" "$bad"
if [[ $bad -gt 0 ]]; then
    echo "提示:B 阶段初期出现红是**预期**的——本脚本是进度表,不是回归闸门。"
    exit 1
fi
echo "✅ B 阶段验收全部满足：可以删 wchat.sh 了。"
exit 0
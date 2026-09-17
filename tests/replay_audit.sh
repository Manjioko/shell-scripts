#!/bin/bash
# 真实流量回放:把 ~/.ct/auto/audit.log 里跑过的真实命令,分别喂给两套判据,逐条对照结论。
#   用法:tests/replay_audit.sh [旧版本来源] [新版本来源]
#         默认 old=HEAD^:wchat.sh  new=wchat.sh(工作树)
#         来源可以是 git 参数(HEAD^ / HEAD~3 / v1.2.3),也可以是文件路径。
# 目的:判据改动"回归表没红"只证明没自相矛盾,不证明没误伤真实命令。误伤与漏拦都只有
#       在真实流量上量才看得见 —— 这一步是 C2(引号边界 + heredoc 数据脱敏)那轮补的。
# 全程只做文本判据,**不执行任何被回放的命令**(被回放的东西里真有 rm -rf 和提权)。
#
# ── 这台测量装置自己的三条红线(都是一轮轮踩出来的,别再改回去)──
#   1) 装载必须走真脚本文件 + eval,不许用 `bash -c 'source ./wchat.sh'`:那条路会撞上
#      主脚本模块级的 bash 5 re-exec,$0 不是脚本时 exec 到垃圾目标(实测 rc=126 的
#      "cannot execute binary file" 与 rc=127 的 "_: No such file"),整段判据静默不跑。
#      见 test_hardwall.sh 的 T25,那三条断言就是钉这个的。
#   2) 判据函数没装载 ≠ 放行。以前 runner 写的是 `if hard_deny_cmd …; then echo 规则; else
#      echo "-"`,而 "-" 在对照阶段就是"放行" —— eval 一崩,全部命令都报告成放行,现象是
#      "新旧零差异",一张看起来很干净的假绿表。现在缺函数直接吐 !NOFUNC 并整体中止。
#   3) 空结果 ≠ 放行,一律 !ERR 中止;并且交结果前必须先过自检:三条已知答案的向量
#      走**同一 runner、同一装载路径**,答错就说明装置坏了,不接受它输出的任何数字。
set -u
# 判据链是 bash 5 语法的(`hd_rule_map` 等用 declare -A)。用 /bin/bash 3.2 跑它不会报错,
# 只会**静默给出错的结论** —— 上一轮 rulefail_check.sh 就是这么假绿的。所以这个工具自己
# 必须先落到 bash 5+ 再干活:这里的 $0 是真脚本文件,re-exec 安全(T25 的那三条断言
# 正是为了保证这一点)。
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash "${HOME}/.linuxbrew/bin/bash"; do
        [[ -x "$_b" ]] || continue
        [[ "$("$_b" -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null || echo 0)" =~ ^[5-9] ]] || continue
        exec "$_b" "$0" "$@"
    done
    echo "⛔ 需要 bash 5+ 才能判这套规则(3.2 下 declare -A 会静默出错),没找到可用 bash 5" >&2
    exit 1
fi
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
ROOT="$PWD"

OLD_SRC="${1:-HEAD^:wchat.sh}"
NEW_SRC="${2:-wchat.sh}"
LOG="${WCHAT_AUDIT_LOG:-$HOME/.ct/auto/audit.log}"
[[ -r "$LOG" ]] || { echo "读不到审计日志:$LOG(可用 WCHAT_AUDIT_LOG 指定)"; exit 1; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/wchat-replay.XXXXXX") || exit 1
trap 'rm -rf "$WORK"' EXIT

# ── 1) 取命令原文(NUL 分隔,兼容命令里带换行)──────────────────────────
python3 - "$LOG" > "$WORK/cmds.nul" <<'PY'
import json, sys
seen = set(); out = []
for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    line = line.strip()
    if not line.startswith('{'):
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get('tool') != 'run_shell_command':
        continue
    t = o.get('target') or ''
    if not t.strip():
        continue
    # 判据链本来就把换行压成空格(见 hook 调用点的 cmd_one),这里保持同一口径
    t = t.replace('\n', ' ')
    key = t[:400]
    if key in seen:
        continue
    seen.add(key); out.append(t)
sys.stdout.buffer.write(('\0'.join(out)).encode('utf-8'))
print(f"# 取到 {len(out)} 条去重后的真实命令", file=sys.stderr)
PY

# ── runner:一次判一条命令,装载走真脚本文件 ────────────────────────────
cat > "$WORK/run.sh" <<'R'
f="$1"; cmd="$2"
# cwd 由调用方决定(=仓库根,与生产环境判这条命令时的 cwd 一致):凭据/工作目录豁免都跟 cwd 有关
eval "$(cat "$f")" 2>"$WORK_DIR/eval.err" || true   # eval 报错不中止:下面用 declare -F 判装载是否真的成功
if ! declare -F hard_deny_cmd >/dev/null 2>&1; then
    echo "!NOFUNC"; exit 1
fi
auto_mode=full
if hard_deny_cmd "$cmd" >/dev/null 2>&1; then
    echo "${hd_rule:-!NORULE}"
else
    # 非破坏性操作:hard_deny_cmd 返回 0 但 hd_rule 为空,同样按"放行"记
    echo "${hd_rule:--}"
fi
R

extract() {   # $1=来源 $2=输出文件 —— 抽出判据链(顶层 hd_*/auto_*/WCHAT_* 赋值 + hd_* 函数定义)
    # 先按文件判,不是文件才当 git ref:布尔式里混 `&&`/`||` 的写法在 `HEAD:wchat.sh`、
    # `~/x`、`a:b` 这类路径上会判错,而判错的后果是抽出空判据、交出一张全放行的表。
    # 换成"能不能 cp"这种事实判定。
    if [[ -f "$1" ]]; then
        cp "$1" "$WORK/src.sh" || return 1
    else
        git show "$1" > "$WORK/src.sh" || return 1
    fi
    {
        grep -E '^(hd_[a-z0-9_]*|auto_[a-zA-Z0-9_]*|WCHAT_[A-Z0-9_]*)=' "$WORK/src.sh" | grep -vF '$('
        for _fn in $(grep -oE '^(hd_[a-z0-9_]*|hard_deny_cmd)\(\) \{' "$WORK/src.sh" | sed 's/() {//'); do
            awk -v f="$_fn" '$0 ~ "^"f"\\(\\) \\{" {p=1} p{print} p && /^}$/ {exit}' "$WORK/src.sh"
        done
    } > "$2"
}

# 自检向量:新旧两版都必须答对(这三条的期望与本轮改动无关,是判据的地基)
SELFCHECK_H6="cat '~/.npmrc'"
SELFCHECK_H3='rm -rf ~'
SELFCHECK_OK='echo hello world'
selfcheck() {   # $1=标签 $2=判据文件
    local tag="$1" f="$2" bad=0 v got
    for v in "H6|$SELFCHECK_H6" "H3|$SELFCHECK_H3" "-|$SELFCHECK_OK"; do
        got=$(WORK_DIR="$WORK" bash "$WORK/run.sh" "$f" "${v#*|}" 2>&1)
        if [[ "$got" != "${v%%|*}" ]]; then
            printf '  ✗ [%s] 自检向量答错:%s → 期望 %s,实测 %s\n' \
                "$tag" "${v#*|}" "${v%%|*}" "${got:0:80}"
            bad=1
        fi
    done
    if (( bad )); then
        printf '⛔ [%s] 自检未通过:判据没装载或装载方式坏了,拒绝交出任何数字。\n' "$tag"
        printf '   eval stderr(截断):%s\n' "$(head -c 200 "$WORK/eval.err" 2>/dev/null)"
        exit 1
    fi
    printf '  ✓ [%s] 自检 3 条已知向量全对\n' "$tag"
}

build_and_run() {   # $1=版本标签 $2=wchat.sh 来源(git 参数或文件)
    # 分两条 local:同一条里 `local tag="$1" f="$WORK/rc_${tag}.sh"` 会先把所有参数整体
    # 展开再赋值,那会儿 tag 还没值 → set -u 当场炸(与主脚本 hd_strip_noncode 注释同族)。
    local tag="$1" src="$2"
    local f="$WORK/judge_${tag}.sh"
    local i=0 cmd r bad=0
    extract "$src" "$f" || { echo "⛔ [$tag] 抽判据失败:$src"; exit 1; }
    [[ -s "$f" ]] || { echo "⛔ [$tag] 抽出来是空的:$src"; exit 1; }
    selfcheck "$tag" "$f"

    : > "$WORK/res_${tag}.out"
    while IFS= read -r -d '' cmd; do
        i=$((i + 1))
        r=$(WORK_DIR="$WORK" bash "$WORK/run.sh" "$f" "$cmd" 2>&1 | head -1)
        case "$r" in
            ''|!*) printf '  ✗ [%s] 第 %d 条没有有效结论:%s(%s)\n' "$tag" "$i" "${r:-空}" "${cmd:0:60}"; bad=1; r="!ERR" ;;
        esac
        printf '%s\t%s\n' "$i" "$r" >> "$WORK/res_${tag}.out"
    done < "$WORK/cmds.nul"
    echo "[$tag] 判完 $i 条"
    if (( bad )); then
        echo "⛔ [$tag] 过程中出现无效判定(装载坏了),结果不可信,中止。"
        exit 1
    fi
}

echo "══ 回放:旧=$OLD_SRC 新=$NEW_SRC(工作树改动不必提交即可对照)══"
build_and_run old "$OLD_SRC"
build_and_run new "$NEW_SRC"

# ── 3) 对照 ────────────────────────────────────────────────────────────
python3 - "$WORK" <<'PY'
import sys, os
d = sys.argv[1]
def load(p):
    m = {}
    for line in open(p, encoding='utf-8', errors='replace'):
        a, b = line.rstrip('\n').split('\t', 1)
        m[int(a)] = b
    return m
old, new = load(os.path.join(d, 'res_old.out')), load(os.path.join(d, 'res_new.out'))
cmds = [c for c in open(os.path.join(d, 'cmds.nul'), encoding='utf-8',
                        errors='replace').read().split('\0') if c.strip()]
nb = sum(1 for v in new.values() if v not in ('-',))
ob = sum(1 for v in old.values() if v not in ('-',))
added = [(i, old[i], new[i]) for i in new if new[i] == '-' and old.get(i, '-') != '-']
blocked = [(i, old[i], new[i]) for i in new if old.get(i, '-') == '-' and new[i] != '-']
changed = [(i, old[i], new[i]) for i in new
           if new[i] != '-' and old.get(i, '-') not in ('-', new[i])]
print(f"总数 {len(new)} | 旧拦 {ob} → 新拦 {nb}")
print(f"新增拦下 {len(blocked)} / 新增放行 {len(added)} / 规则号改变 {len(changed)}")
for name, rows in (('新增拦下', blocked), ('新增放行', added), ('规则号改变', changed)):
    for i, o, n in rows[:10]:
        c = cmds[i - 1] if 0 < i <= len(cmds) else '?'
        print(f"  [{name}] {o}→{n}  {c[:110]}")
if not (added or blocked or changed):
    print("  注:零差异只说明这批流量没触发改动,不说明改动生效 —— 生效性看 test_hardwall.sh")
PY
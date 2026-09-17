#!/bin/bash
# 一次性回放(用完即删):把 ~/.ct/auto/audit.log 里的真实命令,分别喂给
#   旧判据(HEAD^:C2 之前)与新判据(当前工作树),对照每一条的结论。
# 目的:上一轮给 hd_re_wipe_root 的路径边界类补了单双引号(补 sh -c "…" 的 fail-open),
# 同时加了 heredoc 数据型正文脱敏。前者只增加匹配、后者只减少匹配 —— 两者在真实流量上
# 各改变了哪些命令,必须量出来,不能靠"回归表没红"就说没有副作用。
# 全程只做文本判据,不执行任何被回放的命令。
cd /Users/jwan/bin || exit 1
set -u

LOG="$HOME/.ct/auto/audit.log"
[[ -r "$LOG" ]] || { echo "读不到 $LOG"; exit 1; }

# ── 1) 取命令原文(NUL 分隔,兼容命令里带换行)──────────────────────────
python3 - "$LOG" > /tmp/rc_cmds.nul <<'PY'
import json, sys, re
seen = set(); out = []
for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    line = line.strip()
    if not line.startswith('{'): continue
    try: o = json.loads(line)
    except Exception: continue
    if o.get('tool') != 'run_shell_command': continue
    t = o.get('target') or ''
    if not t.strip(): continue
    t = t.replace('\n', ' ')            # 判据链本来就把换行压成空格(见调用点 cmd_one)
    key = t[:400]
    if key in seen: continue
    seen.add(key); out.append(t)
sys.stdout.buffer.write(('\0'.join(out)).encode('utf-8'))
print(f"# 取到 {len(out)} 条去重后的真实命令", file=sys.stderr)
PY

# ── 2) 分别装载新旧两套判据,各跑一遍 ──────────────────────────────────
build_and_run() {   # $1=版本标签(new|old) $2=wchat.sh 来源(git 参数或文件)
    # 分两条 local:同一条里 `local tag="$1" f="/tmp/rc_${tag}.sh"` 会先把所有参数整体
    # 展开再赋值,那会儿 tag 还没值 → set -u 当场炸(与主脚本 hd_strip_noncode 注释里记的
    # `local s="$1" n=${#s}` 是同一族坑,本轮第三次踩)。
    local tag="$1" src="$2"
    local f="/tmp/rc_${tag}.sh"
    local i=0 cmd
    {
        if [[ "$src" == "-"* || "$src" == *"HEAD"* ]]; then git show "$src" > /tmp/rc_wchat_src.sh; else cp "$src" /tmp/rc_wchat_src.sh; fi
        grep -E '^(hd_[a-z0-9_]*|auto_[a-zA-Z0-9_]*|WCHAT_[A-Z0-9_]*)=' /tmp/rc_wchat_src.sh | grep -vF '$('
        for _fn in $(grep -oE '^(hd_[a-z0-9_]*|hard_deny_cmd)\(\) \{' /tmp/rc_wchat_src.sh | sed 's/() {//'); do
            awk -v f="$_fn" '$0 ~ "^"f"\\(\\) \\{" {p=1} p{print} p && /^}$/ {exit}' /tmp/rc_wchat_src.sh
        done
    } > "$f" 2>/dev/null

# 独立 runner:避免在 bash -c 里套 eval 的引号地狱(那种写法出错时是"整套判据静默不命中",
# 正好是本轮一直在排的那类假绿)。
cat > /tmp/rc_run.sh <<'R'
f="$1"; cmd="$2"
cd /Users/jwan/bin || exit 1
eval "$(cat "$f")" 2>/dev/null
auto_mode=full
if hard_deny_cmd "$cmd" >/dev/null 2>&1; then echo "${hd_rule:--}"; else echo "-"; fi
R

    : > "/tmp/rc_${tag}.out"
    while IFS= read -r -d '' cmd; do
        i=$((i + 1))
        r=$(bash /tmp/rc_run.sh "$f" "$cmd" 2>/dev/null)
        printf '%s\t%s\n' "$i" "${r:--}" >> "/tmp/rc_${tag}.out"
    done < /tmp/rc_cmds.nul
    echo "[$tag] 判完 $i 条"
}

build_and_run old "HEAD^:wchat.sh"
build_and_run new "wchat.sh"

# ── 3) 对照 ────────────────────────────────────────────────────────────
python3 - <<'PY'
def load(p):
    d = {}
    for line in open(p, encoding='utf-8', errors='replace'):
        a, b = line.rstrip('\n').split('\t', 1)
        d[int(a)] = b
    return d
old, new = load('/tmp/rc_old.out'), load('/tmp/rc_new.out')
cmds = open('/tmp/rc_cmds.nul', encoding='utf-8', errors='replace').read().split('\0')
cmds = [c for c in cmds if c.strip()]
added = [(i, old[i], new[i]) for i in new if old.get(i, '-') != '-' and new[i] == '-' and old[i] != '-']
blocked = [(i, old[i], new[i]) for i in new if new[i] != '-' and old.get(i, '-') == '-']
changed = [(i, old[i], new[i]) for i in new if old.get(i, '-') not in ('-', new[i]) and new[i] != '-']
print(f"总数 {len(new)} | 旧拦 {sum(1 for v in old.values() if v != '-')} → 新拦 {sum(1 for v in new.values() if v != '-')}")
print(f"新增拦下 {len(blocked)} 条 / 新增放行 {len(added)} 条 / 规则号改变 {len(changed)} 条")
for name, rows in (('新增放行', added), ('新增拦下', blocked), ('规则号改变', changed)):
    for i, o, n in rows[:8]:
        c = (cmds[i - 1] if 0 < i <= len(cmds) else '?')
        print(f"  [{name}] {o or '-'}→{n or '-'}  {c[:110]}")
PY
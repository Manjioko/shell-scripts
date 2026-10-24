#!/usr/bin/env bash
# skill 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `scan_skills` / `tool_read_skill` 已是**唯一活实现**(旧 bash 回退已删)。
# 本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat_ref}" || exit 2

fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }

# ---- scan_skills 夹具 ----
SD="$W/sk"; mkdir -p "$SD/a" "$SD/c" "$SD/d" "$SD/h"
printf -- '---\nname: alpha\ndescription: first skill\n---\n\nbody\n' > "$SD/a/SKILL.md"
printf -- '---\nname: gamma\n---\n' > "$SD/c/SKILL.md"                 # 无 description → 跳过
printf -- '---\ndescription: no name here\n---\n' > "$SD/d/SKILL.md"   # 无 name → 目录名兜底
mkdir -p "$W/outside"
printf -- '---\nname: leak\ndescription: leaked skill\n---\n' > "$W/outside/SKILL.md"
ln -s "$W/outside/SKILL.md" "$SD/h/SKILL.md"                           # 符号链接条目

sc_out() {  # <ext> <extra-env>
    local ex="$1" extra="$2"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
SKILLS_DIR='$SD'; SKILL_MAX_INJECT=30; SKILL_DESC_MAX=200; SKILLS_DIR_EXTERNAL='$ex'
scan_skills
" 2>/dev/null
}

echo "=== scan_skills 语义 ==="
ck "alpha 行在" "$(sc_out 0 '' | grep -c '^- alpha: first skill$')" "1"
ck "无 name 用目录名兜底" "$(sc_out 0 '' | grep -c '^- d: no name here$')" "1"
ck "无 description 的被跳过" "$(sc_out 0 '' | grep -c 'gamma')" "0"
ck "ext=1 跳过符号链接条目" "$(sc_out 1 '' | grep -c 'leak')" "0"
ck "ext=0 收录符号链接条目" "$(sc_out 0 '' | grep -c 'leak')" "1"

# ---- tool_read_skill 夹具 ----
RD="$W/rsk"; mkdir -p "$RD/demo" "$RD/linkdir"
printf -- '---\nname: demo\ndescription: d\n---\n\nHello body.\n' > "$RD/demo/SKILL.md"
ln -s "$W/outside/SKILL.md" "$RD/linkdir/SKILL.md"
rsk() {  # <name> <rdir> <ext> <extra-env>
    local nm="$1" d="$2" ex="$3" extra="$4"
    env $extra WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
SKILLS_DIR='$d'; READ_MAX_CHARS=30000; SKILLS_DIR_EXTERNAL='$ex'
fn_args=\$(jq -n --arg n \"\$1\" '{name:\$n}')
content=''; tool_read_skill
printf 'CONTENT<<%s>>' \"\$content\"
" _ "$nm" 2>/dev/null
}

echo "=== tool_read_skill 语义 ==="
ck "demo 读到全文" "$(rsk demo "$RD" 0 '' | grep -c 'Hello body')" "1"
ck "不存在 → 报错内容" "$(rsk nope "$RD" 0 '' | grep -c 'CONTENT<<错误:技能不存在')" "1"
ck "非法名 → 报错内容" "$(rsk 'a/b' "$RD" 0 '' | grep -c 'CONTENT<<错误:非法技能名')" "1"
ck "ext=1 链接条目被拒" "$(rsk linkdir "$RD" 1 '' | grep -c 'CONTENT<<错误:技能.*是符号链接')" "1"

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
ck "scan_skills 定义 1 处" "$(grep -c '^scan_skills() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody scan_skills | grep -c '回退路径')" "0"
ck "  · 只剩一次 py_run" "$(fnbody scan_skills | grep -c 'py_run skill scan')" "1"
ck "tool_read_skill 定义 1 处" "$(grep -c '^tool_read_skill() {' "$SRC")" "1"
ck "  · 回退注释已删" "$(fnbody tool_read_skill | grep -c '回退路径')" "0"
ck "  · 只剩一次 py_run" "$(fnbody tool_read_skill | grep -c 'py_run skill read')" "1"
ck "  · 不再调 clip/awk/wc" "$(fnbody tool_read_skill | grep -cE 'clip_line_width|awk|wc -m')" "0"

echo "=== 不可用降级(明示,不静默) ==="
ck "scan_skills off → 输出空" "$(sc_out 0 'WCHAT_PY_SKILL=0' | wc -c | tr -d ' ')" "0"
ck "tool_read_skill off → 明示组件不可用" "$(rsk demo "$RD" 0 'WCHAT_PY_SKILL=0' | grep -c 'CONTENT<<错误:技能读取组件不可用')" "1"

finish
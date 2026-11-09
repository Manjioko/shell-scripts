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

# ---- injection():清单前的标题行 ----------------------------------------------
# 标题不是装饰:`read_skill` 的工具描述与"技能不存在"的报错都写"见系统消息中的
# 「可用技能列表」"。这行一丢,模型照着描述去 system 里找,找不到 —— 悬空引用。
# (本仓库真发生过:迁移时把 bash 的 SKILLS_MARK 弄丢了,技能清单成了无归属的裸列表。)
inj() {  # <ext>
    PYTHONPATH="$RLDIR" python3 -c "
from wchat_py.skill import injection
print(injection('$SD', '30', '200', '$1'), end='')
" 2>/dev/null
}
pyq() { PYTHONPATH="$RLDIR" python3 -c "$1" 2>/dev/null; }

echo "=== injection 语义(标题/引导语/空目录) ==="
ck "有标记行且条数正确(ext=0 含软链共 3 条)" \
   "$(inj 0 | grep -c '^## 可用技能列表(Skills,已列 3 个)$')" "1"
ck "明说这是摘要不是正文" "$(inj 0 | grep -c '不是正文')" "1"
ck "引导语点名 read_skill" "$(inj 0 | grep -c 'read_skill')" "1"
ck "标记行在清单之前" "$(inj 0 | head -1 | grep -c '^## 可用技能列表(Skills')" "1"
mkdir -p "$W/skempty"
ck "无技能 → 空串(不占一行预算)" \
   "$(pyq "from wchat_py.skill import injection; print(repr(injection('$W/skempty','30','200','0')))")" "''"

echo "=== 接线:system 里真有这一行,且工具描述指向的标题确实存在 ==="
ck "system 注入含技能标记" \
   "$(pyq "from wchat_py import cli, skill; print('yes' if skill.SKILLS_MARK in cli._system_prompt_text() else 'no')")" "yes"
ck "read_skill 描述的「可用技能列表」在 system 里确有落点(防悬空引用)" \
   "$(pyq "
import json
from wchat_py import cli, tools
d = [t for t in json.loads(tools.build()) if t['function']['name'] == 'read_skill'][0]['function']['description']
s = cli._system_prompt_text()
print('yes' if '可用技能列表' in d and '可用技能列表' in s else 'no')")" "yes"

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
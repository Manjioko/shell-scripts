#!/usr/bin/env bash
# filefmt 模块的 Python 路径闸门(**收口后**)。
#
# 收口(方案铁律 3)后 `filefmt.read` 已是**唯一活实现**(tool_read_file 里的旧 bash
# 格式化回退已删)。本闸门钉:语义锚点 + 函数体内结构锁 + 不可用降级。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-$(dirname "${BASH_SOURCE[0]}")/fixtures/wchat_tools_oracle.sh}" || exit 2

RLDIR="$(dirname "$REAL")"
fnbody() { awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p=1} p {print} p && /^}$/ {exit}' "$SRC"; }
pyread() { python3 "$RLDIR/wchat_py/_run.py" filefmt read "$@"; }
fc() { pyread "$@" | jq -jr '.content'; }     # 展示内容(stdout 原样,含字面 \n)

echo "=== filefmt.read 语义 ==="
printf 'alpha\nbeta\ngamma\ndelta\n' > "$W/rf1"
printf 'l1\nl2\nl3\nl4\nl5\n'        > "$W/rf2"
printf '一二三四五六七八九十\nabcdefghij\n' > "$W/rf3"
printf 'no trailing newline'          > "$W/rf4"

ck "窗口:行号从 offset 起(第1行=2)" "$(fc "$W/rf1" /p 2 2 30000 | grep -c '     2│ beta')" "1"
ck "窗口:只取 limit 行(第4行不出现)" "$(fc "$W/rf1" /p 2 2 30000 | grep -c 'delta')" "0"
ck "窗口:头部写明区间与总行数"        "$(fc "$W/rf1" /p 2 2 30000 | grep -c '第 2-3 行(共 4 行)')" "1"
ck "全量:含最后一行且行号=总数"       "$(fc "$W/rf1" /p 1 0 30000 | grep -c '     4│ delta')" "1"
ck "全量:头部只写总行数"              "$(fc "$W/rf1" /p 1 0 30000 | grep -c '共 4 行:')" "1"
ck "多字节:按字符累计截断,含第1行"   "$(fc "$W/rf3" /p 1 0 15 | grep -c '     1│ 一二三四五六七八九十')" "1"
ck "多字节:截断后不含第2行"           "$(fc "$W/rf3" /p 1 0 15 | grep -c 'abcdefghij')" "0"
ck "多字节:头部给"过大仅前半"提示"     "$(fc "$W/rf3" /p 1 0 15 | grep -c '文件过大仅返回前半部分')" "1"
ck "末尾无换行的文件:末行照常给"       "$(fc "$W/rf4" /p 1 0 30000 | grep -c '     1│ no trailing newline')" "1"
# 历史隐 bug 的**等价复刻**:头部用的是**字面** `\n`(反斜杠+n),不是真换行 —— 迁移只做等价。
ck "头部保留字面 \\n(复刻旧行为)"      "$(fc "$W/rf1" /p 1 0 30000 | grep -cF '行:\n\n')" "1"
ck "lines 字段=总行数"                "$(pyread "$W/rf1" /p 1 0 30000 | jq -r '.lines')" "4"
ck "未知子命令 rc=1"                  "$(pyread nope 2>/dev/null; echo $?)" "1"

echo "=== 收口结构锁:旧 bash 回退确已删除(只查函数体) ==="
ck "tool_read_file 定义 1 处"          "$(grep -c '^tool_read_file() {' "$SRC")" "1"
ck "  · 回退注释已删"                  "$(fnbody tool_read_file | grep -c '回退路径')" "0"
ck "  · 只剩一次 py_run filefmt"       "$(fnbody tool_read_file | grep -c 'py_run filefmt read')" "1"
ck "  · 不再调 sed/awk/wc 格式化"      "$(fnbody tool_read_file | grep -cE 'sed -n|LC_ALL=C awk|wc -m|wc -l')" "0"
ck "  · 有降级明示(content+stderr 各 1)" "$(fnbody tool_read_file | grep -c '读取组件不可用')" "2"

echo "=== 语义/降级:走真实工具口 tool_read_file ==="
rf() {  # <py> <相对路径> <extra-env>
    local py="$1" rel="$2" extra="$3"
    env $extra WCHAT_PY="$py" "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
WORK_ROOT='$W'; granted_files=(); READ_MAX_CHARS=30000; auto_mode=''
fn_args=\$(jq -n --arg p '$rel' '{path:\$p}')
content=''; tool_read_file
printf 'CONTENT<<%s>>' \"\$content\"
" 2>/dev/null
}
ck "工具口:正常读到带行号内容"        "$(rf 1 rf1 '' | grep -c '     2│ beta')" "1"
ck "工具口:正常回显读取行数"          "$(rf 1 rf1 '' | grep -c '已读取 rf1(4 行)')" "1"
ck "工具口 off → 明示组件不可用"       "$(rf 1 rf1 'WCHAT_PY_FILEFMT=0' | grep -c 'CONTENT<<错误:读取组件不可用(filefmt),本次未返回内容>>')" "1"
ck "工具口 off → 不返回任何行号内容"   "$(rf 1 rf1 'WCHAT_PY_FILEFMT=0' | grep -c '│ ')" "0"
# stderr 明示(单独取,不吞)
ck "工具口 off → stderr 有明示"        "$(env WCHAT_PY_FILEFMT=0 WCHAT_PY=1 "$BASHX" -c "
set +u; cd '$W'
_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_load; trap - EXIT
WORK_ROOT='$W'; granted_files=(); READ_MAX_CHARS=30000; auto_mode=''
fn_args=\$(jq -n '{path:\"rf1\"}')
content=''; tool_read_file
" 2>&1 >/dev/null | grep -c '读取组件不可用')" "1"

finish
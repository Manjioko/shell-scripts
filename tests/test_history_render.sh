#!/bin/bash
# 回归测试:-l 加载历史的渲染完整性 + UTF-8 安全截断
#
# 背景(修复的 bug):macOS 自带 BSD awk 的 length/substr 是**字节**语义,历史里的
# 超长行按"限宽 N 字符"截断时会把中文/emoji 切成半个字符,产出非法 UTF-8。这种字节
# 进入渲染管道后,BSD sed 报 "RE error: illegal byte sequence" 当场中止,该位置之后
# 的所有消息再也不会输出 —— 表现就是"加载历史记录只渲染到一半,后面凭空消失"。
#
# 覆盖:
#   T1 渲染链路完整性 —— 含超长中文行的历史,尾部消息必须渲染出来
#   T2 clip_line_width 在各限宽/中文/emoji/坏字节输入下都不产出非法 UTF-8
#   T3 tool_calls 渲染 —— assistant 只带工具调用时不能渲染成空标题,tool 段要标出来历
#   T4 u8len 字符计数 —— BSD awk 按字节计预算会让中文额度缩水成 1/3
#   T5 bash 3.2 兼容 —— 只约束"被本测试直接 eval 出来跑"的那几个函数:shebang
#      是 /bin/bash(macOS 自带 3.2),函数里出现 declare -A / mapfile 等 4+ 语法
#      会在 3.2 上当场失败。脚本级 4+ 语法本身合法(启动器第 65-76 行会 exec 到
#      Homebrew bash 5.x),所以不做整文件一刀切,改为额外校验那个 exec 守卫仍在
#
# 用法:
#   bash test_history_render.sh                  # 测当前 wchat_ref(应全绿)
#   # 负对照(证明测试真能捕获该 bug):把修复前的版本抽出来再跑,期望 FAIL
#   mkdir -p .orig && git show HEAD:wchat_ref > .orig/wchat_ref \
#     && git show HEAD:render_helper.py > .orig/render_helper.py
#   bash test_history_render.sh .orig/wchat_ref

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # 搬进 tests/ 后退回仓库根:被测源与 *_helper.py 都在根目录
SRC="${1:-$PWD/wchat_ref}"
[[ -f "$SRC" ]] || { echo "找不到被测脚本: $SRC"; exit 1; }
SRC_ABS="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"
HELP="$(dirname "$SRC_ABS")/render_helper.py"

pass=0; fail=0
ok()  { echo "  PASS $1"; pass=$((pass + 1)); }
bad() { echo "  FAIL $1${2:+ — $2}"; fail=$((fail + 1)); }

# 只提取被测函数定义(到行首 } 止),不执行脚本主体:绕开交互循环/tty/网络依赖,
# 也不会像"整跑 wchat -l"那样因为 reset 失败而根本没执行到 get_history(假绿)
eval "$(awk '/^clip_line_width\(\)/,/^}$/' "$SRC_ABS")" 2>/dev/null
eval "$(awk '/^render_markdown\(\)/,/^}$/' "$SRC_ABS")" 2>/dev/null
eval "$(awk '/^get_history\(\)/,/^}$/' "$SRC_ABS")" 2>/dev/null

TH="$(mktemp -d)"; trap 'rm -rf "$TH"' EXIT
RENDER_HELPER="$HELP"
HISTORY_DIR="$TH"
inp_msg()       { printf '%s\n' "$*"; }   # get_history 尾部的副作用一律打桩
inject_skills() { :; }
type get_history >/dev/null 2>&1 || { echo "没能从 $SRC_ABS 抽出 get_history"; exit 1; }

echo "被测: $SRC_ABS"
if [[ -f "$HELP" ]] && python3 -c 'import rich' 2>/dev/null; then
    echo "渲染: rich 助手 $HELP"
else
    echo "渲染: 纯文本回退(本机缺 rich,测试仍有效 —— 断点发生在 sed 阶段)"
fi

# ---------- T1 渲染链路完整性 ----------
echo 'T1 加载历史:含超长中文行时尾部消息不能丢'
python3 - "$TH/h.json" <<'PY'
import json, sys
long_line = '滑' * 120                      # 360 字节 > 200 限宽,旧写法从字符中间切开
msgs = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "头部消息HEADMARK"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "run_shell_command",
                                  "arguments": "{\"command\":\"cat x\"}"}}]},
    {"role": "tool", "tool_call_id": "c1",
     "content": "输出\n%s\n第二行\n第三行\n第四行\n第五行\n第六行" % long_line},
    {"role": "user", "content": "尾部消息TAILMARK"},
]
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps({"messages": msgs}, ensure_ascii=False))
PY
get_history "$TH/h.json" > "$TH/t1.out" 2> "$TH/t1.err"
if grep -q 'illegal byte sequence' "$TH/t1.err"; then
    bad "T1a 渲染管道中断" "sed: illegal byte sequence"
else
    ok "T1a 渲染管道未中断"
fi
if grep -q 'HEADMARK' "$TH/t1.out" && grep -q 'TAILMARK' "$TH/t1.out"; then
    ok "T1b 头部与尾部消息都渲染出来"
else
    bad "T1b 消息丢失" "head=$(grep -c HEADMARK "$TH/t1.out") tail=$(grep -c TAILMARK "$TH/t1.out")"
fi

# ---------- T2 截断合法性 ----------
echo 'T2 clip_line_width 不产生非法 UTF-8'
if ! type clip_line_width >/dev/null 2>&1; then
    bad "T2" "被测文件没有 clip_line_width(修复前的按字节硬切正是 bug 源头)"
else
    t2bad=0
    for w in 200 500 2000; do
        for rep in 67 100 200 700; do          # 3*rep 字节,截点必然常落在字符中间
            python3 -c "print('中'*$rep)" | clip_line_width "$w" '…(该行过长已截断)' \
                | python3 -c "import sys; sys.stdin.buffer.read().decode('utf-8')" 2>/dev/null \
                || { echo "    BROKEN w=$w rep=$rep"; t2bad=$((t2bad + 1)); }
        done
    done
    python3 -c "print('a😀中'*300)" | clip_line_width 200 '…' \
        | python3 -c "import sys; sys.stdin.buffer.read().decode('utf-8')" 2>/dev/null \
        || { echo "    BROKEN emoji/中英混排"; t2bad=$((t2bad + 1)); }
    # 输入本身已损坏时:不许崩、不许卡死(旧历史文件里可能已存着坏字节)
    printf 'bad\xffbytes here and more text to exceed the limit definitely yes' \
        | clip_line_width 20 '…' >/dev/null 2>&1 \
        || { echo "    坏字节输入下退出码非 0"; t2bad=$((t2bad + 1)); }
    (( t2bad == 0 )) && ok "T2 各宽度/emoji/坏字节输入全部安全" || bad "T2" "有 $t2bad 处不合法"
fi

# ---------- T3 tool_calls 渲染 ----------
echo 'T3 工具调用必须渲染出来,tool 段要标出工具名'
python3 - "$TH/tc.json" <<'PY'
import json, sys
msgs = [
    {"role": "system", "content": "sys"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {
            "name": "run_shell_command",
            "arguments": json.dumps({"command": "ls -la", "reason": "查看目录内容以定位中文长行" * 40},
                                    ensure_ascii=False)}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "总用量 8"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c2", "type": "function", "function": {
            "name": "read_file", "arguments": "{\"path\":\"中文文件.txt\"}"}}]},
    {"role": "tool", "tool_call_id": "c2", "content": "文件内容"},
]
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps({"messages": msgs}, ensure_ascii=False))
PY
get_history "$TH/tc.json" > "$TH/t3.out" 2> "$TH/t3.err"
# rich 会在标题里插样式转义,直接 grep 原始字节会漏匹配,先剥 ANSI 再断言
python3 - "$TH/t3.out" <<'PY' > "$TH/t3.plain"
import re, sys
t = open(sys.argv[1], "rb").read().decode("utf-8", "replace")
t = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", t)
sys.stdout.write(re.sub(r"[\r\x08]", "", t))
PY
t3bad=0
grep -q '🔧 run_shell_command' "$TH/t3.plain" || { bad "T3a 工具调用未渲染(只带 tool_calls 的 assistant 变成空标题)"; t3bad=1; }
grep -q '🔧 read_file' "$TH/t3.plain" || { bad "T3b 第二个工具调用未渲染"; t3bad=1; }
# 判据用"渲染后"的形态:rich 把 "## " 当标题标记消费掉了(实测原始字节为
# \x1b[4;35mtool → run_shell_command\x1b[0m + 补齐空格),拿渲染前的字面量
# "## tool → x" 去匹配渲染输出必然假 FAIL —— 这正是上一版 T3c/T3d 的错。
grep -q 'tool → run_shell_command' "$TH/t3.plain" || { bad "T3c tool 段没标出来源工具名"; t3bad=1; }
grep -q 'tool → read_file' "$TH/t3.plain" || { bad "T3d 第二个 tool 段没标对工具名"; t3bad=1; }
# 更狠的一条:标注在但名字是空的(🔧 : / tool → )等于没渲染,肉眼最易误判成"不渲染"
grep -qE '🔧[[:space:]]*:' "$TH/t3.plain" && { bad "T3f 🔧 行工具名为空(id→名字映射没接上)"; t3bad=1; }
grep -qE 'tool →[[:space:]]*$' "$TH/t3.plain" && { bad "T3g tool 标题工具名为空"; t3bad=1; }
# 参数里的中文长行同样不许切坏(走 clip_line_width 限宽)
if python3 -c "
import sys
sys.stdin.buffer.read().decode('utf-8')" < "$TH/t3.out" 2>/dev/null; then
    (( t3bad == 0 )) && ok "T3 两个调用都渲染、tool 段标注正确、输出合法 UTF-8"
else
    bad "T3e 渲染输出含非法 UTF-8"
fi

# ---------- T4 awk 字符计数 ----------
echo 'T4 u8len 按字符计预算(不能用 BSD awk 的字节 length)'
if eval "$(grep '^_AWK_U8LEN=' "$SRC_ABS")" && [[ -n "${_AWK_U8LEN:-}" ]]; then
    python3 -c "
import random
random.seed(11)
for _ in range(120):
    print(''.join(random.choice('中文测试字abc123 🎉.,-') for _ in range(random.randint(0, 45))))
" > "$TH/u8.txt"
    LC_ALL=C awk "$_AWK_U8LEN"'{ print u8len($0) }' "$TH/u8.txt" > "$TH/u8.awk"
    if python3 - "$TH/u8.txt" "$TH/u8.awk" <<'PY'
import sys
got = [int(l) for l in open(sys.argv[2], encoding="utf-8")]
exp = [len(l.rstrip("\n")) for l in open(sys.argv[1], encoding="utf-8")]
bad = [(i, g, e) for i, (g, e) in enumerate(zip(got, exp)) if g != e]
if bad:
    print("    u8len 与字符数不符 %d 行, 例(行,awk,期望): %s" % (len(bad), bad[:3]))
    sys.exit(1)
print("    %d 行随机中英混排逐一相等" % len(exp))
PY
    then
        ok "T4a u8len 按字符计数正确(旧写法按字节会让中文额度缩水 3 倍)"
    else
        bad "T4a u8len 计数错误"
    fi
    [[ "$(LC_ALL=C awk "$_AWK_U8LEN"'BEGIN{s="中文中文"; print u8len(s) " " length(s) }' "$TH/u8.txt")" == "4 12" ]] \
        && ok "T4b 中文串:u8len=4 字节 length=12(证明二者确实不同)" \
        || bad "T4b u8len 计算异常"
else
    bad "T4" "被测文件里没有 _AWK_U8LEN(三处 awk 预算仍按字节计)"
fi

# ---------- T5 bash 3.2 兼容 ----------
# 注意断言范围:wchat_ref 第 65-76 行会 exec 切到 Homebrew bash 5.x,所以脚本里
# 确有合法的 4+ 语法(如第 536 行注释说明的那处 declare -A)。整文件一刀切地禁
# 4+ 语法是错的断言。真正必须守 3.2 的是"本测试直接 eval 出来跑"的那几个函数
# —— 测试没有走启动器,跑在本机 3.2 上,函数里出现 4+ 语法就会当场炸。
echo 'T5 被测函数在 bash 3.2 下可用(启动器 exec 到 5.x 是另一回事)'
t5f="$TH/bash32_scope.sh"
{ awk '/^clip_line_width\(\)/,/^}$/' "$SRC_ABS"
  awk '/^render_markdown\(\)/,/^}$/' "$SRC_ABS"
  awk '/^get_history\(\)/,/^}$/' "$SRC_ABS"
  awk '/^history_handler\(\)/,/^}$/' "$SRC_ABS"
} > "$t5f" 2>/dev/null
t5hits=$(grep -n -E 'declare -A|local -A|mapfile|readarray|\$\{[A-Za-z_]+\^\^|\$\{[A-Za-z_]+,,' "$t5f" \
    | grep -v -E '^[0-9]+: *(#|//)' | head -5)
# 同时确认启动器的 bash 5 切换守卫还在:它没了,文件里那些 4+ 语法就真会变成线上事故
if ! grep -q 'BASH_VERSINFO\[0\].*< 5' "$SRC_ABS"; then
    bad "T5c 找不到 exec 切换 bash 5 的守卫,文件级 4+ 语法失去保护"
fi
if (( ${BASH_VERSINFO[0]} >= 4 )); then
    [[ -z "$t5hits" ]] && ok "T5 未发现 bash 4+ 专有语法 (本机 bash $BASH_VERSION)" \
        || bad "T5 存在 bash 4+ 专有语法" "$(printf '%s\n' "$t5hits" | head -2 | tr '\n' ' ')"
elif [[ -z "$t5hits" ]]; then
    ok "T5 被测函数无 4+ 语法,且确已在 bash $BASH_VERSION(3.2)上跑通 T1-T3"
else
    bad "T5 被测函数含 bash 4+ 专有语法(macOS 自带 3.2 会当场失败)" \
        "$(printf '%s\n' "$t5hits" | head -2 | tr '\n' ' ')"
fi

# ---------- 对照:修复前的按字节硬切 ----------
echo '对照:旧写法 substr($0,1,w)'
python3 -c "print('中'*200)" | LC_ALL=C awk -v w=200 \
    '{ if (length($0)>w) print substr($0,1,w) "…"; else print }' \
    | python3 -c "
import sys
try:
    sys.stdin.buffer.read().decode('utf-8'); print('  VALID(意外:与预期不符)')
except UnicodeDecodeError:
    print('  BROKEN 已确认:旧写法把字符切成半个 → 非法 UTF-8')"

printf '\n结果:PASS=%d FAIL=%d\n' "$pass" "$fail"
(( fail == 0 )) || exit 1
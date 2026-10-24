#!/usr/bin/env bash
# SKILLS_DIR 外置闸门:环境变量覆盖 / 三条目录校验(绝对路径+非链接、属主、o+w 与父目录)
# / 退回默认目录 / 外置标记 / 链接守卫配对 / -skill 的 name 清洗 / 文档与行号范围回归。
#
# 用法: bash tests/skills_dir_test.sh
#       bash tests/skills_dir_test.sh --source .orig/wchat_ref   # 打基线对照(负对照用)
#
# 为什么单独一个闸门:技能目录的内容会进 **system prompt**(name+description 由
# scan_skills 注入,正文由 read_skill 整篇读入)。这条链一旦配错,现象不是"报错",
# 而是"模型开始照着别人的话干活",事后极难归因。所以这里判的是"生效目录是谁"和
# "被拒时是否真的退回",不是"函数能不能跑通"。
#
# 抽实现一律按模式,不写死行号(与 tests/vision_layer_test.sh 同一口径)。
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash "${HOME}/.linuxbrew/bin/bash"; do
        [[ -x "$_b" ]] || continue
        _v="$("$_b" -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null || true)"
        [[ "$_v" =~ ^[0-9]+$ ]] || _v=0
        (( _v >= 5 )) && exec "$_b" "$0" "$@"
    done
    echo "❌ 需要 bash 5+(与生产一致);或 WCHAT_BASH=/path/to/bash5 $0" >&2
    exit 1
fi
export LC_ALL=C
# 被测源文件默认取**本仓库那份**(脚本在 tests/ 下,根目录是其父目录)。
# 原先写死 /Users/jwan/bin/wchat_ref:那个路径只在这台机器的原布局下成立,别人
# clone 下来副本就跑不动 —— 与 tty_e2e_check.sh 同一句注释所记的"换机即挂"同源。
# 仍保留 REAL 覆盖点供负对照/异机使用。
REAL=${REAL:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/wchat_ref"}
# 负对照开关:--source 指一份未打补丁的实现,除"实现已含 X"之外的行为断言会红
if [[ "${1:-}" == "--source" && -n "${2:-}" ]]; then REAL="$2"; fi
[[ -f "$REAL" ]] || { echo "❌ 找不到被测源文件:$REAL"; exit 1; }
# 子进程统一用**当前这个** bash(顶层已 re-exec 到 5+,$BASH 就是它);写死 bash 会掉回
# macOS 自带 3.2,测的就不是生产行为。
RB="$BASH"
W=$(mktemp -d)
FH="$W/home"            # 假 HOME,默认目录落这里,绝不碰 ~/.ct
mkdir -p "$FH/.ct"
fail=0
ck() { [[ "$2" == "$3" ]] && echo "  ✓ $1" || { echo "  ✗ $1"; echo "    期望 [$3]"; echo "    实际 [$2]"; fail=$((fail + 1)); }; }
has() { [[ "$2" == *"$3"* ]] && echo "  ✓ $1" || { echo "  ✗ $1"; echo "    缺少: $3"; echo "    实际: $2"; fail=$((fail + 1)); }; }
hasnt() { [[ "$2" != *"$3"* ]] && echo "  ✓ $1" || { echo "  ✗ $1"; echo "    不该出现: $3"; echo "    实际: $2"; fail=$((fail + 1)); }; }

# ───────────── 抽沙箱:stat_num + 技能目录定义到"显式外置"落地为止 ─────────────
# 区间终点选 ^unset _skills_req$ 而不是 ^mkdir:后者的 mkdir -p "$HISTORY_DIR" 会在假
# HOME 下造 history/,虽无害但会把"迁移旧 json"那段之外的副作用引进来,测试不该有副作用。
{
    awk '/^stat_num\(\) \{/{p=1} p{print} p && /^}$/{exit}' "$REAL"
    awk '/^SKILLS_DIR_DEFAULT=/{p=1} p{print} p && /^unset _skills_req$/{exit}' "$REAL"
    printf 'printf "%%s|%%s\\n" "$SKILLS_DIR" "$SKILLS_DIR_EXTERNAL"\n'
} > "$W/lib.sh"

# 防空转(上一类事故:抽取区间落空 → 变量全空 → "不该出现"类断言集体假绿)
for probe in 'skills_dir_validate "$_skills_req"' 'SKILLS_DIR_EXTERNAL=0' 'skills_dir_reject()' 'stat_num()' '16#$mode'; do
    grep -qF -- "$probe" "$W/lib.sh" || { echo "❌ 沙箱漏抽 [$probe],后面所有断言都是假的"; exit 1; }
done
ck "抽取区间不含 mkdir 副作用" "$(grep -c 'mkdir -p "\$HISTORY_DIR"' "$W/lib.sh")" "0"

# 解析一次生效目录:$1=SKILLS_DIR 请求值(空串=不设) $2=cwd;stdout="目录|外置标记",stderr 落 $W/err
resolve() {
    local req="$1" cwd="${2:-$W}"
    ( cd "$cwd" && env -u SKILLS_DIR ${req:+SKILLS_DIR="$req"} CT_HOME="$FH/.ct" \
        "$RB" "$W/lib.sh" ) 2>"$W/err"
}
err() { cat "$W/err"; }

echo "=== 1. 默认路径(未设环境变量)必须与改造前完全一致 ==="
r=$(resolve "")
ck "未设时落 ~/.ct/skills(假 HOME 下)" "${r%%|*}" "$FH/.ct/skills"
ck "未设时不算外置(不加任何新判定)" "${r##*|}" "0"
hasnt "未设时不刷校验告警" "$(err)" "校验未通过"

echo "=== 2. 合法外置:目录被采纳,且标记 external ==="
EXT="$W/proj/.skills"; mkdir -p "$EXT"; chmod 755 "$EXT"
r=$(resolve "$EXT")
ck "采纳外置目录" "${r%%|*}" "$EXT"
ck "标记为外置(逐条目链接守卫随之生效)" "${r##*|}" "1"
hasnt "合法目录不该有告警" "$(err)" "校验未通过"
# 4 位 mode(sticky,非 o+w)也要能采纳:stat_num 在部分实现会回 "1755"
S4="$W/sticky"; mkdir -p "$S4"; chmod 1755 "$S4"
r=$(resolve "$S4"); ck "sticky 四位权限 1755 被采纳(没被正则误杀)" "${r%%|*}" "$S4"
# 八进制陷阱回归:mode 串进 (( )) 按十进制解释时,755&2=2 → 健康目录会被误退回。
# 这条是本次改动最容易写错又最容易"看起来在跑"的地方,单独立一条。
ck "755 未被误判为全局可写(16# 前缀在位)" "$(grep -c '(( 16#\$mode & 02 ))' "$REAL")" "1"

echo "=== 3. 相对路径按启动目录归一 ==="
# 期望值取**物理路径**:实现用的是 pwd -P,macOS 下 /var → /private/var,
# 归一结果必须是物理形式,否则同一目录会以两种写法进注入(与硬墙 norm_path 同一口径)。
mkdir -p "$W/proj2"
PROJ2_PHYS=$(cd "$W/proj2" && pwd -P)
r=$(resolve ".skills" "$W/proj2")
ck "相对路径归一为 \$PWD/.skills(物理路径)" "${r%%|*}" "$PROJ2_PHYS/.skills"
ck "归一后目录确实被创建" "$([[ -d "$W/proj2/.skills" ]] && echo yes)" "yes"

echo "=== 4. 三条校验各自必须退回默认目录 ==="
DEF="$FH/.ct/skills"
# 4a 符号链接
mkdir -p "$W/elsewhere"; ln -s "$W/elsewhere" "$W/linkdir"
r=$(resolve "$W/linkdir")
ck "链接目录被退回默认" "${r%%|*}" "$DEF"
ck "被拒时外置标记保持 0" "${r##*|}" "0"
has "告警点名符号链接" "$(err)" "符号链接"
has "告警同时打出被拒值与生效值(否则人不知改哪)" "$(err)" "生效:"
# 4b 目录自身 o+w
OW="$W/ownw"; mkdir -p "$OW"; chmod 777 "$OW"
r=$(resolve "$OW"); ck "o+w 目录被退回" "${r%%|*}" "$DEF"
has "告警点名全局可写" "$(err)" "全局可写"
# 4c 父目录 o+w(把整包技能 rename 走的攻击面)
mkdir -p "$W/parw/inner"; chmod 777 "$W/parw"; chmod 755 "$W/parw/inner"
r=$(resolve "$W/parw/inner"); ck "父目录 o+w 被退回" "${r%%|*}" "$DEF"
has "告警点名父目录" "$(err)" "父目录"
# 4d 属主不是自己:/usr/share 在 macOS/Linux 都是 root 所有
r=$(resolve "/usr/share"); ck "他人属主目录被退回" "${r%%|*}" "$DEF"
has "告警点名属主" "$(err)" "属主"
# 4e 空/纯斜杠:绝不把 / 当技能目录
r=$(resolve "///"); ck "纯斜杠被退回" "${r%%|*}" "$DEF"
has "告警点名不能为根" "$(err)" "为空"
# 4f 无法创建(父目录不可写)
mkdir -p "$W/ro"; chmod 555 "$W/ro"
r=$(resolve "$W/ro/sub"); ck "创建失败时退回默认(不是退出)" "${r%%|*}" "$DEF"
chmod 755 "$W/ro"
ck "拒绝路径不会被留下垃圾目录" "$([[ -e "$W/ro/sub" ]] && echo yes || echo no)" "no"

echo "=== 5. 显式设成默认目录不算外置(dotfiles 里写死同一路径是常见配置) ==="
r=$(resolve "$DEF")
ck "生效值仍是该目录" "${r%%|*}" "$DEF"
ck "但不打外置标记(默认目录归人管,不该被链接守卫改判)" "${r##*|}" "0"

echo "=== 6. 链接守卫必须三侧配对(只堵一侧=列表里没有但凭记忆还能读/写穿) ==="
# scan_skills / tool_read_skill 已**收口**(守卫移入 wchat_py/skill.py);skill_save 仍并存(守卫在 bash 侧)。
PYSC="$(dirname "$REAL")/wchat_py/skill.py"
ck "scan_skills 已收口(不再内联守卫)" "$(awk '/^scan_skills\(\) \{/{p=1} p{print} p && /^}$/{exit}' "$REAL" | grep -c 'f%/SKILL.md')" "0"
ck "scan_skills 外置标志仍传给 Python" "$(awk '/^scan_skills\(\) \{/{p=1} p{print} p && /^}$/{exit}' "$REAL" | grep -c 'SKILLS_DIR_EXTERNAL')" "1"
ck "tool_read_skill 外置标志仍传给 Python" "$(awk '/^tool_read_skill\(\) \{/{p=1} p{print} p && /^}$/{exit}' "$REAL" | grep -c 'SKILLS_DIR_EXTERNAL')" "1"
ck "链接守卫在 Python 侧(scan+read 共 ≥4 处)" "$([[ "$(grep -o 'islink' "$PYSC" | wc -l | tr -d ' ')" -ge 4 ]] && echo ge4 || echo lt4)" "ge4"
ck "skill_save 写侧守卫仍在(该模块未收口)" "$(grep -c '的目录或 SKILL.md 是符号链接' "$REAL")" "1"

# 行为断言已**移到 tests/py_skill_test.sh**:那里按 ext=0/1 跑**真 scan_skills**(经 Python),
# 看链接条目是否被跳过(抽函数体跑的老办法已不可用 —— scan_skills 收口后函数体只剩一行 py_run,
# 抽出来跑会因缺 _py_on 而空转)。

echo "=== 7. -skill 的 name 清洗:写侧此前缺 read_skill 那条「拒 ..」判据 ==="
clean_line=$(grep -E "^ +name=\\$\\(printf '%s' \"\\\$name\" \\| tr '\\[:upper:\\]' '\\[:lower:\\]'" "$REAL")
ck "清洗语句唯一(测试跟着它跑;多了就是在复制第二套实现)" "$(printf '%s\n' "$clean_line" | grep -c .)" "1"
ck "纯点号兜底判据在位且唯一" "$(grep -cF '[[ "$name" =~ ^\.+$ ]] && name="skill"' "$REAL")" "1"
# 直接喂实现里那两行原文跑,测试里不再抄一份 sed 表达式(抄的那份会漂)
clean_run() {
    { printf 'name=%q\n' "$1"
      printf '%s\n' "$clean_line"
      printf '[[ "$name" =~ ^\\.+$ ]] && name="skill"\n'
      printf 'printf "%%s\\n" "$name"\n'; } | "$RB"
}
for probe_in in ".." "..." "." "a/../../b" "SKILL ME" "--" "..ct..SKILL"; do
    got=$(clean_run "$probe_in")
    hasnt "输入[$probe_in] 清洗后不含斜杠(穿不出技能目录)" "$got" "/"
    ck  "输入[$probe_in] 清洗后不是纯点号" "$([[ "$got" =~ ^\.+$ ]] && echo dot || echo ok)" "ok"
done
ck "输入[..] 收敛成 skill(不再是父目录)" "$(clean_run '..')" "skill"
ck "输入[.] 同样收敛(不再指向技能目录自身)" "$(clean_run '.')" "skill"
ck "正常名字不被误伤" "$(clean_run 'cad-dfam-check')" "cad-dfam-check"

echo "=== 8. read_skill 的 name 白名单不许被削弱 ==="
# tool_read_skill 已收口 → name 白名单在 wchat_py/skill.py(仍是"拒 / 与 ..")
ck "python 侧仍拒斜杠" "$(grep -c '"/" in name' "$PYSC")" "1"
ck "python 侧仍拒 .." "$(grep -c '"\.\." in name' "$PYSC")" "1"

echo "=== 9. 文档口径与实现一致(注释说不能覆盖、代码却能覆盖,是下一轮误判的源头) ==="
has "① 类(env 可覆盖)登记了 SKILLS_DIR" "$(awk '/^# ① 可用/{p=1} /^# ② /{p=0} p' "$REAL")" "SKILLS_DIR"
hasnt "③ 类(设了也没用)不再列 SKILLS_DIR" "$(awk '/^# ③ 派生量/{p=1} /^# ----/{p=0} p' "$REAL")" "SKILLS_DIR"
sk_help=$(grep -F 'inp_msg "  -skill' "$REAL")
ck "-h 里两条 -skill 都在" "$(printf '%s\n' "$sk_help" | grep -c .)" "2"
hasnt "-h 不再硬编码 ~/.ct/skills" "$sk_help" '~/.ct/skills'
# 生效目录必须用变量表达(写死 = 外置后屏幕告诉用户一个错的地方,比不告诉更糟)
ck "-h 用 \${SKILLS_DIR} 变量而非字面目录" "$(grep -c '\${SKILLS_DIR}' "$REAL")" "2"
note=$(grep -F '当前技能目录' "$REAL")
has "说明行同时给出默认值(用户能看懂「外置」相对什么)" "$note" '${SKILLS_DIR_DEFAULT}'
ck "落地用的 mkdir 仍在(别把默认目录创建删了)" "$(grep -c 'mkdir -p "\$HISTORY_DIR" "\$SKILLS_DIR"' "$REAL")" "1"
ck "SKILLS_DIR_DEFAULT 定义唯一" "$(grep -c '^SKILLS_DIR_DEFAULT=' "$REAL")" "1"

echo "=== 10. 行号范围断言的护栏(tests/vision_layer_test.sh 第 8/9 节按区间取文) ==="
# 护栏的本意是"上面加了注释会不会把别人的取文区间挤歪"。实测它自己就是第一个受害者:
# HEAD 上 '1,60p' 已经框不住第 65 行的「鉴权头」说明,红了两条与产品无关的假失败。现在
# vision_layer_test.sh 第 8/9 节改用**内容锚**,这里镜像同一套表达式 —— 行号漂移不再致红,
# 而"说明被删/关键词被改"仍然两边一起红。剩下这条仍是区间的(取文件头 25 行查有无 404 字样):
# 它的目标就是"文件最开头",插入式漂移动不到它,保留。
hasnt "1,25 仍含图片侧无探测" "$(sed -n '1,25p' "$REAL")" "404"
ck  "1,25 含「图片侧已无厂商探测」" "$(sed -n '1,25p' "$REAL" | grep -c '图片侧已无厂商探测')" "1"
ck  "锚:配置区注释仍提 mimo LLM 双头"        "$(grep -cE '^[[:space:]]*#.*mimo LLM' "$REAL")" "1"
ck  "锚:「鉴权头(勿删)」marker 仍唯一"        "$(grep -c '鉴权头(勿删)' "$REAL")" "1"
ck  "锚:鉴权头块内仍点 MiMo(与上两条配对)"   "$(grep -A16 '鉴权头(勿删)' "$REAL" | grep -c 'MiMo')" "1"
ck  "语法通过" "$(bash -n "$REAL" 2>&1 | grep -c . )" "0"

rm -rf "$W"
echo
if (( fail )); then echo "❌ SKILLS_DIR 闸门 $fail 项失败"; exit 1; fi
echo "✅ SKILLS_DIR 闸门全部通过"
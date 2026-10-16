#!/usr/bin/env bash
# 压缩布局探针:不联网络,只验压缩的"历史结构"那半边 —— summary_pin_count 与 compact_apply。
#
# 为什么要单独一个探针文件:发摘要请求要走真实接口,硬墙全表(527 条)按名字抽函数抽不到
# 它,于是 5b 要钉的那件事("旧摘要有没有被原样保留、失败时有没有被一起丢掉")只能靠人在
# 长会话里撞运气。把布局从网络里剥出来当纯函数,这件事才有回归保护。
#
# 用法:bash tests/compaction_pin_probe.sh
set -uo pipefail
# 支持传入被测来源(与 test_hardwall.sh 同惯例):变异装置要靠它把断言跑到变异体上
SRC=${1:-}
[[ -n "$SRC" ]] || SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wchat.sh"
[[ -f "$SRC" ]] || { printf '找不到被测脚本:%s\n' "$SRC"; exit 1; }
TMP=$(mktemp -d "${TMPDIR:-/tmp}/cpp.XXXXXX"); trap 'rm -rf "$TMP"' EXIT

# 只抽需要的:三个配置 + 两个纯函数(按名字抽函数体,到行首 } 止)
# ⚠ 名单必须含 _JQ_* 共享片段:summary_pin_count 的 jq 程序里拼了 $_JQ_CONTENT_TEXT,
#   夹具不登记 → 沙箱里它是空串 → jq 编译失败 → 函数返回空 → 断言全红(而不是假绿)。
#   这是 bugList #47「漏登记 auto_install_allowed → H14 放行分支永远走不到」的同款病:
#   被测函数一依赖全局,抽取名单就是合同,漏一项等于测的是另一个程序。
eval "$(grep -E '^(KEEP_MSGS|SUMMARY_PREFIX|SUMMARY_PIN_MAX|_JQ_CONTENT_TEXT|_JQ_HAS_IMAGE)=' "$SRC")"
for _f in compact_due summary_pin_count compact_apply; do
    awk -v fn="$_f" '$0 ~ "^"fn"\\(\\) \\{" {p=1} p {print} p && /^\}$/ {exit}' "$SRC" > "$TMP/$_f.sh"
    # shellcheck disable=SC1090
    source "$TMP/$_f.sh"
    declare -F "$_f" >/dev/null 2>&1 || { printf '抽取失败:%s(函数改名或签名变了?)\n' "$_f"; exit 1; }
done
[[ -n "${SUMMARY_PREFIX:-}" ]] || { echo "SUMMARY_PREFIX 没抽到,后面的断言都是假绿"; exit 1; }
[[ -n "${_JQ_CONTENT_TEXT:-}" ]] || { echo "_JQ_CONTENT_TEXT 没抽到,summary_pin_count 的断言全是假绿"; exit 1; }
# 迁移通道:compact_due/compact_apply **已收口**(Python 唯一活实现)→ 让 `_py_on` 恒真、
# `py_run` 直连启动器,抽出来的函数体照常走 Python(summary_pin_count 未收口,不碰 _py_on)。
_py_on() { return 0; }
py_run() { local _m="$1"; shift; python3 "$(dirname "$SRC")/wchat_py/_run.py" "$_m" "$@"; }

pass=0 fail=0
ck() {  # $1=说明 $2=实得 $3=期望
    if [[ "$2" == "$3" ]]; then pass=$((pass + 1)); printf '  ok   %s\n' "$1"
    else fail=$((fail + 1)); printf '  FAIL %s:得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi
}

# 夹具:system + 2 条连续旧摘要 + 1 条普通 + 1 条"前缀不在开头"的干扰消息
m=$(jq -n --arg p "$SUMMARY_PREFIX" '[
      {role:"system",content:"s"},
      {role:"user",content:($p+"\n旧1")},
      {role:"user",content:($p+"\n旧2")},
      {role:"user",content:"普通消息"},
      {role:"user",content:("x"+$p+"不是开头的摘要")} ]')
# 旧摘要 1 的原文,后面用来验"逐字保留"
o1=$(jq -rn --arg p "$SUMMARY_PREFIX" '$p+"\n旧1"')

echo "compact_due:要不要压(两处触发点与 compact_messages 共用这一份口径)"
# 阈值显式设成本地值:这一档测的是**判据形状**,与产品默认派生式无关
# (CTX_WINDOW→TOKEN_THRESHOLD 的派生关系已由 test_hardwall.sh 的"三数关系"结构锁钉住)
TOKEN_THRESHOLD=100000
_due() { if compact_due "$1"; then echo 压; else echo 不压; fi; }
ck "t=0 不压(新进程尚无精确值 —— -st 单轮的边界形状)" "$(_due 0)"        "不压"
ck "阈值下一格不压"                                "$(_due 99999)"       "不压"
ck "正好等于阈值不压(严格 >,不是 >=)"              "$(_due 100000)"      "不压"
ck "超一格就压"                                    "$(_due 100001)"      "压"
ck "阈值被裁空时不压(半装载不许每轮都压)"          "$(TOKEN_THRESHOLD= _due 500000)" "不压"
ck "阈值为 0 时不压(同上,烧钱且每次多一层摘要)"    "$(TOKEN_THRESHOLD=0 _due 500000)" "不压"
ck "t 为空视为无基准,不压"                          "$(_due '')"         "不压"
ck "t 不是数字也不炸、判不压"                       "$(_due abc)"          "不压"

echo "接线锁:判据对不代表调用点还在、还在该在的位置"
# 这几条一律按"代码形态"数,不按 compact_due 出现次数 —— 注释里也写得到函数名,
# 按次数数会变成"改一行注释就把闸门弄红"的假敏感。
_d1=$(grep -c '^compact_due() {' "$SRC" || true)
ck "定义恰好 1 处" "$_d1" "1"
_d2=$(grep -cF 'compact_due "$last_prompt_tokens"' "$SRC" || true)
ck "调用恰好 3 处(compact_messages 选提示语 + 两处触发点)" "$_d2" "3"
_tg=$(grep -c '&& compact_messages' "$SRC" || true)
ck "触发点恰好 2 处(少一处就是有人把 -st 那条路径又弄丢了)" "$_tg" "2"
_raw=$(grep -cF '(( last_prompt_tokens > TOKEN_THRESHOLD ))' "$SRC" || true)
ck "没有裸判据残留(这条判据不许再被抄回调用点)" "$_raw" "0"
# 触发点必须排在"发请求"之前 —— 挪到 ask_ai 之后等于回到"永不触发"(见 e67ac6a)
_tp=$(grep -nF 'compact_due "$last_prompt_tokens" && compact_messages' "$SRC" | cut -d: -f1 | tr '\n' ' ')
_bad=0
for _l in $_tp; do
    awk -v s="$_l" 'NR>s && NR<=s+6 && /make_request_body/ {f=1} END{exit f?0:1}' "$SRC" || _bad=$((_bad + 1))
done
ck "每个触发点都在 make_request_body 之前 6 行内" "$_bad" "0"

echo "summary_pin_count:只数 system 之后**连续**的旧摘要"
ck "连续 2 条被数出"                "$(summary_pin_count "$m")" "2"
ck "无摘要时是 0"                   "$(summary_pin_count '[{"role":"system","content":"s"},{"role":"user","content":"x"}]')" "0"
ck "system 自己带前缀不算(只从 .[1:] 起数)" \
   "$(summary_pin_count "$(jq -n --arg p "$SUMMARY_PREFIX" '[{role:"system",content:($p+"伪装")}]')")" "0"
ck "前缀不在开头的不算摘要"          "$(summary_pin_count '[{"role":"system","content":"s"},{"role":"user","content":"x"}]')" "0"

# ── 带图消息(数组型 content)──────────────────────────────────────────
# 这几条是"真多模态"的前置守卫。旧实现下它们**不是红,而是静默错**:数组 content 让
# startswith 报 rc=5 → 函数输出空 → 调用处兜成 pin=0 → 旧摘要失去锁定,压缩一路做
# 套叠摘要,而屏幕上一句错都不报。所以断言要钉在形状上,不能赌"模型不会读图"。
echo "带图消息:数组型 content 不许把锁定计数打成空/0"
_mi_after=$(jq -n --arg p "$SUMMARY_PREFIX" '[
    {role:"system",content:"s"},
    {role:"user",content:($p+"\n旧1")},
    {role:"tool",tool_call_id:"c1",content:[
        {type:"image_url",image_url:{url:"data:image/png;base64,AAAA"}},
        {type:"text",text:"这是原图,请核对线号"}]}]')
ck "摘要后紧跟一条带图消息:仍认出 1 条锁定" "$(summary_pin_count "$_mi_after")" "1"
ck "取数不许返回空串(空=假 0,会废掉锁定机制)" \
   "$([[ -n "$(summary_pin_count "$_mi_after")" ]] && echo nonempty || echo EMPTY)" "nonempty"
_mi_only=$(jq -n '[{role:"system",content:"s"},
    {role:"tool",tool_call_id:"c1",content:[{type:"image_url",image_url:{url:"data:image/png;base64,AA"}}]}]')
ck "纯图消息(无 text 部件)不算摘要,但必须是数字 0" "$(summary_pin_count "$_mi_only")" "0"
_mi_arrsum=$(jq -n --arg p "$SUMMARY_PREFIX" '[{role:"system",content:"s"},
    {role:"user",content:[{type:"text",text:($p+"\n数组形状的摘要")}]}]')
ck "归一口径对数组同样生效(数组里的摘要前缀要认)" "$(summary_pin_count "$_mi_arrsum")" "1"

# 负对照:把片段清空,模拟"全局依赖没装载"(夹具漏登记/jq 过旧都长这样)。
# 产品侧必须**出声 + 非零返回**;静默返回 0 是本函数最怕的失效形状——锁定悄悄失效,
# 压十次才看得出来。这条也顺便钉住"探针自己的装载守卫"不是唯一防线。
_ec_out=$( { _JQ_CONTENT_TEXT=""; summary_pin_count "$m"; } 2>&1 ); _ec_rc=$?
ck "片段没装载时必须出声+非零(不许静默 0)" \
   "$( (( _ec_rc != 0 )) && [[ "$_ec_out" == *取数失败* ]] && echo loud || echo "silent rc=$_ec_rc")" "loud"

# 结构锁:消息侧不许再残留"把 content 当字符串裸用"的写法(四处消费点已收敛到片段)。
# 为什么用 grep 而不是只跑行为断言:新加的消费点最容易照抄旧写法,行为断言覆盖不到"下一处"。
# ⚠ 必须剔掉注释行:文件头那段"旧写法会 rc=5"的说明本身就引用着旧形状,不剔就是
#   3 条假红(实测踩过)。判"代码里有没有"就只看代码。
# 口径为什么是"数 `(.content // "")` 恰好 1 处"而不是"必须为 0":
#   1) 全文件 .content // 还有 9 处是**解析 API 响应**(choices[0].message.content // empty),
#      那是另一回事(响应侧由 vision_parse_resp 归一),宽口径会误伤;
#   2) 消息侧的裸惯用法只该活在片段定义自己那一行。数出 1=正常;0=有人删了定义
#      (那 4 个消费点会跟着坏);≥2=有新代码照抄了旧形状。变异验证 M2 就是把这条
#      从"=0 的字面匹配"改成"=1 的计数"的起因:旧口径被带转义的植入行绕过了。
# ⚠ 局限写在这:字面锁防"照抄",防不了"故意绕"(换空格即躲开)。真正兜底的是上面
#   那组行为断言与反例断言,这条只是早一级报警。
_naked=$(sed -E 's/^[[:space:]]*#.*//' "$SRC" | grep -cF '(.content // "")')
ck "消息侧裸 content 惯用法只剩片段定义那 1 处" "$_naked" "1"
# 反例:上一条光靠"把守卫都删了"也能变绿。所以同时钉"守卫确实在被用"——
# 定义 1 处 + 消费点 4 处(summary_pin_count / compact_messages / skill_save / get_history)。
_uses=$(sed -E 's/^[[:space:]]*#.*//' "$SRC" | grep -cF '$_JQ_CONTENT_TEXT')
ck "归一口径至少有 4 个消费点在用(防'删断言消红')" "$([[ "$_uses" -ge 4 ]] && echo ge4 || echo "lt4:$_uses")" "ge4"
ck "片段定义恰好 1 处(不许再出现第二份口径)" \
   "$(grep -c '^_JQ_CONTENT_TEXT=' "$SRC")" "1"

echo "compact_apply 成功路径:锁定块原样保留,新摘要排在它后面"
messages="$m"
compact_apply 2 "新摘要正文" 4
ck "system 仍在首位"                "$(printf '%s' "$messages" | jq -r '.[0].role')" "system"
ck "旧摘要 1 逐字未改"              "$(printf '%s' "$messages" | jq -r '.[1].content')" "$o1"
ck "第二条仍是旧摘要(前缀判定交给 jq,不碰字节/字符坑)" \
   "$(printf '%s' "$messages" | jq -r --arg p "$SUMMARY_PREFIX" '.[2].content | startswith($p)')" "true"
ck "新摘要紧跟锁定块之后"           "$(printf '%s' "$messages" | jq -r '.[3].content' | tail -1)" "新摘要正文"
ck "总条数 = 1+2+1+尾1"             "$(printf '%s' "$messages" | jq 'length')" "5"
ck "尾部原文没被吞"                 "$(printf '%s' "$messages" | jq -r '.[-1].content')" "x${SUMMARY_PREFIX}不是开头的摘要"

echo "compact_apply 失败路径(摘要为空):旧摘要绝不能一起丢"
messages="$m"
compact_apply 2 "" 4
ck "锁定块 2 条摘要还在"            "$(printf '%s' "$messages" | jq --arg p "$SUMMARY_PREFIX" '([.[1:3][] | (.content|startswith($p))] | index(false) // 2)')" "2"
ck "失败时不凭空多一条摘要"          "$(printf '%s' "$messages" | jq 'length')" "4"
ck "硬截断后旧摘要 1 仍然逐字"       "$(printf '%s' "$messages" | jq -r '.[1].content')" "$o1"

echo "钳制与幂等"
messages="$m"
compact_apply 9 "越界钳制测试" 3     # pin 9 远超 cut-1=2,必须被钳到 2
ck "pin 越界后旧摘要没被数两遍"      "$(printf '%s' "$messages" | grep -o '旧1' | wc -l | tr -d ' ')" "1"
messages="$m"
compact_apply 2 "第二层" 4
ck "压完一次后能认出 3 条锁定摘要"    "$(summary_pin_count "$messages")" "3"
ck "默认锁定上限是正整数(0 表示关闭锁定,不该是默认)" \
   "$(printf '%s' "${SUMMARY_PIN_MAX:-}" | grep -qE '^[1-9][0-9]*$' && echo yes || echo no)" "yes"

# 两条汇总:人看的中文行 + 机器可读的无色彩固定字段行(与全表同惯例,变异装置按
# ^  FAIL 取差异、按 RESULT 取结论,少一条就是它读不到东西而误判"变异未被杀")
printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass + fail))" "$pass" "$fail"
printf '结果:PASS=%d FAIL=%d\n' "$pass" "$fail"
[[ $fail -eq 0 ]] || exit 1
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
eval "$(grep -E '^(KEEP_MSGS|SUMMARY_PREFIX|SUMMARY_PIN_MAX)=' "$SRC")"
for _f in compact_due summary_pin_count compact_apply; do
    awk -v fn="$_f" '$0 ~ "^"fn"\\(\\) \\{" {p=1} p {print} p && /^\}$/ {exit}' "$SRC" > "$TMP/$_f.sh"
    # shellcheck disable=SC1090
    source "$TMP/$_f.sh"
    declare -F "$_f" >/dev/null 2>&1 || { printf '抽取失败:%s(函数改名或签名变了?)\n' "$_f"; exit 1; }
done
[[ -n "${SUMMARY_PREFIX:-}" ]] || { echo "SUMMARY_PREFIX 没抽到,后面的断言都是假绿"; exit 1; }

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


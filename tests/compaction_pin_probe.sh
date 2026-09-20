#!/usr/bin/env bash
# 压缩布局探针:不联网络,只验压缩的"历史结构"那半边 —— summary_pin_count 与 compact_apply。
#
# 为什么要单独一个探针文件:发摘要请求要走真实接口,硬墙全表(527 条)按名字抽函数抽不到
# 它,于是 5b 要钉的那件事("旧摘要有没有被原样保留、失败时有没有被一起丢掉")只能靠人在
# 长会话里撞运气。把布局从网络里剥出来当纯函数,这件事才有回归保护。
#
# 用法:bash tests/compaction_pin_probe.sh
set -uo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wchat.sh"
[[ -f "$SRC" ]] || { printf '找不到被测脚本:%s\n' "$SRC"; exit 1; }
TMP=$(mktemp -d "${TMPDIR:-/tmp}/cpp.XXXXXX"); trap 'rm -rf "$TMP"' EXIT

# 只抽需要的:三个配置 + 两个纯函数(按名字抽函数体,到行首 } 止)
eval "$(grep -E '^(KEEP_MSGS|SUMMARY_PREFIX|SUMMARY_PIN_MAX)=' "$SRC")"
for _f in summary_pin_count compact_apply; do
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

printf '\n结果:PASS=%d FAIL=%d\n' "$pass" "$fail"
[[ $fail -eq 0 ]] || exit 1


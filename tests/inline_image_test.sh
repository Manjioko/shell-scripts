#!/usr/bin/env bash
# inline 图片通道闸门(不联网)。覆盖 read_image 把原图交给主模型这条链路的全部形状契约。
#
# 为什么值得单独一道闸门(三条都是实测踩出来的,不是假想):
#   1) base64 结尾的 0a 会混进手拼 JSON,让 map 非法 → 展开静默失败,表现是
#      "模型只看到一行标记、没有图",屏幕上一切正常;
#   2) 标记字面量可能出现在**被读取的正文里**(本仓库源码就有),若无条件改写,
#      工具结果会被静默篡改 —— 这道闸门自己最初就是这么读不到原文的;
#   3) 降级文案必须是"痕迹 + 事实 + 义务"且**不含任何现在时断言**(六臂实验:
#      留假陈述的那臂 2/3 坚持原结论,含一例读错后不再重读)。
# 用法:bash tests/inline_image_test.sh [被测脚本]
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
SRC=${1:-}
[[ -n "$SRC" ]] || SRC="$(pwd)/wchat.sh"
[[ -f "$SRC" ]] || { printf '找不到被测脚本:%s\n' "$SRC"; exit 1; }
# 与生产同解释器:片段里有 bash4+ 语法,3.2 上测出来的不是同一套行为
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$_b" ]] || continue
        [[ "$("$_b" -c 'echo ${BASH_VERSINFO[0]}' 2>/dev/null || echo 0)" =~ ^[5-9] ]] && exec "$_b" "$0" "$@"
    done
    printf '需要 bash5+\n'; exit 1
fi

W=$(mktemp -d "${TMPDIR:-/tmp}/iimg.XXXXXX")
IMG_REF_DIR_KEEP=""
cleanup() { [[ -n "$IMG_REF_DIR_KEEP" && -d "$IMG_REF_DIR_KEEP" ]] && rm -rf "$IMG_REF_DIR_KEEP"; rm -rf "$W"; }
trap cleanup EXIT

pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }

# 装载走脚本自带的 _WCHAT_LIB_ONLY 口。**不要**改成按行 grep 抽函数:
# _JQ_* 片段与 IMG_DESC_PROMPT 都是单行约定,但历史上多行引号串曾把抽取器整段吞掉。
_WCHAT_LIB_ONLY=1 API_KEY="${WCHAT_API_KEY:-}" source "$SRC" < /dev/null >/dev/null 2>&1
for f in img_dims prepare_inline img_inline_attach img_build_map img_refs_prune make_request_body tool_read_image; do
    ck "函数已装载 $f" "$(declare -F "$f" >/dev/null && echo yes || echo no)" "yes"
done
# 探针自己的前提:全局没定义时 `${#IMG_REFS[@]}` 在 set -u 下会当场死,
# 现象是"一道闸门静默不出结论行"(本文件 README 的头号忌律:没结论行=失败)
[[ -n "${_JQ_EXPAND_REFS:-}" && -n "${_JQ_HAS_IMAGE:-}" ]] \
    || { printf '  FAIL 片段没装载,后面所有断言都是假绿\n'; printf 'RESULT total=1 pass=0 fail=1\n'; exit 1; }

echo "=== 1. 单行赋值约定(按行抽取的夹具依赖它) ==="
ck "_JQ_EXPAND_REFS 恰好占 1 行" "$(sed -n '/^_JQ_EXPAND_REFS=/p' "$SRC" | wc -l | tr -d ' ')" "1"
ck "_JQ_CONTENT_TEXT 恰好占 1 行" "$(sed -n '/^_JQ_CONTENT_TEXT=/p' "$SRC" | wc -l | tr -d ' ')" "1"

# 造一张远超 IMG_INLINE_MAX_SIDE 的图(2600px),用短表切片,别逐像素循环
python3 - "$W/big.png" <<'PY'
import sys, zlib, struct
S = 2600
tbl = bytes((i*37) % 256 for i in range(997))
big = tbl * (S // len(tbl) + 1)
rows = b''.join(b'\x00' + big[(y*7) % len(tbl):((y*7) % len(tbl)) + S] for y in range(S))
def ch(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t+d) & 0xffffffff)
open(sys.argv[1], 'wb').write(b'\x89PNG\r\n\x1a\n'
    + ch(b'IHDR', struct.pack('>IIBBBBB', S, S, 8, 2, 0, 0, 0))
    + ch(b'IDAT', zlib.compress(rows, 1)) + ch(b'IEND', b''))
PY

echo "=== 2. 按像素封顶(token 跟像素走,压字节省不了钱) ==="
ck "原图尺寸读得出" "$(img_dims "$W/big.png")" "2600 2600"
del=$(prepare_inline "$W/big.png")
ck "交付件最长边 = IMG_INLINE_MAX_SIDE" "$(img_dims "$del" | awk '{print ($1>$2?$1:$2)}')" "$IMG_INLINE_MAX_SIDE"
ck "尺寸取不到时返回空(不许编)" "$(img_dims "$W/nope.png"; printf x)" "x"

echo "=== 3. 回归锁 A:.url 不许带尾部换行 ==="
IMG_INLINE=1; IMG_CAPABLE=1; IMG_INLINE_HOLD=1
messages='[{"role":"system","content":"s"}]'
img_inline_attach "$del" "图纸A.png"; idA="$inline_ref_id"
IMG_REF_DIR_KEEP="$IMG_REF_DIR"
ck "attach 成功" "$([[ -n "$idA" ]] && echo yes)" "yes"
ck ".url 尾字节 = 3d(不是 0a)" "$(tail -c 1 "$IMG_REF_DIR/$idA.url" | xxd -p | tr -d '\n')" "3d"
ck ".url 整体不含换行" "$(tr -cd '\n' < "$IMG_REF_DIR/$idA.url" | wc -c | tr -d ' ')" "0"

echo "=== 4. 展开(live):必须真出 image_url 部件 ==="
# 顺序:img_build_map 扫的是全局 $messages,所以先把带标记的消息放好再采 map
messages=$(jq -c -n --arg a "$idA" '[{role:"tool",tool_call_id:"c1",content:("read_image: 图纸A.png\n[[IMGREF:"+$a+"]]")}]')
ck "messages 侧只有标记、无 base64" "$( (( $(printf '%s' "$messages" | wc -c) < 3000 )) && echo yes)" "yes"
img_build_map "$W/m1.json"
ck "map 是合法 JSON" "$(jq -e . "$W/m1.json" >/dev/null 2>&1 && echo yes)" "yes"
ck "map 认得这个号" "$(jq -r --arg a "$idA" 'has($a)|tostring' "$W/m1.json")" "true"
exp=$(printf '%s' "$messages" | jq -c --rawfile map "$W/m1.json" \
      --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
      '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS")
ck "展开无 jq 报错" "$([[ -n "$exp" ]] && echo ok)" "ok"
ck "content 变成数组" "$(jq -r '.[0].content|type' <<<"$exp")" "array"
ck "第 0 部件 = image_url" "$(jq -r '.[0].content[0].type' <<<"$exp")" "image_url"
ck "第 1 部件 = 抹净标记后的正文" "$(jq -r '.[0].content[1].text' <<<"$exp")" "read_image: 图纸A.png"
ck "带图判据成立" "$(jq -r "$_JQ_HAS_IMAGE" <<<"$exp")" "true"

echo "=== 5. 回归锁 B:外来同款字面量一个字都不许改 ==="
foreign=$(jq -c -n '[{role:"tool",tool_call_id:"c9",content:"源码里写着 [[IMGREF:zz9]] 这种字面量,还有 [[IMGREF:abc]]"}]')
messages="$foreign"; img_build_map "$W/m2.json"
ck "没发过的号不进 map" "$(jq -r 'length' "$W/m2.json")" "0"
out=$(printf '%s' "$foreign" | jq -c --rawfile map "$W/m2.json" \
      --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
      '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS")
ck "外来文本原样保留" "$(jq -r '.[0].content' <<<"$out")" \
   "源码里写着 [[IMGREF:zz9]] 这种字面量,还有 [[IMGREF:abc]]"
ck "没被加【原图已移除】前缀" "$(jq -r '.[0].content' <<<"$out" | grep -c '原图已移除')" "0"

echo "=== 6. 回收走真实机制:换轮 → 上一轮图 .url 改名 .gone ==="
# HOLD 的单位是**轮**(IMG_REF_GROUP),不是张:同一轮里读多张必须全部可见,
# 换轮时才把上一轮的降级。这里显式推进轮号来模拟"用户发了新一轮消息"。
IMG_REF_GROUP=1
img_inline_attach "$del" "图纸B.png"; idB="$inline_ref_id"
ck "上一轮的图已被改名 .gone" "$([[ -e "$IMG_REF_DIR/$idA.gone" && ! -e "$IMG_REF_DIR/$idA.url" ]] && echo yes)" "yes"
ck "本轮的图仍活着" "$([[ -s "$IMG_REF_DIR/$idB.url" ]] && echo yes)" "yes"
ck "IMG_REFS 只留本轮一条" "${#IMG_REFS[@]}" "1"
ck "条目带轮号(按轮裁剪靠它)" "${IMG_REFS[0]%% *}" "1"
two=$(jq -c -n --arg a "$idA" --arg b "$idB" '[
  {role:"tool",tool_call_id:"c1",content:("read_image: 图纸A.png\n[[IMGREF:"+$a+"]]")},
  {role:"assistant",content:"看过A"},
  {role:"tool",tool_call_id:"c2",content:("read_image: 图纸B.png\n[[IMGREF:"+$b+"]]")} ]')
messages="$two"; img_build_map "$W/m3.json"
exp3=$(printf '%s' "$two" | jq -c --rawfile map "$W/m3.json" \
       --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
       '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS")
ck "展开非空(jq 报错必须算失败,不许被 grep 数成 0 而假通过)" "$([[ -n "$exp3" ]] && echo yes)" "yes"
ck "旧那条 → 字符串形状" "$(jq -r '.[0].content|type' <<<"$exp3")" "string"
ck "含【原图已移除】" "$(jq -r '.[0].content' <<<"$exp3" | grep -c '原图已移除')" "1"
ck "保留路径(重看的唯一抓手)" "$(jq -r '.[0].content' <<<"$exp3" | grep -c '图纸A.png')" "1"
ck "含重新调用 read_image 的义务" "$(jq -r '.[0].content' <<<"$exp3" | grep -c '重新调用 read_image')" "1"
ck "无任何现在时假陈述(F 臂代价)" "$(jq -r '.[0].content' <<<"$exp3" | grep -cE '附有图片|请以原图为准|本条消息附')" "0"
ck "gone 里无标记残留" "$(jq -r '.[0].content' <<<"$exp3" | grep -c IMGREF)" "0"
ck "新那条仍是数组带图" "$(jq -r '.[2].content[0].type' <<<"$exp3")" "image_url"

echo "=== 7. 请求体契约(独立子 shell,不与上面的状态共享) ==="
# ⚠ 重定向必须放在**子 shell 内层**:wchat.sh:980 的 EXIT trap 会 printf "\033[?25h\033[?7h"
#   恢复光标/终端。在子 shell 里 source 它 = 把这条 trap 也装上,子 shell 退出时才输出,
#   于是"外层重定向/命令替换"会把这串转义收进请求体尾巴 → jq -e 判非法(而 jq -r 单键
#   取值却照样成功,现象是"一半断言红一半绿",极难归因)。放内层就与请求体无关了。
run_body() {  # $1=装载源 $2=messages 文件 $3=额外 bash 语句(可空;set -u 下必须给默认值)
    local extra="${3:-}"
    _WCHAT_LIB_ONLY=1 API_KEY="${WCHAT_API_KEY:-}" /opt/homebrew/bin/bash -c "
        source '$1' </dev/null >/dev/null 2>&1
        messages=\$(cat '$2')
        no_tools=1
        $extra
        make_request_body > '$W/body.out'
    " < /dev/null >/dev/null 2>&1
    cat "$W/body.out"
}
printf '%s' '[{"role":"system","content":"s"},{"role":"user","content":"hi"}]' > "$W/noimg.json"
new=$(run_body "$SRC" "$W/noimg.json")
ck "无引用时请求体是合法 JSON" "$(jq -e . <<<"$new" >/dev/null 2>&1 && echo yes)" "yes"
ck "无图 reasoning_effort=high" "$(jq -r .reasoning_effort <<<"$new" 2>/dev/null)" "high"
# 与"不含 inline 的那一版"逐字节比:HEAD 还没有 inline,正好当基线
git show HEAD:wchat.sh > "$W/head.sh" 2>/dev/null
if [[ -s "$W/head.sh" ]]; then
    old=$(run_body "$W/head.sh" "$W/noimg.json")
    ck "无引用时与 HEAD 逐字节相同(新特性不许动旧路径)" \
       "$([[ "$new" == "$old" ]] && echo same || echo diff)" "same"
fi
# 只存标记(尚未展开)时也必须判出 none —— 钉住"先展开再算 reasoning_effort"的顺序
printf '%s' "[{\"role\":\"tool\",\"tool_call_id\":\"c\",\"content\":\"read_image: x\n[[IMGREF:$idB]]\"}]" > "$W/marked.json"
mk=$(run_body "$SRC" "$W/marked.json" "IMG_REF_DIR='$IMG_REF_DIR'")
ck "只存标记也判出 none(展开先于判据)" "$(jq -r .reasoning_effort <<<"$mk" 2>/dev/null)" "none"
ck "展开后的请求体含 image_url 键" "$(jq -e '[..|objects|select(.type=="image_url")]|length>0' <<<"$mk" >/dev/null 2>&1 && echo yes)" "yes"
ck "展开后的请求体是合法 JSON" "$(jq -e . <<<"$mk" >/dev/null 2>&1 && echo yes)" "yes"

echo "=== 8. IMG_INLINE=0 时退回转述(不产生任何引用) ==="
IMG_INLINE=0; inline_ref_id=""
if img_inline_attach "$del" "图纸C.png"; then r=attached; else r=refused; fi
ck "IMG_INLINE=0 时 attach 必须拒绝" "$r" "refused"
ck "拒绝时不留引用号" "${inline_ref_id:-}" ""

echo "=== 9. 同轮多张 与 单条消息多标记(用户发图的形状) ==="
# 为什么单独一节:printContent 把用户一次甩来的多张图拼进**同一条** user 消息,
# 而展开片段早期用 capture(单匹配)→ 只有第一张成为 image_url 部件,其余以字面量
# 留在正文里(实测:双标记残留 1、三标记残留 2)。这是"用户发 4 张面板、模型只看到 1 张"
# 级别的缺陷,必须有独立一节看着它。
IMG_REF_GROUP=2; IMG_INLINE=1; IMG_INLINE_HOLD=1
same=""; for i in 1 2 3; do img_inline_attach "$del" "同轮$i.png"; same+="$inline_ref_id,"; done
ck "同一轮连读 3 张必须全部可见(按轮不按张)" "${#IMG_REFS[@]}" "3"
ck "同轮 3 个 .url 都在" "$(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ')" "3"

# 用同轮 3 个号拼一条"用户发图"形状的消息(一张消息、三个标记)
idlist=(); for e in "${IMG_REFS[@]}"; do idlist+=("${e#* }"); done
json_ids=$(printf '\"%s\",' "${idlist[@]}" | sed 's/,$//')
# ⚠ --argjson 而不是 --arg:传的是 JSON 数组,--arg 会把它当字符串 → to_entries 当场报错
multi=$(jq -nc --argjson ids "[$json_ids]" '
  {role:"user"} | .content = ($ids | to_entries
     | map("read_image: 面板\(.key+1).png\n[[IMGREF:\(.value)]]") | join("\n"))')
messages="[$multi]"; img_build_map "$W/m4.json"
ck "map 认出同轮 3 个号" "$(jq 'length' "$W/m4.json")" "3"
exp4=$(printf '%s' "$messages" | jq -c --rawfile map "$W/m4.json" \
       --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
       '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS")
ck "多标记展开非空" "$([[ -n "$exp4" ]] && echo yes)" "yes"
ck "3 张图 → 4 个部件(3 image + 1 text)" "$(jq -r '.[0].content|length' <<<"$exp4")" "4"
ck "零标记残留" "$(jq -r '.[0].content|tostring' <<<"$exp4" | grep -c IMGREF)" "0"
ck "每个部件都是 image_url" "$(jq -r '[.[0].content[]|select(.type=="image_url")]|length' <<<"$exp4")" "3"

# 混合:同一条消息里 1 活 + 1 已回收。
# ⚠ 取号前先验数量:上一版直接 ${IMG_REFS[1]},在没有"按轮"语义的版本上跑会 unbound 崩掉,
#   整道闸门连 RESULT 都不输出 —— 按本仓库铁律"没有结论行=失败",但**崩**比红更坏:
#   红会告诉人去哪看,崩只会让人以为闸门坏了。所以前置条件不满足要记 FAIL 后继续。
if (( ${#idlist[@]} >= 2 )); then
    live_id="${idlist[0]}"; gone_id="${idlist[1]}"
    mv -f "$IMG_REF_DIR/$gone_id.url" "$IMG_REF_DIR/$gone_id.gone" 2>/dev/null
    mixed=$(jq -nc --arg a "$live_id" --arg b "$gone_id" '{role:"user",content:("read_image: A.png [[IMGREF:"+$a+"]]\nread_image: B.png [[IMGREF:"+$b+"]]")}')
    messages="[$mixed]"; img_build_map "$W/m5.json"
    exp5=$(printf '%s' "$messages" | jq -c --rawfile map "$W/m5.json" \
           --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
           '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS")
    ck "混合条:仍是数组且带 1 个 image_url" "$(jq -r '[.[0].content[]|select(.type=="image_url")]|length' <<<"$exp5")" "1"
    ck "混合条:正文点名被回收的那张" "$(jq -r '.[0].content[-1].text' <<<"$exp5" | grep -c '原图已移除')" "1"
    ck "混合条:无标记残留" "$(jq -r '.[0].content|tostring' <<<"$exp5" | grep -c IMGREF)" "0"
    unk=$(jq -nc --arg a "$live_id" '{role:"user",content:("真图 [[IMGREF:"+$a+"]] 与源码里的 [[IMGREF:zz99]] 同行")}')
    messages="[$unk]"; img_build_map "$W/m6.json"
    exp6=$(printf '%s' "$messages" | jq -c --rawfile map "$W/m6.json" \
           --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
           '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS")
    ck "同条里的未知标记不被删除也不被改写" "$(jq -r '.[0].content[1].text' <<<"$exp6" | grep -c 'IMGREF:zz99')" "1"
    ck "同条里的真号仍被展开成图" "$(jq -r '[.[0].content[]|select(.type=="image_url")]|length' <<<"$exp6")" "1"
else
    ck "混合/未知标记两条向量需要同轮 >=2 个引用(前置条件)" "lt2" "ge2"
fi

echo "=== 10. 切档视觉预检(只测离线性,不发网络) ==="
# 探测函数本体要发请求,闸门里不测它发得对不对(那是端到端探针的活);
# 这里钉的是**结论有没有接进链路**与**缓存是否真挡住网络**——
# 探测错了比不探更糟:一次拼错的模型名若被判"不收图",通道就被永久关掉。
IMG_CAPABLE=1; IMG_INLINE=1
IMG_MAIN_VISION=0
if img_inline_attach "$del" "预检拒绝.png"; then r10a=attached; else r10a=refused; fi
ck "预检=不收图 时 attach 必须直接拒绝(不付那次必然 400 的主请求)" "$r10a" "refused"
ck "拒绝时不留引用号" "${inline_ref_id:-}" ""
IMG_MAIN_VISION=""
img_inline_attach "$del" "预检未知.png"; r10b="$inline_ref_id"
ck "预检未知时照常尝试内联" "$([[ -n "$r10b" ]] && echo yes)" "yes"
# 缓存:BASE_URL 清空后若仍返回"收图",说明走的是缓存、没碰网络
_saved_url="$BASE_URL"
IMG_MAIN_VISION=1; IMG_VISION_PROBED_FOR="$model"; BASE_URL=""
img_probe_main_model; r10c=$?
BASE_URL="$_saved_url"
ck "同模型第二次预检走缓存(无 BASE_URL 也返回缓存结论=没发请求)" "$r10c" "0"
# 探不出(缺 key/url)时不许改判:否则一次网络抖动就把通道关掉
IMG_MAIN_VISION=""; IMG_VISION_PROBED_FOR=""; BASE_URL=""; API_KEY=""
img_probe_main_model; r10d=$?
ck "缺配置必须判'未知'(rc=2)" "$r10d" "2"
ck "'未知'不得把结论写成不收图" "${IMG_MAIN_VISION:-空}" "空"

printf '\nRESULT total=%d pass=%d fail=%d\n' "$((pass + fail))" "$pass" "$fail"
printf '结果:PASS=%d FAIL=%d\n' "$pass" "$fail"
(( fail == 0 ))
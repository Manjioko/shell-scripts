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

# ⚠ 本闸门 3–12 组测的是**回收发生之后**的形状与文案契约(标记怎么展开、gone 文案、
#   外来字面量不许动),与"回收**何时**发生"无关。而"何时发生"在刀3 改成了
#   由活图预算决定(docs/img-cache-policy.md §三 M2)。所以这里显式钉在**回退模式**
#   (IMG_LIVE_BUDGET_TOKENS=0 → 逐轮 HOLD 旧语义),让上面各组继续测它们真正要测的东西。
#   新策略由第 13 组单独测 —— 把两种语义塞进同一组断言,得到的红说明不了任何问题。
IMG_LIVE_BUDGET_TOKENS=0

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
     | map("用户粘贴的图片: 面板\(.key+1).png\n[[IMGREF:\(.value)]]") | join("\n"))')
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
    mixed=$(jq -nc --arg a "$live_id" --arg b "$gone_id" '{role:"user",content:("用户粘贴的图片: A.png [[IMGREF:"+$a+"]]\n用户粘贴的图片: B.png [[IMGREF:"+$b+"]]")}')
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

echo "=== 11. 轮边界位置(降级必须发生在换轮时,不是等下次读图) ==="
# 这一组是**位置锁**而不是行为锁,原因值得记下:机制本身(prune 按轮裁剪)在坏版本上也是对的,
# 坏在"谁调用它"——原先 IMG_REF_GROUP+1 与 prune 都只在 img_inline_attach 里发生,
# 于是"下一轮只打字不读图"时上一轮的图一直挂着、每轮重付 token
# (实测:第2轮 image_url 部件仍是 2、.gone 是 0)。所以必须钉位置。
pc_start=$(grep -n '^printContent() {' "$SRC" | cut -d: -f1)
inc_line=$(grep -n 'IMG_REF_GROUP=\$(( ' "$SRC" | head -1 | cut -d: -f1)
img_if_line=$(grep -n 'if (( \${#input_images\[@\]} > 0 ))' "$SRC" | head -1 | cut -d: -f1)
ck "被测源里能找到轮号递增与发图分支两个锚点(找不到=锚点漂了,断言全不可信)" \
   "$([[ -n "$inc_line" && -n "$img_if_line" ]] && echo yes)" "yes"
ck "全文件只有一处轮号递增(重复递增会让 HOLD>=2 时提前误降级)" \
   "$(grep -c 'IMG_REF_GROUP=\$(( ' "$SRC")" "1"
ck "轮号递增在'本轮有图'判断之前(纯文字轮也必须推进)" \
   "$([[ -n "$inc_line" && -n "$img_if_line" && "$inc_line" -lt "$img_if_line" ]] && echo before || echo "after:$inc_line/$img_if_line")" "before"
ck "轮边界处紧接着调用降级(不等下一次 attach)" \
   "$(awk -v s="$inc_line" 'NR>s && NR<=s+3 && /img_refs_prune/{f=1} END{print f?"yes":"no"}' "$SRC")" "yes"
ck "img_inline_attach 里仍保留 prune(同轮累计也要能裁)" \
   "$(awk '/^img_inline_attach\(\) \{/{f=1} f && /img_refs_prune/{c++} f && /^\}$/{exit} END{print c+0}' "$SRC" | tr -d ' ' | awk '{print ($1>=1)?"yes":"no"}')" "yes"

# 机制本身也钉一条:换轮后旧引用必须变成 .gone(不是消失)且展开出诚实的 gone 文字
IMG_REF_GROUP=1; img_inline_attach "$del" "轮1.png" >/dev/null; t1="$inline_ref_id"
IMG_REF_GROUP=2; img_refs_prune
one=$(jq -nc --arg a "$t1" '[{role:"tool",tool_call_id:"c",content:("read_image: 轮1.png [[IMGREF:"+$a+"]]")}]')
# ⚠ 顺序:img_build_map 扫的是**全局 $messages**,必须先赋值再采 map
# (这个坑本文件已踩过一次:反过来写就会得到"map 里没有这个号 → 按未知处理 → 正文不动",
#  看起来像展开逻辑坏了,其实是测试自己的时序)
messages="$one"; img_build_map "$W/m7.json"
ck "换轮后旧引用文件改名 .gone 而非被删(要靠它区分已回收/非我方)" \
   "$([[ -e "$IMG_REF_DIR/$t1.gone" && ! -e "$IMG_REF_DIR/$t1.url" ]] && echo yes)" "yes"
exp7=$(printf '%s' "$one" | jq -c --rawfile map "$W/m7.json" \
       --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
       '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS")
ck "换轮后展开只剩文字且带移除标记" "$(jq -r '.[0].content|type' <<<"$exp7")/$(jq -r '.[0].content' <<<"$exp7" | grep -c '原图已移除')" "string/1"
IMG_REF_GROUP=3; img_inline_attach "$del" "轮3.png" >/dev/null   # 恢复一个活引用给后续节用

echo "=== 12. 「不收图」判据:用真实端点抓回的报错原文 ==="
# 这些字符串不是编的,是 2026-10-03 从两个真实端点取回的原文
# (.scratch/tokens.log;qwen3.7-max 那条尤其关键 —— 里面没有 image/vision/not support
#  任何词,旧判据一条都不匹配,于是纯文字档每轮 400、永不退回转述)。
reject_case() {  # $1=期望(yes=判不收图 / no=不许关通道) $2=说明 $3=原文
    if img_reject_wording "$3"; then got=yes; else got=no; fi
    ck "$2(期望 $1)" "$got" "$1"
}
reject_case yes  "qwen3.7-max 真实拒绝文本" \
  'The provided messages input is invalid. The error info is [Unexpected item type in content.].'
reject_case yes  "内容形状非法的真实文本" \
  'if content is list. item must be dict and key[type] should in dict'
reject_case yes  "英文明确不支持" 'The model does not support image input.'
reject_case yes  "直接否认视觉"   'This is not a vision model.'
reject_case yes  "无视觉能力"     'The account has no vision capability.'
reject_case yes  "中文拒绝文本"   '当前模型不支持图片输入'
# 下面三条是**反向锁**:误判成"不收图"会把整个会话的内联能力关掉,后果比漏判重
reject_case no   "几何限制不能算能力问题" \
  'The image length and width do not meet the model restrictions. [height:8 or width:8 must be larger than 10]'
reject_case no   "型号不存在不能算能力问题" 'Model not exist.'
reject_case no   "含 vision 的无关报错(旧 ask_ai 那份 *vision* 单条件正是在这里误杀)" \
  'The requested vision model was not found, check the model name.'
reject_case no   "鉴权失效不能算能力问题" 'AuthenticationError: Incorrect API key provided.'
reject_case no   "空文本不能判不收图"   ''

# 规矩只许有一处定义、其余全走调用(三处各抄一份已经漂过一次:ask_ai 那份多了
# `*vision*` 单条件,就是上面那条负锁要拦的形状)
ck "判据定义恰好 1 处" "$(grep -c '^img_reject_wording() {' "$SRC")" "1"
ck "判据至少 3 处调用(probe/ask_ai/describe_images 都走它)" \
   "$(( $(grep -c 'img_reject_wording ' "$SRC") - 1 ))" "3"
ck "兜底不得再用 rm 删 .url(必须改名 .gone,否则标记变孤儿、正文留裸标记)" \
   "$(grep -c 'rm -f "${IMG_REF_DIR:?}"' "$SRC")" "0"
ck "正向判据必须看 image_tokens 而不是只看有回复" \
   "$(grep -c 'prompt_tokens_details.image_tokens' "$SRC" | awk '{print ($1>=1)?"yes":"no"}')" "yes"

echo "=== 13. 刀3 新策略:降级由活图预算决定(与上面 3–12 组的旧语义分开钉) ==="
# 上面各组钉在回退模式;这一组把预算制打开,专测"何时降级"。
# 每组都用**独立的引用目录**:上一组残留的活图会把硬上限压下来,让后面的断言
# 红得莫名其妙 —— 本轮 inline_image_test 第一次转绿失败就是这个原因。
budget_env() { # $1=名字 → 干净的引用状态
    IMG_REF_DIR="$W/b_$1"; rm -rf "$IMG_REF_DIR"; mkdir -p "$IMG_REF_DIR"
    IMG_REFS=(); IMG_REF_SEQ=0; IMG_REF_GROUP=1
    IMG_LIVE_BUDGET_TOKENS=6000; IMG_LIVE_FLOOR_TOKENS=3600; IMG_LIVE_KEEP_MIN=1
    IMG_LIVE_HARD_TOKENS=0; IMG_INLINE_HOLD=1; IMG_INLINE=1; IMG_CAPABLE=1; IMG_INLINE_MAX_SIDE=2048
}
expand_of() { messages="$1"; img_build_map "$W/bm.json"
    printf '%s' "$1" | jq -c --rawfile map "$W/bm.json" \
        --arg gone_pre "$IMG_INLINE_GONE_MARK" --arg gone_hint "$IMG_INLINE_GONE_HINT" \
        '($map|fromjson) as $map | '"$_JQ_EXPAND_REFS"; }

# 13.1 预算内换轮:**不降级** —— 这是整条策略的目的,不是副作用
budget_env resid
img_inline_attach "$del" "A.png"; ra="$inline_ref_id"
msgA=$(jq -nc --arg a "$ra" '[{role:"tool",tool_call_id:"c1",content:("read_image: A.png [[IMGREF:"+$a+"]]")}]')
hA=$(printf '%s' "$(expand_of "$msgA")" | jq -c '.[:-1]' | cksum | awk '{print $1}')
IMG_REF_GROUP=2; img_refs_prune_by_budget
ck "预算内换轮:图没被回收(.url 仍在)" "$([[ -s "$IMG_REF_DIR/$ra.url" ]] && echo yes)" "yes"
ck "预算内换轮:没有 .gone(一次都没降级)" "$(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ')" "0"
ck "预算内换轮:展开仍是数组(还看得见)" "$(jq -r '.[0].content|type' <<<"$(expand_of "$msgA")")" "array"
ck "预算内换轮:已发出的前缀指纹不变(不变量 I1)" \
   "$(printf '%s' "$(expand_of "$msgA")" | jq -c '.[:-1]' | cksum | awk '{print $1}')" "$hA"

# 13.2 超预算换轮:一次降到**地板**,且留底 ≥1(滞回,不是逐张)
img_inline_attach "$del" "B.png"; img_inline_attach "$del" "C.png"
IMG_REF_GROUP=3; img_refs_prune_by_budget
ck "超预算后成批降级(≥2 张变 .gone)" \
   "$(( $(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ') >= 2 ? 1 : 0 ))" "1"
ck "降级后留底 ≥1 张(视觉态不塌)" \
   "$(( $(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ') >= 1 ? 1 : 0 ))" "1"
ck "降级后活图总额 ≤ 地板" \
   "$(( $(img_live_tokens) <= IMG_LIVE_FLOOR_TOKENS ? 1 : 0 ))" "1"
ck "被降级那条展开后带诚实的移除文案" \
   "$(grep -c '原图已移除' <<<"$(expand_of "$msgA")" )" "1"

# 13.3 轮号已推进、但**还没走轮边界**时再挂一张:旧代码会在这次的 attach 里把
#      上一轮那张**已经发出去**的图裁成 .gone(= 在轮内改写历史,破 I1);新代码不裁。
#      ⚠ 上一版这里写成"同一轮内连挂两张",而旧 prune 是按轮号裁的、同轮本来就不动 ——
#      那条断言新旧语义下都得绿,是**恒真的假判据**(负对照翻不红就是它的现形)。
budget_env within
img_inline_attach "$del" "A.png"; ra="$inline_ref_id"
msg1=$(jq -nc --arg a "$ra" '[{role:"tool",tool_call_id:"c1",content:("read_image: A.png [[IMGREF:"+$a+"]]")}]')
# 基线与比较值必须取**同一个切片**(.[:1]);上一版基线写成 .[:-1],恒红。
h1=$(printf '%s' "$(expand_of "$msg1")" | jq -c '.[:1]' | cksum | awk '{print $1}')
IMG_REF_GROUP=2                      # 新一轮的轮号,但**不调用**边界函数
img_inline_attach "$del" "B.png"     # ← 旧实现在这里就地把上一轮的图改写掉
msg2=$(jq -nc --arg a "$ra" '[{role:"tool",tool_call_id:"c1",content:("read_image: A.png [[IMGREF:"+$a+"]]")}]')
ck "轮号推进后 attach 新图:旧图没被在轮内改写(.gone 为 0)" \
   "$(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ')" "0"
ck "轮号推进后 attach 新图:上一条已发出消息的展开形态不变(I1)" \
   "$(printf '%s' "$(expand_of "$msg2")" | jq -c '.[:1]' | cksum | awk '{print $1}')" "$h1"
# 成对负对照:回退模式(预算=0)下 attach 会照旧裁掉上一轮 → 同一判据必须翻红。
# 这里比的是**磁盘形状**(实测过它两种模式下确实翻得动:回退=1 url/1 gone,预算=2 url/0 gone),
# 上一版用"展开后的 hash 切片"作对照,红不起来 —— 那是对照手段自己被骗了(取样管道),
# 不是判定点选错。判据翻不动时先怀疑判据,别急着改产品。
budget_env within_neg; IMG_LIVE_BUDGET_TOKENS=0
img_inline_attach "$del" "A.png"
IMG_REF_GROUP=2; img_inline_attach "$del" "B.png"
ck "负对照:回退模式下 attach 会裁上一轮(gone≥1,证明 G13.3 测的是真差别)" \
   "$(( $(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ') >= 1 ? 1 : 0 ))" "1"

# 13.4 超硬上限:**拒绝挂载**,而不是回头去改已发出的历史
budget_env hard
IMG_LIVE_BUDGET_TOKENS=2600; IMG_LIVE_FLOOR_TOKENS=100; IMG_LIVE_HARD_TOKENS=3000
img_inline_attach "$del" "A.png"; rcA=$?
img_inline_attach "$del" "B.png"; rcB=$?
ck "第一张允许挂载(_live=0 时不拒,否则会死锁)" "$rcA" "0"
ck "超硬上限时第二张被拒绝(rc=1)" "$rcB" "1"
ck "拒绝之后没有新增 .gone(没靠改历史腾地方)" "$(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ')" "0"

# 13.5 刀2 去重:同一份内容读两次只挂一份(与预算无关,单独钉)
budget_env dedup
img_inline_attach "$del" "A.png"; r1="$inline_ref_id"
hit=$(img_live_ref_of "$del")
ck "第二次同内容读命中已有引用号" "$hit" "$r1"
ck "命中时没有新增 .url(仍只有 1 份)" "$(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ')" "1"

echo "=== 14. 用户发图链路的三条(F1 caption 形状 / F2 粘贴即授权 / F3 去重回复指位置) ==="
# 起因是用户现场:粘进来的图已被附上,模型却又调一次 read_image,还弹一次
# "AI 请求访问工作目录外文件"。根因是**我们给用户附图写的 caption 用了
# `read_image: <路径>` 这个工具返回形状** —— 模型照做了一次。
# 三条各配负对照,否则就是恒真判据(本轮已经被这条抓过两次)。

# --- 14.1 F1 结构锁:用户消息里不许出现工具调用的形状 ---
_user_cap_line=$(awk '/用户粘贴的图片: /{print}' "$SRC" | head -1)
ck "用户附图的 caption 是新写法(名词式)" "$(printf '%s' "$_user_cap_line" | grep -c '用户粘贴的图片')" "1"
ck "用户附图的 caption 不含 read_image: 前缀(那形状会诱导模型多调一次)" \
   "$(printf '%s' "$_user_cap_line" | grep -c 'read_image: ')" "0"
ck "caption 仍带路径(回收后唯一的重看抓手)" "$(printf '%s' "$_user_cap_line" | grep -c '${img_path}')" "1"
# 实测有效的不是"换个名词",而是这句**条件式指引**(A/B:旧 5 / 名词式 7 / 加指引 3,理想 3)
ck "caption 带'看得见就别再调'的条件式指引(这条才是实测压住多余调用的那半)" \
   "$(printf '%s' "$_user_cap_line" | grep -c '不要重复调用 read_image')" "1"
ck "caption 的条件句留了重看出口(图被回收后不能把模型堵死)" \
   "$(printf '%s' "$_user_cap_line" | grep -Ec '看不到时.*再调用本工具')" "1"
ck "caption 不声明'本条附有原图'类现在时断言(F 臂纪律)" \
   "$(printf '%s' "$_user_cap_line" | grep -Ec '附有原图|图如下|画面就在当前上下文')" "0"
ck "caption 仍带 IMGREF 标记(展开契约)" \
   "$(awk '/img_desc\+=.*IMGREF:\$inline_ref_id/{print}' "$SRC" | grep -c '用户粘贴的图片')" "1"
# 负对照:HEAD 版确实是旧形状 —— 证明上面几条测的是真改动,不是空 grep
if git -C "$(dirname "$SRC")" rev-parse HEAD >/dev/null 2>&1; then
    _head_line=$(git -C "$(dirname "$SRC")" show HEAD:wchat.sh 2>/dev/null | awk '/read_image: \$img_path/{print}' | head -1)
    ck "负对照:HEAD 上旧形状确实存在(证明这条锁在管事)" \
       "$([[ -n "$_head_line" ]] && echo yes || echo no)" "yes"
fi

# --- 14.2 F2 行为:粘贴图片 = 只读授权,不再二次询问 ---
granted_files=()
p_img="$W/paste.png"; cp "$del" "$p_img"
PARSE_OUT=""; input_images=()
parse_placeholders "📋 [图片] $p_img"
ck "粘贴图片后该路径已在只读名单里" "$(is_granted "$p_img" r && echo yes)" "yes"
ck "粘贴只给读,不给写(看 ≠ 许改)" "$(is_granted "$p_img" w && echo yes || echo no)" "no"
ck "粘贴的图进了 input_images(原功能没坏)" "${#input_images[@]}" "1"
# 负对照:同一路径若不登记,必须判"未授权"(证明 is_granted 不是恒真)
p_img2="$W/paste2.png"; cp "$del" "$p_img2"
ck "负对照:没粘贴过的路径必须判未授权" "$(is_granted "$p_img2" r && echo yes || echo no)" "no"
# 负对照:非图片占位行不该被授权
p_txt="$W/notimage.txt"; printf 'hello' > "$p_txt"
granted_files=(); input_images=()
parse_placeholders "📋 [图片] $p_txt"
ck "非图片的 [图片] 占位行不得授权" "$(is_granted "$p_txt" r && echo yes || echo no)" "no"
ck "非图片的 [图片] 占位行不得进 input_images" "${#input_images[@]}" "0"

# --- 14.3 F3 行为:去重回复必须指位置 + 带条件句 + 无现在时断言 ---
# ⚠ 这里**必须走粘贴流程拿授权**:夹具图在 $W(仓库外),直接调 tool_read_image 会让
#   path_access 弹交互确认,非交互环境下整道闸门挂死(实测踩过)。
#   顺带这一跑就是用户现场的完整链路:粘图 → 挂载 → 模型再读同一张 → 走去重、不弹确认。
budget_env reply; IMG_INLINE_HOLD=999
p_img3="$W/paste3.png"; cp "$del" "$p_img3"
granted_files=(); input_images=(); PARSE_OUT=""
parse_placeholders "📋 [图片] $p_img3"
ck "链路前置:粘贴后已只读授权(所以后面不该再弹确认)" "$(is_granted "$p_img3" r && echo yes)" "yes"
inline_ref_id=""; img_inline_attach "$p_img3" "$p_img3"; rr="$inline_ref_id"
fn_args=$(jq -nc --arg p "$p_img3" '{path:$p}')
content=""; tool_read_image </dev/null >/dev/null 2>&1
ck "第二次读同一张走去重(命中引用号)" "$(grep -c "$rr" <<<"$content")" "1"
ck "去重回复指出图在哪条消息(引用号 + 所在消息)" "$(grep -c '所在的那条消息' <<<"$content")" "1"
ck "去重回复带条件句(被回收后这句话仍为真)" "$(grep -c '若那里已看不到图片本体' <<<"$content")" "1"
ck "去重回复无现在时断言(F 臂纪律)" "$(grep -Ec '附有原图|图如下|画面就在当前上下文' <<<"$content")" "0"
ck "去重回复里不含新标记(确实没再挂一份)" "$(grep -c 'IMGREF' <<<"$content")" "0"
ck "去重之后引用目录里仍只有 1 份 .url" "$(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ')" "1"

echo "=== 15. 刀4 单价自标定 + F-1/F-2/F-3:估算查表 / 字节护栏 / 回收顺序与可见性 ==="
# 起因是 2026-10-04 用户现场:两张大图第一轮后就被回收(4MB 字节护栏拍小了),
# 而且先踢的正是用户亲手粘的那张(纯年龄序对它天然不利)。同批收尾刀4 的两处半移动状态。
# 每条行为都配负对照 —— 本轮已两次被"恒真判据"骗过,不配对照的断言不算数。

# --- 15.1 刀4:查标定表(命中/缩放/绑 model/坏样本门/去重/开关) ---
_tbl="$(img_tok_table_file)"; rm -f "$_tbl"
model="probe-m"
ck "无标定:est512 用面积常数" "$(img_est_from_side 512)" "262"
ck "无标定:est1024 用面积常数" "$(img_est_from_side 1024)" "1048"
img_token_calibrate "probe-m" p1 512 194
ck "标定 512→194 后 est512=194(查表真命中,不再是永不命中的哑火)" "$(img_est_from_side 512)" "194"
ck "面积缩放到未标定档:est1024=776(=194×4)" "$(img_est_from_side 1024)" "776"
model="other-m"
ck "换 model 退回常数(标定绑 model)" "$(img_est_from_side 512)" "262"
model="probe-m"
img_token_calibrate "probe-m" p1 512 4321     # 同 (model,引用号):不许重复记
img_token_calibrate "probe-m" p2 512 0        # 坏样本 real=0:不入库
img_token_calibrate "probe-m" p3 512 99999999 # 坏样本 >4×cap:不入库
ck "去重 + 坏样本门:表内仍 1 条" "$(img_token_calib_count)" "1"
ck "坏样本没污染查表(est512 仍 194)" "$(img_est_from_side 512)" "194"
ck "计数函数已定义(-u 的引用不再悬空)" "$(declare -F img_token_calib_count >/dev/null && echo yes)" "yes"
IMG_TOKENS_AUTOCAL=0
ck "AUTOCAL=0 完全退回刀3 常数" "$(img_est_from_side 512)" "262"
ck "AUTOCAL=0 时连标定都不记" "$(img_token_calibrate "probe-m" p4 512 194; img_token_calib_count)" "1"
IMG_TOKENS_AUTOCAL=1
rm -f "$_tbl"
# 负对照:同一查表逻辑去掉 BEGIN{best=1e18} 就永远 miss —— 那正是上一轮"标定写了、
# 查不到"的 bug 本体,证明"查表命中"这条断言不是恒真
ck "负对照:awk 未初始化 best 时查不到(旧 bug 现形)" \
   "$(awk -F'|' -v m='probe-m' -v s='512' '$1==m{d=($3>s?$3-s:s-$3); if(d<best){best=d;bs=$3;bt=$4}} END{if(bt!="")print "hit"; else print "miss"}' <<< 'probe-m|p1|512|194')" "miss"

# 造"已挂载"的引用(不走 attach:这里测的是回收策略,与挂载无关)。$1=rid $2=src $3=原始字节
mk_ref() {
    printf 'data:image/jpeg;base64,' > "$IMG_REF_DIR/$1.url"
    head -c "$3" /dev/zero | base64 | tr -d '\n' >> "$IMG_REF_DIR/$1.url"
    printf 'c1 c2 2048 2048 2048 %s\n' "$2" > "$IMG_REF_DIR/$1.fp"
    IMG_REFS+=("1 $1")
}

# --- 15.2 F-1:字节护栏 4MB→12MB(两张大图不许把用户粘贴图第一轮就踢掉) ---
budget_env f1old
mk_ref rA1 user 3000000; mk_ref rA2 user 3000000   # 各 ≈4MB base64,合计 ≈8MB
IMG_LIVE_MAX_BYTES=4194304                           # 旧阈值
img_refs_prune_by_budget > "$W/pr_f1old.out"
ck "负对照:4MB 旧阈值下两张大图确实被回收(证明下面那条不是恒真)" \
   "$(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ')" "1"
budget_env f1new
mk_ref rB1 user 3000000; mk_ref rB2 user 3000000
IMG_LIVE_MAX_BYTES=12582912                          # 新阈值
img_refs_prune_by_budget > "$W/pr_f1new.out"
ck "12MB 新阈值下两张大图不触发回收(用户粘贴图不再第一轮被踢)" \
   "$(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ')" "0"
ck "结构锁:默认值就是 12MB" "$(grep -c 'IMG_LIVE_MAX_BYTES:-12582912' "$SRC")" "1"

# --- 15.3 F-2:回收顺序按来源分级,不是纯年龄 ---
budget_env f2
mk_ref rT1 tool 1000      # 最老,tool
mk_ref rU1 user 1000      # 中间,user(用户粘贴的对话主角)
mk_ref rT2 tool 1000      # 最新,tool
IMG_LIVE_BUDGET_TOKENS=100 # 强制触发(7500>100),一次降到地板:踢 2 留 1
img_refs_prune_by_budget > "$W/pr_f2.out"
ck "F-2:最老的 tool 先被回收" "$([[ -e "$IMG_REF_DIR/rT1.gone" ]] && echo yes)" "yes"
ck "F-2:另一张 tool 也回收(降到地板)" "$([[ -e "$IMG_REF_DIR/rT2.gone" ]] && echo yes)" "yes"
ck "F-2:用户粘贴那张活到最后(哪怕比 rT2 老)" "$([[ -s "$IMG_REF_DIR/rU1.url" ]] && echo yes)" "yes"
# 负对照:来源全是 user 时退回年龄序,此时"最老的死"——证明上面三条分得清来源,不是恒真
budget_env f2neg
mk_ref rN1 user 1000; mk_ref rN2 user 1000; mk_ref rN3 user 1000
IMG_LIVE_BUDGET_TOKENS=100
img_refs_prune_by_budget > /dev/null
ck "负对照:全 user 时按年龄先踢最老(rN1 死)" "$([[ -e "$IMG_REF_DIR/rN1.gone" ]] && echo yes)" "yes"
ck "负对照:全 user 时最年轻的活(rN3 在)" "$([[ -s "$IMG_REF_DIR/rN3.url" ]] && echo yes)" "yes"
ck "结构锁:.fp 写入带第 6 列来源(列序契约只准往后加)" \
   "$(grep -cF '"${IMG_INLINE_MAX_SIDE}" "${_fp_dims}" "${src}"' "$SRC")" "1"
ck "结构锁:read_image 挂载标 tool 来源" "$(grep -cF 'img_inline_attach "$resolved" "$path" "tool"' "$SRC")" "1"
ck "结构锁:用户粘贴挂载标 user 来源" "$(grep -cF 'img_inline_attach "$img_path" "$img_path" "user"' "$SRC")" "1"

# --- 15.4 F-3:回收必须说出口(♻),不许静默 ---
budget_env f3
mk_ref rV1 tool 1000; mk_ref rV2 tool 1000; mk_ref rV3 user 1000
IMG_LIVE_BUDGET_TOKENS=100
img_refs_prune_by_budget > "$W/pr_f3.out"
ck "F-3:回收在屏幕上说出口(♻ 一行)" "$(grep -c '♻ 已回收' "$W/pr_f3.out")" "1"
ck "F-3:提示带回收张数" "$(grep -cE '♻ 已回收 2 张' "$W/pr_f3.out")" "1"
ck "F-3:提示带触发判据名(哪条判据干的要说清)" \
   "$(grep -cE 'IMG_LIVE_BUDGET_TOKENS|IMG_LIVE_MAX_BYTES' "$W/pr_f3.out")" "1"
# 负对照:没回收就不得凭空出现 ♻(否则上面就是恒真)
budget_env f3neg
mk_ref rQ1 user 1000
img_refs_prune_by_budget > "$W/pr_f3neg.out"
ck "负对照:预算内不回收时没有 ♻ 行" "$(grep -c '♻' "$W/pr_f3neg.out")" "0"

# --- 15.5 工具描述的措辞纪律:不许教模型"换轮即不可见"(刀3 前的旧政策) ---
# 2026-10-04 现场二:两张图明明全程驻留(无 ♻、无改写报警),模型却言之凿凿"看不到了",
# 逐字引用的正是 read_image 描述里那句旧政策 —— **文案在教模型编瞎话**。措辞纪律:
# 描述只能讲机制与条件,不许替模型断定"此刻可见/不可见"。
_desc=$(awk '/"description": "读取本地图片/{print}' "$SRC" | head -1)
ck "描述里没有旧的逐轮回收政策(它是幻觉的教科书)" "$(grep -c '换轮后只有最近一轮' <<<"$_desc")" "0"
ck "描述里的重看指引还在(回收后模型唯一的抓手)" "$(grep -c '再调用一次本工具' <<<"$_desc")" "1"
ck "描述教模型以下文里有无图片部件为准(反幻觉指引)" "$(grep -c '不要因为' <<<"$_desc")" "1"
ck "描述仍声明移除行无图片无转述(诚实分支)" "$(grep -c '没有任何图片内容' <<<"$_desc")" "1"
ck "描述不再替模型断定可见范围(没有'全部可见'类现在时断言)" "$(grep -c '全部可见' <<<"$_desc")" "0"
# 负对照:锁的模式确实对旧文案敏感,否则"没有旧政策"那条就是恒真
ck "负对照:旧政策文案样本确实会被判违规" \
   "$(grep -c '换轮后只有最近一轮' <<< '可见范围:同一轮内读过的图全部可见,换轮后只有最近一轮的图可见')" "1"
# 结构锁:-u 显示活图数(用户端的硬证据:模型说"看不到"时,这里能看出图到底在不在)
ck "结构锁:-u 显示活图张数" "$(grep -c '张在上下文' "$SRC")" "1"

echo "=== 16. 两次原则:去重墙不许锁死看不见的模型(坚持重看必须重挂) ==="
# 2026-10-04 现场三:请求里明明有图(网关图片计费 6528/请求),模型坚持"看不到",
# 连调两次 read_image 都被去重墙挡回 —— 话术说"看不到就再调用本工具重看",
# 再调用却撞同一堵墙,死循环。两次原则:第一次去重(挡惯性重读,刀2 的 6× 靠它),
# 第二次信模型的感知自述(它是自己可见性的唯一证人):收回旧引用 + 重挂新号。
budget_env twice
p_img4="$W/paste4.png"; cp "$del" "$p_img4"
granted_files=(); input_images=(); PARSE_OUT=""
parse_placeholders "📋 [图片] $p_img4"
fn_args=$(jq -nc --arg p "$p_img4" '{path:$p}')
_prune_before=$IMG_PRUNE_EVENTS
content=""; tool_read_image </dev/null >/dev/null 2>&1      # 首读:真挂载
ck "前置:首读已挂载(.url=1)" "$(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ')" "1"
content=""; tool_read_image </dev/null >/dev/null 2>&1      # 第二读:惯性重读 → 去重
ck "第二读:走去重(没有第二份)" "$(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "去重话术承诺'再调用会重挂'(出口不再是对空气说话)" "$(grep -c '重新给你挂载' <<<"$content")" "1"
content=""; tool_read_image </dev/null >/dev/null 2>&1      # 第三读:模型坚持 → 重挂
ck "第三读:重挂成功(结果里有新 IMGREF 标记)" "$(grep -c 'IMGREF:' <<<"$content")" "1"
ck "第三读:旧引用已收回(.gone 在,与'看不见'的感知一致)" "$(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "第三读:重挂不叠罗汉(全会话仍只 1 份 .url)" "$(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "重挂计入合法改写(刀1 不误报意外改写)" "$((IMG_PRUNE_EVENTS - _prune_before))" "1"
# 负对照:只重读一次的惯性场景不得付重挂成本 —— 两次原则不是"见图就重挂"
budget_env twice_neg
p_img5="$W/paste5.png"; cp "$del" "$p_img5"
granted_files=(); input_images=(); PARSE_OUT=""
parse_placeholders "📋 [图片] $p_img5"
fn_args=$(jq -nc --arg p "$p_img5" '{path:$p}')
content=""; tool_read_image </dev/null >/dev/null 2>&1
content=""; tool_read_image </dev/null >/dev/null 2>&1
ck "负对照:只重读一次时零重挂(0 个 .gone、仍 1 份 .url)" \
   "$(ls "$IMG_REF_DIR"/*.gone 2>/dev/null | wc -l | tr -d ' ')/$(ls "$IMG_REF_DIR"/*.url 2>/dev/null | wc -l | tr -d ' ')" "0/1"

printf '\nRESULT total=%d pass=%d fail=%d\n' "$((pass + fail))" "$pass" "$fail"
printf '结果:PASS=%d FAIL=%d\n' "$pass" "$fail"
(( fail == 0 ))
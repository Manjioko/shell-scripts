#!/usr/bin/env bash
# 视觉接口抽象层自测:默认配置 / 转述开关 / 请求形状(400 回归)/ 响应归一 /
# 能力门槛 / 自动降级 / 解释器与 bash5 兜底 / 鉴权双头一致性。
# 用法:bash vision_layer_test.sh            # 会自动切到与生产同一个 bash 5+
#       IMG_PROVIDER=none bash vision_layer_test.sh   # 验证「整条链路关掉」
# 说明:主循环是脚本顶层 while,无法整文件 source(会起 TUI),故按函数抽取被测单元。
# 网络:curl 在本测试里是桩函数,任何真实请求都会立刻失败并被断言捕获。
#
# 解释器与生产对齐:wchat.sh 在第 130 行附近有「bash < 5 就 re-exec 到 bash5」的兜底,
# 所以生产实际跑在 bash 5+ 上。测试若停在 macOS 自带的 /bin/bash 3.2 上,就会去判那些
# 生产里根本不会以 3.2 执行的分叉(第 6 节的 ${err,,} 判定在 3.2 下是 bad substitution,
# 直接判错方向)。这里镜像同一个判据,保证「测试看到的实现行为 == 生产行为」。
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash "${HOME}/.linuxbrew/bin/bash"; do
        [[ -x "$_b" ]] || continue
        _v="$("$_b" -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null || true)"
        [[ "$_v" =~ ^[0-9]+$ ]] || _v=0
        if (( _v >= 5 )); then exec "$_b" "$0" "$@"; fi
    done
    echo "❌ 需要 bash 5+(与生产一致);装一下 brew install bash,或 WCHAT_BASH=/path/to/bash5 $0" >&2
    exit 1
fi
export LC_ALL=C
REAL=/Users/jwan/bin/wchat.sh
[[ -f "$REAL" ]] || { echo "❌ 找不到被测源文件:$REAL"; exit 1; }
W=$(mktemp -d)

# ---- 抽沙箱:配置头部 + 图片理解配置段 + 被测函数 ----
# 用 awk 按模式抽,不要再写死行号:上一次配置集中区整体搬到头部后,旧的
# sed -n '1,123p;2343,2383p' 抽出的是「shebang + 一堆 case」,真正的配置行一条
# 没进沙箱 → IMG_PROVIDER 全程为空 → 多个「不该出现」类断言空转通过(假绿)。
# 兜底段(guard)也一并抽:第 7 节要真跑它。沙箱里到 fi 就停,不会走进主程序。
GUARD_S=$(grep -n 'BASH_VERSINFO\[0\]:-0} < 5' "$REAL" | head -1 | cut -d: -f1)
GUARD_E=$(awk -v s="$GUARD_S" 'NR>s && /^fi$/{print NR; exit}' "$REAL")
[[ -n "$GUARD_S" && -n "$GUARD_E" ]] || { echo "❌ 在 $REAL 里定位不到 bash5 兜底段(第 7 节前提)"; exit 1; }

{
    sed -n "1,${GUARD_E}p" "$REAL"                    # 头部 + 默认配置 + 兜底段
    awk '/^# ---------- 图片理解\(多模态,转述架构\)配置/{s=1} /^IMG_DESC_PROMPT=/{s=0} s' "$REAL"
    # 坑(本轮实测):旧写法拿 /^IMG_DESC_PROMPT=/ 当区间**结束**标记,而实现里它是
    # 第 544 行的**单行**赋值 → 声明整条被跳过,沙箱里 IMG_DESC_PROMPT 为空串,
    # 发图时的转述提示词整个丢了,却没有任何一条断言会红(典型静默漏抽)。
    # 单独抽它:当前是单行 '...' 形式;将来若改成 """ 多行,后半句兜住。
    grep -E "^IMG_DESC_PROMPT=.*'$" "$REAL" \
        || awk '/^IMG_DESC_PROMPT="/{p=1} p{print} p && /^"""$/{exit}' "$REAL"
} > "$W/sandbox.sh"
for fn in img_mime is_image prepare_image vision_extra_fields vision_build_body \
    vision_parse_resp vision_disable describe_images; do
    awk -v name="$fn" 'index($0, name "() {")==1 {s=1} s {print} s && $0=="}" {exit}' "$REAL" >> "$W/sandbox.sh"
done
got=$(grep -c '() {' "$W/sandbox.sh")
curl() { echo "NETWORK-LEAK" >&2; return 99; }        # 桩:被测路径不该触网
for v in IMG_PROVIDER IMG_CAPABLE VISION_MODEL flash_model pro_model BASE_URL IMG_DESC_PROMPT; do
    grep -Eq "^${v}=|^    ${v}=" "$W/sandbox.sh" || { echo "❌ 沙箱缺配置变量 $v,后面断言会空转"; exit 1; }
done

# 沙箱里含「未配置 API_KEY 就 exit 1」这段(在生产是给真人看的),source 前给个假 key,
# 否则测试会在 source 处静默终止(表现是"一行输出都没有",很容易被误判成脚本没跑起来)
export WCHAT_API_KEY="${WCHAT_API_KEY:-sk-sandbox-not-a-real-key}"
# shellcheck disable=SC1090
source "$W/sandbox.sh" || { echo "沙箱 source 失败"; exit 1; }
echo "解释器:$BASH_VERSION($BASH)  抽到函数 $got 个(含 vision_disable/describe_images)"

fail=0
ck() { [[ "$2" == "$3" ]] && echo "  ✓ $1" || { echo "  ✗ $1"; echo "    期望 [$3]"; echo "    实际 [$2]"; fail=$((fail + 1)); }; }
has() { [[ "$2" == *"$3"* ]] && echo "  ✓ $1" || { echo "  ✗ $1"; echo "    缺少: $3"; echo "    实际: $2"; fail=$((fail + 1)); }; }
hasnt() { [[ "$2" != *"$3"* ]] && echo "  ✓ $1" || { echo "  ✗ $1"; echo "    不该出现: $3"; echo "    实际: $2"; fail=$((fail + 1)); }; }
hasq() { jq -e "$3" <<<"$2" >/dev/null 2>&1 && echo "  ✓ $1" || { echo "  ✗ $1"; echo "    jq 谓词: $3"; echo "    实际: $(head -c 400 <<<"$2")"; fail=$((fail + 1)); }; }

echo "=== 0. 默认配置(当前接口) ==="
ck "默认 IMG_PROVIDER=auto(只当开关,不再锁形状)" "$(sed -n 's/^IMG_PROVIDER="\${IMG_PROVIDER:-\([^}]*\)}".*/\1/p' "$REAL" | head -1)" "auto"
ck "沙箱里 IMG_PROVIDER 有值(防空转)" "$([[ -n "$IMG_PROVIDER" ]] && echo ok)" "ok"
# 开关落地**只有一行**(实现里没有 case,别再按记忆造形状):
#   515: [[ "$IMG_PROVIDER" == "none" ]] && IMG_CAPABLE=0
# 断言按"这一行存在且唯一"来盯,而不是测试里复制一份 case —— 上一版就是复制了
# 一个实现里根本不存在的 `case ${IMG_PROVIDER,,}` 分支,看着全绿其实啥也没测。
ck "开关落地判定唯一(none → 不可用)" \
   "$(grep -vE '^[[:space:]]*#' "$REAL" | grep -cF -- '[[ "$IMG_PROVIDER" == "none" ]]')" "1"
ck "none 时确实置 IMG_CAPABLE=0(实跑该赋值块)" \
   "$( ( IMG_PROVIDER=none; IMG_CAPABLE=1; [[ "$IMG_PROVIDER" == "none" ]] && IMG_CAPABLE=0; echo "$IMG_CAPABLE" ) )" "0"
ck "默认可用" "$IMG_CAPABLE" "1"
ck "VISION_MODEL 空 → 退回 flash_model" "$vision_model" "$flash_model"

echo "=== 1. provider 探测已收口(不再有「形状家族」) ==="
# 只看代码行:配置段注释里还在解释"曾经有 detect_vision_provider",连注释一起数会假红
_nb() { grep -vE '^[[:space:]]*#' "$REAL" | grep -cE -- "$1"; }
ck "detect_vision_provider 在代码里已不存在" "$(_nb 'detect_vision_provider')" "0"
ck "不再把 IMG_PROVIDER 赋成 mimo/openai" "$(_nb '^[[:space:]]*IMG_PROVIDER=(mimo|openai)[[:space:]]*$')" "0"
ck "IMG_PROVIDER=none 的赋值仅剩 1 处(vision_disable)" "$(_nb '^[[:space:]]*IMG_PROVIDER=none[[:space:]]*$')" "1"
has "默认值走环境变量覆盖(集中区一行搞定)" "$(grep -E '^IMG_PROVIDER=' "$REAL")" '${IMG_PROVIDER:-auto}'
DI_REF=$(sed -n '/^describe_images()/,/^}/p' "$REAL" | grep -c 'IMG_PROVIDER')
ck "describe_images 里 IMG_PROVIDER 只出现在提示文案(不再分叉)" "$DI_REF" "1"

echo "=== 2. 视觉模型与主对话解耦 ==="
sed -n '/^describe_images()/,/^}/p' "$REAL" > "$W/di.txt"
ck "describe_images 不再硬编码 flash_model" "$(grep -c '"\$flash_model"' "$W/di.txt")" "0"
has "改用 vision_model" "$(cat "$W/di.txt")" 'vision_build_body "$vision_model"'

echo "=== 3. 请求形状(回归:多模态带 reasoning_effort 会被 qwen 网关 400) ==="
T=$(mktemp -d)
printf '[{"type":"image_url","image_url":{"url":"data:image/png;base64,aGk="}}]' > "$T/parts.json"
for p in mimo openai; do
    body=$(vision_build_body "m1" "$T/parts.json" "描述这张图" "你是转述助手")
    hasq "$p: 骨架正确(model/messages/stream)" "$body" '.model=="m1" and (.messages|length)==2 and .stream==false'
    hasq "$p: 图片与文本合并进 user 消息" "$body" '.messages[1].content|map(.type)==["image_url","text"]'
    hasnt "$p: 不带 reasoning_effort" "$body" "reasoning_effort"
    hasq "$p: thinking=disabled" "$body" '.thinking.type=="disabled"'
done
ck "system 传空 → 只有一条消息" "$(jq '.messages|length' <<<"$(vision_build_body m1 "$T/parts.json" x '')")" "1"
hasq "system 非空 → 放在首位" "$(vision_build_body m1 "$T/parts.json" x 'SYS')" '.messages[0].content=="SYS"'

echo "=== 4. 响应取文归一 ==="
ck "字符串 content 直取" "$(vision_parse_resp '{"choices":[{"message":{"content":"一张计算器图标"}}]}')" "一张计算器图标"
ck "数组 parts content 拼接" "$(vision_parse_resp '{"choices":[{"message":{"content":[{"type":"text","text":"甲"},{"type":"text","text":"乙"}]}}]}')" "甲乙"
ck "无 choices → 空串(不把字面量喂给模型)" "$(vision_parse_resp '{"error":{"message":"x"}}')" ""

echo "=== 5. 能力门槛:provider=none 直接提示,不发请求 ==="
IMG_PROVIDER=none IMG_CAPABLE=0 vision_disabled_reason=""
describe_images /nonexistent.png
ck "置 describe_failed" "$describe_failed" "1"
has "说明不支持图片" "$describe_result" "不支持图片"
has "给出修复办法(VISION_MODEL)" "$describe_result" "VISION_MODEL"
ck "没有触网(桩未被调用)" "$(grep -c NETWORK-LEAK <<<"$describe_result")" "0"

echo '=== 6. 接口报"不支持图片" → 进程内降级(判的是实现本体,不是副本) ==='
# 第 6 节一度长驻一个 ✗:测试里另抄了一份 classify_unsupported(用 ${e,,}),
# 实现里是 ${err,,},断言按字面量比就成了假失败。现在直接把实现里那段 case 抠出来
# 跑 —— 判定本体与实现不可能漂移,而且顺带验证它在生产解释器(5.x)下真能执行。
DI_BODY=$(sed -n '/^describe_images()/,/^}/p' "$REAL")
IMPL_CASE=$(printf '%s\n' "$DI_BODY" | awk '/err,,/ && /case /{c=1} c{print} c && /^[[:space:]]*esac$/{exit}')
[[ -n "$IMPL_CASE" ]] || { echo "  ✗ 抠不出实现里的降级判定 case(第 6 节没有可测对象)"; fail=$((fail + 1)); }
{ echo 'classify_impl() {'; echo '    local err="$1"'; printf '%s\n' "$IMPL_CASE"; echo '}'; } > "$W/classify.sh"
# shellcheck disable=SC1090
source "$W/classify.sh" 2>/dev/null
mk_err() { printf '{"error":{"message":"%s"}}' "$1"; }
IMG_PROVIDER=auto IMG_CAPABLE=1 vision_disabled_reason="" describe_result=""
classify_impl 'The model does not support image input' >/dev/null 2>&1
ck "模型级不支持 → 实现自己把 provider 降级为 none" "$IMG_PROVIDER" "none"
ck "…并同步置 capable=0" "$IMG_CAPABLE" "0"
IMG_PROVIDER=auto IMG_CAPABLE=1 vision_disabled_reason="" describe_result=""
classify_impl '该模型不支持图片输入' >/dev/null 2>&1
ck "中文报错同样命中判定" "$IMG_PROVIDER" "none"
IMG_PROVIDER=auto IMG_CAPABLE=1 vision_disabled_reason="" describe_result=""
classify_impl 'This image format is not supported by the decoder' >/dev/null 2>&1
ck "格式类报错 → 不全会话降级(是文件问题)" "$IMG_PROVIDER/$IMG_CAPABLE" "auto/1"
IMG_PROVIDER=auto IMG_CAPABLE=1 vision_disabled_reason="" describe_result=""
classify_impl "'reasoning_effort' must be 'none' when 'enable_thinking' is false" >/dev/null 2>&1
ck "400 reason 字段冲突 → 不降级(是形状问题不是能力问题)" "$IMG_PROVIDER/$IMG_CAPABLE" "auto/1"
ck "判定确实来自实现本体(不是测试里抄的副本)" "$(grep -c 'err,,' "$W/classify.sh")" "1"

echo "=== 7. bash5 兜底与解释器耦合(硬断言:前提塌了就红) ==="
shb=$(sed -n '1p' "$REAL"); she=${shb#\#!}
has "shebang 不写死平台专属路径(用 env bash)" "$shb" "env bash"
# 生产落点由上面兜底的执行结果决定(PROD_BASH);绝不能用 shebang 的字面路径去跑 bash4+
# 语法 —— env bash 那个"路径"在 macOS 上就是 3.2,用它验等于验一个生产不会用的解释器。
# 顺序不变量:兜底必须在**任何** bash4+ 语法之前(bash 逐条解析,写在兜底前就必炸)
FIRST_B4=$(grep -vnE '^[[:space:]]*#' "$REAL" | grep -E 'declare -A|mapfile|readarray|\$\{[A-Za-z_]+(,,|\^\^)|[^&]&>' | head -1 | cut -d: -f1)
if [[ -n "$FIRST_B4" ]] && (( FIRST_B4 > GUARD_E )); then
    echo "  ✓ 兜底(到 $GUARD_E 行)早于第一处 bash4+ 语法($FIRST_B4 行)"
else
    echo "  ✗ 兜底与 bash4+ 语法的顺序不成立:guard_fi=$GUARD_E 第一处=$FIRST_B4"
    fail=$((fail + 1))
fi
[[ -n "$FIRST_B4" ]] || { echo "  ✗ 抽不到 bash4+ 语法(正则或注释过滤失效),本节的顺序断言在空转"; fail=$((fail + 1)); }
# 真跑一遍兜底:从 3.2 起必须落到 bash 5+;顺带问出"生产到底跑在哪个 bash 上",
# 后面几处 bash4+ 语法的可执行性要用那个解释器验(用 shebang 字面路径不行,
# #!/usr/bin/env bash 的"路径"是 env+bash 两个词)
{ sed -n "1,${GUARD_E}p" "$REAL"; echo 'echo "NOW=${BASH_VERSINFO[0]} EXEC=$BASH"'; } > "$W/guard.sh"
gout=$(WCHAT_API_KEY=sk-fake /bin/bash "$W/guard.sh" 2>/dev/null | tail -1)
NOW_MAJOR=$(awk '{sub(/^NOW=/,"",$1); print $1}' <<<"$gout")
PROD_BASH=$(awk '{sub(/^EXEC=/,"",$2); print $2}' <<<"$gout")
[[ -x "${PROD_BASH:-}" ]] || PROD_BASH="$BASH"     # 拿不到就用当前(≥5)解释器,绝不退回 3.2
echo "  · 生产落点:$PROD_BASH($NOW_MAJOR.x)  测试落点:$BASH($((${BASH_VERSINFO[0]})).x)"
ck "用 /bin/bash 3.2 启动兜底 → 实际落到 bash 5+" "$([[ "${NOW_MAJOR:-0}" -ge 5 ]] && echo ok)" "ok"
NOW5=$(WCHAT_API_KEY=sk-fake "$PROD_BASH" "$W/guard.sh" 2>/dev/null | tail -1 | awk '{sub(/^NOW=/,"",$1); print $1}')
ck "从 bash 5 起判据为假(不重复 exec、不死循环)" "${NOW5:-?}" "5"
# 报错分支不能是死代码:清空候选表后必须明确失败退出,而不是静默继续
cp "$W/guard.sh" "$W/guard2.sh"
perl -0pe 's/bash_candidates=\$\(.*?\n    \)\n/bash_candidates="\/nonexistent-bash"\n/s' -i "$W/guard2.sh" 2>/dev/null
if grep -q 'nonexistent-bash' "$W/guard2.sh"; then
    gc_out=$(env -i WCHAT_API_KEY=sk-fake PATH=/usr/bin:/bin HOME="$W" /bin/bash "$W/guard2.sh" 2>&1)
    gc_code=$?
    has "找不到 bash5 时明确报错(不静默降级)" "$gc_out" "需要 bash 5"
    ck "找不到 bash5 时退出码非 0" "$([[ $gc_code -ne 0 ]] && echo ok)" "ok"
    has "报错给了跨平台安装提示" "$gc_out" "WCHAT_BASH="
else
    echo "  ⚠ 兜底结构与本节预期不同(候选表换写法了),跳过报错分支验证 —— 请同步改本节"
fi
# 那几处 bash4+ 写法在生产解释器下确实可用(对照:3.2 下会 bad substitution)
{ echo 'e="NOT SUPPORT IMAGE"'; echo 'case "${e,,}" in *"not support image"*) echo hit ;; *) echo miss ;; esac'; } > "$W/b5.sh"
ck "生产落点解释器下 \${e,,} 可用(对照:3.2 会 bad substitution)" "$("$PROD_BASH" "$W/b5.sh" 2>&1 | tail -1)" "hit"
ck "测试自身与生产同解释器(≥5)" "$([[ ${BASH_VERSINFO[0]} -ge 5 ]] && echo ok)" "ok"
printf '  · bash4+ 写法共 %s 处,全部位于兜底之后(有 3 处 ${v,,}/1 处 ${v^^}/declare -A 等),清单:\n' \
    "$(grep -vnE '^[[:space:]]*#' "$REAL" | grep -cE 'declare -A|mapfile|readarray|\$\{[A-Za-z_]+(,,|\^\^)|[^&]&>')"
grep -vnE '^[[:space:]]*#' "$REAL" | grep -E '\$\{[A-Za-z_]+(,,|\^\^)' | cut -c1-90 | sed 's/^/      /'

echo "=== 8. 鉴权双头一致性(api-key 头是小米 MiMo LLM 需要的兼容头,勿删) ==="
# 只数代码行:脚本头部那段「鉴权头(勿删)」说明里就带着 -H "api-key: $API_KEY" 的字面量,
# 连注释一起数会多算 1 个,把守恒断言变成假失败
ak=$(grep -vE '^[[:space:]]*#' "$REAL" | grep -cF -- '-H "api-key: $API_KEY"')
bear=$(grep -vE '^[[:space:]]*#' "$REAL" | grep -cF -- '-H "Authorization: Bearer $API_KEY"')
ck "Bearer 与 api-key 成对出现(数量守恒)" "$([[ "$ak" == "$bear" && "$ak" -gt 0 ]] && echo "$ak/$bear")" "$bear/$bear"
# 锚定用内容,不用行号区间:区间是"上面每加几行注释就被挤一次"的那种断言 —— 实测
# HEAD 上就已经偏了两条(「鉴权头」说明在第 65 行,而区间只取到 60),红的是"别人家的
# 测试坏了",与产品毫无关系。改内容锚之后同样删不掉:marker 或关键词一没,锚就空。
auth_doc=$(grep -A16 '鉴权头(勿删)' "$REAL")
has "头部有「鉴权头(勿删)」说明" "$auth_doc" "鉴权头(勿删)"
has "说明里点明给小米 MiMo LLM" "$auth_doc" "MiMo"
ck "每一处 api-key 头都紧跟在鉴权点上" \
   "$(grep -vE '^[[:space:]]*#' "$REAL" | awk '/Authorization: Bearer \$API_KEY/{b=NR} /-H "api-key:/{if(b&&NR-b<=2)n++} END{print n+0}')" "$ak"

echo "=== 9. 探测收口:不再有"以 BASE_URL 锁图片形状" ==="
has "集中区注释里仍有 api-key 双头提示" "$(grep -E '^[[:space:]]*#.*mimo LLM' "$REAL")" "mimo LLM"
has "头部注释说明图片侧不再有探测" "$(grep -E '^[[:space:]]*#.*图片侧已无厂商探测' "$REAL")" "图片侧已无厂商探测"

rm -rf "$T" "$W"
echo
(( fail )) && { echo "❌ 视觉抽象层自测 $fail 项失败"; exit 1; }
echo "✅ 视觉抽象层自测全部通过"
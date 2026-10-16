#!/usr/bin/env bash
# ↑ 不写死 /bin/bash:见下面「与生产同解释器」段。Linux 上 bash 位置随发行版不同,
#   macOS 的 env bash 仍是 3.2,由该段 re-exec 到 bash 5+。
# 端到端:真实图片走改造后的 describe_images。四条链路 —— 默认端点 / VISION_MODEL 显式
# 指定 / 换 mimo 端点(探针,验证"换家要不要改代码")/ 手工关闭(应零请求)。
# 主循环在脚本顶层,无法 source 整个脚本 → 按函数抽取沙箱,与自测同一套提取逻辑。
#
# 2026-09-14 同步:detect_vision_provider 与 mimo/openai「形状家族」已从 wchat.sh 收掉,
# IMG_PROVIDER 现在只是 auto|none 开关(两家的图片请求形状实测完全一致),抽取也从按行号
# 改成按模式(原 `sed -n '1,25p'` 会随配置集中区搬迁静默漏抽 flash_model)。
# ⚠ 本脚本会**真实发请求并扣费**,只在手动排查时跑;日常回归用 vision_layer_test.sh。
export LC_ALL=C
# 与生产同解释器:wchat.sh 会在 bash<5 时 re-exec 到 bash 5+,而沙箱里的 describe_images
# 带着 `case "${err,,}"`(bash4+ 语法)。在 macOS 的 /bin/bash 3.2 上跑这份 e2e,降级分支会
# bad substitution、临时目录不清 —— 测出来的行为和线上不是一回事,所以这里镜像同一个判据。
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash "${HOME}/.linuxbrew/bin/bash"; do
        [[ -x "$_b" ]] || continue
        _v="$("$_b" -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null || true)"
        [[ "$_v" =~ ^[0-9]+$ ]] || _v=0
        if (( _v >= 5 )); then exec "$_b" "$0" "$@"; fi
    done
    echo "❌ 需要 bash 5+(与生产一致)。brew install bash,或 WCHAT_BASH=/path/to/bash5 $0" >&2
    exit 1
fi
REAL=${REAL:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/wchat.sh"}
W=$(mktemp -d)

{
    grep -E '^(BASE_URL|API_KEY|flash_model|pro_model)=' "$REAL"
    grep -E '^(VISION_MODEL|vision_model|IMG_PROVIDER|IMG_CAPABLE|IMG_MAX_BYTES|IMG_MAX_SIDE|IMG_MAX_BATCH|IMG_BATCH_MAX_BYTES)=' "$REAL"
    grep -E '^\[\[ "\$IMG_PROVIDER" == "none" \]\]' "$REAL"
    # 到 IMG_DESC_PROMPT **之前**为止:那行是 `IMG_DESC_PROMPT="""`,把它当普通行抽进
    # 沙箱会留下一个未闭合的引号 → 后面追加的函数体全被吞进字符串里(source 不报错,
    # 但函数根本不存在,跑起来才以 command not found 收场)。多行串单独按起止行抽。
    awk '/^# ---------- 图片转述\(单端点前提下的最小组合\)/{s=1} /^IMG_DESC_PROMPT=/{s=0} s' "$REAL"
    # 同上:IMG_DESC_PROMPT 是单行赋值,旧写法当区间结束标记会把它整条跳过 → 沙箱里
    # 转述提示词为空,真实发图时把默认指令整个丢了。单独抽 + 下面非空自检。
    grep -E "^IMG_DESC_PROMPT=.*'$" "$REAL" \
        || awk '/^IMG_DESC_PROMPT="/{p=1} p{print} p && /^"""$/{exit}' "$REAL"
} > "$W/sandbox.sh"
for fn in img_mime is_image img_dims prepare_image prepare_delivery img_reject_wording \
    vision_extra_fields vision_build_body vision_parse_resp vision_disable describe_images _py_on py_run; do
    awk -v name="$fn" 'index($0, name "() {")==1 {s=1} s {print} s && $0=="}" {exit}' "$REAL" >> "$W/sandbox.sh"
done
# describe_images 现在共用 prepare_delivery(而它要用 img_dims)。漏抽的后果不是报错,是
# "command not found" 被当成压图失败 —— 断言全跑在假路径上,所以这里显式自检依赖。
for fn in img_dims prepare_delivery img_reject_wording; do
    grep -q "^${fn}() {" "$W/sandbox.sh" || { echo "❌ 沙箱没抽到 ${fn}(describe_images 的依赖)"; exit 1; }
done
# shellcheck disable=SC1090
source "$W/sandbox.sh"
# 迁移通道:vision_build_body/parse_resp 已收口(旧 bash jq 回退已删)→ 必须走 Python 生产路。
PY_HELPER_DIR="$(dirname "$REAL")"; WCHAT_PY=1
for v in BASE_URL flash_model vision_model IMG_PROVIDER IMG_DESC_PROMPT; do
    [[ -n "${!v}" ]] || { echo "❌ 沙箱抽取不完整:$v 为空 —— wchat.sh 排版漂了,先修抽取"; exit 1; }
done

# 密钥与主程序同源:环境变量 WCHAT_API_KEY 优先,其次 ~/.ct/config(只 source,绝不打印)
API_KEY="${WCHAT_API_KEY:-${API_KEY:-}}"
if [[ -z "$API_KEY" && -f "$HOME/.ct/config" ]]; then
    # shellcheck disable=SC1090
    source "$HOME/.ct/config"
    API_KEY="${WCHAT_API_KEY:-${API_KEY:-}}"
fi
[[ -n "$API_KEY" ]] || { echo "❌ 没有 API_KEY:export WCHAT_API_KEY=... 或写进 ~/.ct/config"; exit 1; }

IMG="$W/probe.png"
sips -s format png --resampleWidth 420 /System/Applications/Calculator.app/Contents/Resources/appicon.icns --out "$IMG" >/dev/null 2>&1
echo "测试图 420px / $(wc -c < "$IMG" | tr -d ' ') 字节"

run() {  # $1=标签
    echo
    echo "--- $1 ---"
    echo "    provider=$IMG_PROVIDER  vision_model=$vision_model  网关=${BASE_URL%%/v1*}"
    local t0=$SECONDS
    describe_images "$IMG"
    printf '    describe_failed=%s  耗时 %ss\n' "$describe_failed" "$((SECONDS - t0))"
    printf '    转述: %s\n' "$(head -c 300 <<<"$describe_result" | tr '\n' ' ')"
}

# 1) 默认配置(qwen maas + flash_model 档):形状收敛前这条必 400
run "默认端点(单形状)"

# 2) VISION_MODEL 显式指定(与主对话解耦的能力)
(
    VISION_MODEL="$flash_model"
    vision_model="${VISION_MODEL:-$flash_model}"
    run "VISION_MODEL 显式赋值"
)

# 3) 换到 mimo 端点:网关/模型全换,链路其它部分不动。
#    注意这段是**探针**,不代表主程序支持跨厂商挂图(wchat.sh「图片转述」节明确写了不做:
#    跨厂商要独立 URL + 独立密钥,会把单一 API_KEY 扩成密钥矩阵)。
#    真正需要保留的是鉴权头 —— describe_images 里那条 `-H "api-key: $API_KEY"` 就是
#    小米 MiMo LLM 要求的兼容头,qwen 侧忽略它。删了这段就会 401。
(
    BASE_URL="https://api.xiaomimimo.com/v1/chat/completions"
    # mimo 的密钥与 qwen 的不是同一把:单独 export MIMO_API_KEY 才跑这段,
    # 没设就沿用当前 API_KEY(届时 401 属预期,不是链路故障)
    [[ -n "${MIMO_API_KEY:-}" ]] && API_KEY="$MIMO_API_KEY"
    IMG_PROVIDER=auto        # 只是开关,不再是形状家族;两家图片形状实测一致
    IMG_CAPABLE=1
    vision_model="mimo-v2.5"
    run "mimo-v2.5(靠保留的 api-key 头鉴权)"
)

# 4) 手工关闭:应零请求、直接给人话提示
(
    IMG_PROVIDER=none IMG_CAPABLE=0
    run "IMG_PROVIDER=none(应提示不支持,不触网)"
)

rm -rf "$W"
#!/usr/bin/env bash
# MCP 通道闸门(离线)。
#
# 装载方式:用主脚本自己的 _WCHAT_LIB_ONLY 口整文件装载,再**覆盖**三个协作者
#   run_with_window / confirm_cmd / auto_audit。
# 桩覆盖的是 mcp_cli(MCP 唯一的执行出口),不再桩 run_with_window ——
#   产品侧已改为"argv 直执 + 输出落文件",因为给人看的 pty 窗口会让子进程开 spinner
#   把要解析的 JSON 冲掉(本轮实测)。
# 为什么不用逐段 awk 抽取:那段区间要靠注释行当锚,注释一改就静默抽出空函数,
# 现象是"断言全绿但其实什么都没测"(isolate_section.sh 与本仓库 T23 都栽过同类)。
# 整文件装载还额外覆盖到 tools() 的条件追加与 parse_options 的 -mcp 解析。
#
# 为什么全走桩不打真 mcporter:闸门必须在无网、无 Chrome、无 npm 缓存的机器上
# 给出**同样**的结论(run_gates 的判据是 rc=0 + 自家结论行)。真实链路是另一道。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
# 被测文件可由第一个参数指定(变异装置会把**副本**路径传进来)。
# 写死 wchat.sh 的后果实测过:变异跑在副本上、断言却读工作原件,于是"未杀"是假的 ——
# 本仓库 T23/T25 出过同一个病,闸门必须按传进来的那份跑。
SRC="${1:-wchat.sh}"
[[ -f "$SRC" ]] || { echo "找不到 $SRC"; exit 2; }
SRC=$(cd "$(dirname "$SRC")" && pwd -P)/$(basename "$SRC")
BASHX="${BASH:-bash}"
if [[ "${BASH_VERSINFO[0]}" -lt 5 ]]; then
    for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$b" ]] && BASHX="$b" && break
    done
fi

pass=0; fail=0
ck()  { if [[ "$2" == "$3" ]]; then echo "  ✓ $1"; pass=$((pass+1)); else echo "  FAIL ✗ $1"; echo "      期望 [$3] / 实际 [$2]"; fail=$((fail+1)); fi; }
ckn() { if [[ "$2" != "$3" ]]; then echo "  ✓ $1"; pass=$((pass+1)); else echo "  FAIL ✗ $1(不应相等)"; echo "      两边都是 [$2]"; fail=$((fail+1)); fi; }

W=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$W"' EXIT
export HOME="$W/home"; mkdir -p "$HOME" "$W/proj"
export TMPDIR="$W/tmp"; mkdir -p "$TMPDIR"
export WCHAT_API_KEY="${WCHAT_API_KEY:-sk-stub}"          # 装载口需要非空 key
export BASE_URL="${BASE_URL:-https://stub.invalid/v1}"
REAL="$SRC"

# 每次起一个 bash,装载真脚本 + 打桩三件套,再执行传进来的片段
run() {
    # ⚠ 必须在**函数上下文**里 source:装载口写的是 `return 0 2>/dev/null || exit 0`,
    #   在 bash -c 顶层 return 会失败并走 exit —— 表现是"源化后一行输出都没有",
    #   看着像被测代码坏了,其实是装载姿势不对(本轮踩过两次)。
    "$BASHX" -c "
set +u
_mcp_load() { _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }
_mcp_load
CLIP_DIR='$W/clip'; MCP_STATE_DIR=''; MCP_EXPOSED=''; AUDIT_FILE='$W/audit.txt'; : > \$AUDIT_FILE
mkdir -p \"\$CLIP_DIR\"
mcp_cli() { MCP_OUT="\${MOCK_OUT:-}"; MCP_ERR=""; LAST_CMD="\$*"; return \${MOCK_RC:-0}; }
confirm_cmd() { CONFIRMED=1; return \${MOCK_CONFIRM:-0}; }
auto_audit() { printf '%s|%s|%s|%s|%s\n' "\$1" "\$2" "\$3" "\$4" "\$5" >> \$AUDIT_FILE; }
$1
" 2>&1 | tr -d '\r' | perl -pe 's/\e\[[0-9;?]*[a-zA-Z]//g'
}

LIST_OK='{"servers":[
 {"name":"chrome","status":"ok","tools":[{"name":"evaluate","description":"Evaluates a JS script"},{"name":"navigate","description":"Go to url"}]},
 {"name":"node_repl","status":"ok","tools":[{"name":"js","description":"run js"}]}]}'

echo "=== A 装载与缺席守卫(装不上就红,不空跑) ==="
ck "MCP 通道函数齐" "$(run 'declare -F mcp_boot inject_mcp tool_mcp_call mcp_name_ok >/dev/null && echo ok || echo missing')" "ok"
ck "常量在装载口可见" "$(run 'echo "${MCP_ENABLE:-none}"')" "0"

echo "=== B 名字校验(唯一进 argv 的模型可控标识符) ==="
for pair in "chrome|ok" "my_srv-1|ok" "a/b|no" "../etc|no" "-y|no" "a_b c|no" "$(printf 'x%.0s' {1..65})|no"; do
    n="${pair%|*}"; want="${pair##*|}"
    ck "mcp_name_ok 拒/收 [$n]" "$(run "mcp_name_ok '$n' && echo ok || echo no")" "$want"
done
ck "空名被拒" "$(run "mcp_name_ok '' && echo ok || echo no")" "no"

echo "=== C mcp_boot:只暴露本地声明的 server ==="
mkcfg() { printf '%s' "$1" > "$W/mc.json"; }
mkcfg '{"imports":[],"mcpServers":{"chrome":{"command":"npx"}}}'
out=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1
echo \"EXPOSED=[\$(printf '%s ' \$MCP_EXPOSED)]\"
echo \"N=\$(jq 'length' <<< \"\$MCP_TOOLS_JSON\")\"")
ck "只暴露配置文件里声明的 chrome" "$(printf '%s' "$out" | grep -o 'EXPOSED=\[[^]]*\]')" "EXPOSED=[chrome ]"
ck "本地清单齐全时注册 3 条通道" "$(printf '%s' "$out" | grep -o 'N=.*')" "N=3"
warn=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot 2>&1
")
ck "外来源(node_repl)被排除并大声报出" "$(printf '%s' "$warn" | grep -c '来自其它程序的配置')" "1"

mkcfg '{"imports":[],"mcpServers":{"chrome":{"command":"npx"},"figma":{"command":"x"}}}'
out=$(MOCK_OUT='{"servers":[{"name":"chrome","tools":[{"name":"evaluate"}]},{"name":"figma","tools":[{"name":"get_data"}]}]}' run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=(figma)
mcp_boot >/dev/null 2>&1; echo \"EXPOSED=[\$(printf '%s ' \$MCP_EXPOSED)]\"")
ck "-mcp figma 只暴露点名的那个" "$(printf '%s' "$out" | grep -o 'EXPOSED=\[[^]]*\]')" "EXPOSED=[figma ]"

out=$(MOCK_OUT='{"servers":[{"name":"chrome","status":"offline","tools":[]}]}' run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1; echo \"ENABLED=\$MCP_ENABLE\"; echo \"N=\$(jq 'length' <<< \"\$MCP_TOOLS_JSON\")\"")
ck "server 全离线 → 关回 MCP_ENABLE=0" "$(printf '%s' "$out" | grep -o 'ENABLED=.*')" "ENABLED=0"
ck "离线时注册体保持为空" "$(printf '%s' "$out" | grep -o 'N=.*')" "N=0"

# daemon 被硬杀后留下的"上次意外退出"标记会让全部 server 变 error,
# 且 stop/start 都清不掉(实测)。这条路径必须有**独立于"没有可用工具"**的指引,
# 否则用户看到的是一句无信息量的话,而真因在模型读不到的地方(凭据规则刻意拦着)。
out=$(MOCK_OUT='{"servers":[{"name":"chrome","status":"error","tools":[]}]}' run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_cli() { MCP_OUT=\${MOCK_OUT:-}; MCP_ERR='Previous daemon exited unexpectedly; no replacement was launched.'; return 0; }
mcp_boot 2>&1 >/dev/null; echo "ENABLED=\$MCP_ENABLE"
")
ck "daemon 异常退出标记被单独识别" "$(printf '%s' "$out" | grep -c '异常退出')" "1"
ck "给出人工恢复步骤(前台带日志启动)" "$(printf '%s' "$out" | grep -c 'foreground --log')" "1"
ck "该场景仍退回无 MCP,不炸主流程" "$(printf '%s' "$out" | grep -o 'ENABLED=.*')" "ENABLED=0"
out=$(MOCK_OUT='{"servers":[{"name":"chrome","status":"offline","tools":[]}]}' run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=(); mcp_boot 2>&1 >/dev/null; echo done
")
ck "普通不可用不被误报成 daemon 阻塞" "$(printf '%s' "$out" | grep -c '异常退出')" "0"

out=$(MOCK_OUT='' run "
MCP_ENABLE=1; MCP_CONFIG='$W/nope.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1; echo \"ENABLED=\$MCP_ENABLE\"")
ck "配置缺失 → 按无 MCP 运行,不报错退出" "$(printf '%s' "$out" | grep -o 'ENABLED=.*')" "ENABLED=0"

echo "=== D L0 清单:常驻与超预算降级 ==="
out=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1
messages=\$(jq -n '[{role:\"system\",content:\"base\"}]')
inject_skills >/dev/null 2>&1 || true
inject_mcp
echo \"\$messages\" | jq -r '.[0].content'")
ck "清单进了 messages[0]" "$(printf '%s' "$out" | grep -c '可用外部能力')" "1"
# 路由句是真机打出来的教训:只给工具名时模型会去调 web_fetch(它问的就是"看这个 URL")。
# 这段没有内容检查,以后被"顺手精简提示词"删掉是静默的 —— 所以钉住。
# ⚠ 钉的是**跨 server 通用**的那句(外部程序的运行态 vs 静态内容),不是某个 server 的能力清单。
#   上一版这里钉的是"web_fetch 只取静态 HTML / 文件:行号",等于把浏览器叙事焊进常驻提示:
#   只接 figma 时它每轮都在讲浏览器。反向锁(下面两条 =0)防的就是它以后被"顺手加回来"。
ck "L0 含与内置工具的通用分工句"   "$(printf '%s' "$out" | grep -c 'web_fetch 取的是静态 HTML')" "1"
ck "L0 含'外部程序自己的运行态'判据" "$(printf '%s' "$out" | grep -c '外部程序自己的运行态')" "1"
ck "L0 不得出现浏览器能力列举(反向锁)" "$(printf '%s' "$out" | grep -c '真实浏览器\|文件:行号\|点击/输入/拖拽')" "0"
# mcp_call 的 description 是**每轮随 tools 发给 API** 的,权重比 L0 还高:上一版在这里数了
# 六项浏览器能力,于是只接 figma 的会话里,模型被告知"mcp_call = 浏览器工具",可能压根不去调它。
# 这条必须按注册后的真实 JSON 断言(在夹具里真调 tools()),grep 源码不算(§硬约定 4)。
call_desc=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1; tools | jq -r '.[]|select(.function.name==\"mcp_call\")|.function.description'")
ck "mcp_call 描述给的是通用判据(运行态 vs 静态)" "$(printf '%s' "$call_desc" | grep -c '内置工具与 web_fetch 只能取静态文本')" "1"
ck "mcp_call 描述保留三条实测协议须知"   "$(printf '%s' "$call_desc" | grep -c '超时=结果未知')" "1"
ck "mcp_call 描述授权说法与会话级授权一致" "$(printf '%s' "$call_desc" | grep -c '首次调用会请你授权一次')" "1"
# $(printf …) 赋值会吃掉末尾换行,于是第一条 server 曾直接黏在路由句末句后面
# (实测 `…别猜)。- chrome: navigate_page,…`)。常驻段里的这种黏连模型每一轮都吃一次。
# 夹具里声明了 chrome 与 node_repl 两个,但 node_repl 未在 mc.json 声明 → 被来源过滤摘掉,
# 所以这里恒为 1(上一版我按"两个 server"编期望,红得是我自己算错,不是代码错)。
ck "每个 server 各占一行(不被路由句黏住)" "$(printf '%s' "$out" | grep -c '^- ')" "1"
# 只数**清单行里**的标注,别把路由句里那句解释也算进来
ck "缺用途说明在清单行里被标出"           "$(printf '%s' "$out" | grep -c '^- .*未提供用途说明')" "1"
ck "mcp_call 描述不再数浏览器能力(反向锁)" "$(printf '%s' "$call_desc" | grep -c '渲染后的 DOM\|console 报错\|拖拽\|截图')" "0"
ck "清单含 server 名与工具名" "$(printf '%s' "$out" | grep -c 'evaluate')" "1"
again=$(printf '%s' "$out" | grep -c '可用外部能力')
out2=$(printf '%s' "$out" | grep -c 'base')
ck "原 system 文本没被吃掉" "$out2" "1"

# 幂等:注入两次只剩一段
out=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1
messages=\$(jq -n '[{role:\"system\",content:\"base\"}]')
inject_mcp; inject_mcp; inject_mcp
echo \"\$messages\" | jq -r '.[0].content' | grep -c '可用外部能力'")
ck "重复注入幂等(三次只剩一段)" "$out" "1"

# 超预算 → 折叠并指向 mcp_tools
out=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_STATE_DIR='$W/big'; MCP_EXPOSED='chrome'; mkdir -p \"\$MCP_STATE_DIR\"
python3 -c \"
import json;json.dump({'server':'chrome','tools':[{'name':'t%03d'%i,'description':'d'*20} for i in range(400)]},open('$W/big/chrome.json','w'))\"
MCP_CATALOG_MAX=2048
mcp_catalog_text")
ck "超预算降级为折叠形态" "$(printf '%s' "$out" | grep -c '清单超限已折叠')" "1"
ck "折叠时给出下一步(mcp_tools)" "$(printf '%s' "$out" | grep -q 'mcp_tools' && echo yes || echo no)" "yes"

echo "=== E mcp_call:成功 / isError / CLI 失败 / 图片 / 确认 / 审计 ==="
prep="MCP_ENABLE=1; MCP_STATE_DIR='$W/st'; MCP_EXPOSED='chrome'; mkdir -p \"\$MCP_STATE_DIR\"
printf '%s' '{\"server\":\"chrome\",\"tools\":[{\"name\":\"evaluate\",\"description\":\"x\"}]}' > \"\$MCP_STATE_DIR/chrome.json\"
MCP_MAX_CALLS=5; MCP_CALLS=0; AUDIT_LOG=''"

out=$(MOCK_OUT='{"content":[{"type":"text","text":"[1,2]"}]}' MOCK_RC=0 run "$prep
fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{\"script\":\"1+1\"}}'
tool_mcp_call >/dev/null 2>&1; printf '%s' \"\$content\"")
ck "成功结果打外部内容标记" "$(printf '%s' "$out" | grep -c '都不是用户指令')" "1"
ck "正文取到了工具返回值" "$(printf '%s' "$out" | grep -c '\[1,2\]')" "1"

out=$(MOCK_OUT='{"isError":true,"content":[{"type":"text","text":"Input validation error: script"}]}' MOCK_RC=0 run "$prep
fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{}}'
tool_mcp_call >/dev/null 2>&1; printf '%s' \"\$content\"")
ck "isError 归为参数错误(明确'已往返成功,别当网络失败重发')" "$(printf '%s' "$out" | grep -c '已经往返成功')" "1"

out=$(MOCK_OUT='boom' MOCK_RC=1 run "$prep
fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{}}'
tool_mcp_call >/dev/null 2>&1; printf '%s' \"\$content\"")
ck "CLI 失败劝阻重试" "$(printf '%s' "$out" | grep -c '别反复重发同一条')" "1"

out=$(MOCK_OUT='{"content":[{"type":"text","text":"t"},{"type":"image","data":"QUJDREVG"}]}' MOCK_RC=0 run "$prep
fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{}}'
tool_mcp_call >/dev/null 2>&1; printf '%s' \"\$content\"")
ck "图片块提示走 read_image" "$(printf '%s' "$out" | grep -c 'read_image')" "1"
ckn "base64 没混进正文" "$(printf '%s' "$out" | grep -c 'QUJDREVG')" "1"

out=$(MOCK_OUT='{"content":[{"type":"text","text":"x"}]}' MOCK_CONFIRM=1 run "$prep
auto_mode=''
fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{}}'
tool_mcp_call >/dev/null 2>&1; echo \"CONFIRMED=\${CONFIRMED:-0}\"")
ck "非 auto:首次调用会请求授权" "$(printf '%s' "$out" | grep -o 'CONFIRMED=.*')" "CONFIRMED=1"

out=$(MOCK_OUT='{"content":[{"type":"text","text":"x"}]}' run "$prep
auto_mode=sandbox
fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{}}'
tool_mcp_call >/dev/null 2>&1
tool_mcp_call >/dev/null 2>&1
echo \"CALLS=\$MCP_CALLS\"; echo \"AUDIT=[\$(grep -c '^mcp|mcp_call' \$AUDIT_FILE)]\"")
ck "auto:不弹确认,但两次都记审计" "$(printf '%s' "$out" | grep -o 'AUDIT=\[[^]]*\]')" "AUDIT=[2]"
ck "调用计数在累加(刹车的前提)" "$(printf '%s' "$out" | grep -o 'CALLS=.*')" "CALLS=2"

out=$(MOCK_OUT='{"content":[{"type":"text","text":"x"}]}' run "$prep
auto_mode=sandbox; MCP_MAX_CALLS=3; MCP_CALLS=3
fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{}}'
tool_mcp_call >/dev/null 2>&1; printf '%s' \"\$content\"")
ck "达上限即拒(显式设了上限时才生效)" "$(printf '%s' "$out" | grep -c '次数已达本会话上限')" "1"

# —— 默认必须是不限(Bruker/Blender 类 server 撞的正是这个)——
# 这条**读的是装载后的变量值**,不是 grep 源码那行常量:常量行被改坏、或 env 前缀写错
# (本文件里已踩过"$1 展开后 bash 不当赋值前缀"那个坑),这里就会红。
q0=$(run "echo \"M=\$MCP_MAX_CALLS\"")
ck "装载后的默认上限 = 0(不限)" "$q0" "M=0"
# 用同一份 prep 但**不覆盖**上限,连发 7 次:一次都不许被拒,且计数照常累加(刹车关了≠不记账)
prep_nolim="${prep/MCP_MAX_CALLS=5;/}"
out=$(MOCK_OUT='{"content":[{"type":"text","text":"x"}]}' run "$prep_nolim
auto_mode=sandbox
for i in 1 2 3 4 5 6 7; do
  fn_args='{\"server\":\"chrome\",\"tool\":\"evaluate\",\"args\":{}}'
  tool_mcp_call >/dev/null 2>&1
done
printf 'CALLS=%s DENIED=%s' \"\$MCP_CALLS\" \"\$(printf '%s' \"\$content\" | grep -c '次数已达')\"")
# 用 grep -o 抽取:tool_mcp_call 每次都会往 stdout 打一行"调用完成",整串相等会被它撑爆
# (上一版就是这么红的 —— 红的是我的比对式,不是判据)。
ck "默认下连发 7 次一次都不拒,计数仍累加" \
   "$(printf '%s' "$out" | grep -o 'CALLS=[0-9]* DENIED=[0-9]*')" "CALLS=7 DENIED=0"
# 上限说法只有一个出处(三处共用);两形都要对,且**不许出现"上限 0 次"这种假话**
ck "有上限时的说法" "$(run "MCP_MAX_CALLS=5; mcp_quota_text" 2>/dev/null | tr -d '\n')" "单会话上限 5 次(超出会拒并提示你)"
ck "不限时的说法"   "$(run "MCP_MAX_CALLS=0; mcp_quota_text" 2>/dev/null | tr -d '\n')" "不限次数(默认;要收紧用 AUTO_MAX_MCP_CALLS=<n>)"
ck "不限时绝不出现『上限 0 次』" "$(run "MCP_MAX_CALLS=0; mcp_quota_text" 2>/dev/null | grep -c '上限 0 次')" "0"

out=$(run "$prep
fn_args='{\"server\":\"nope\",\"tool\":\"evaluate\"}'
tool_mcp_call >/dev/null 2>&1; printf '%s' \"\$content\"")
ck "未启用的 server 被拒" "$(printf '%s' "$out" | grep -c '未启用')" "1"
out=$(run "$prep
fn_args='{\"server\":\"../../etc\",\"tool\":\"evaluate\"}'
tool_mcp_call >/dev/null 2>&1; printf '%s' \"\$content\"")
ck "路径穿越型名字被拒" "$(printf '%s' "$out" | grep -c '非法 server/tool 名')" "1"

echo "=== F 无人值守那道范围闸 ==="
out=$(MOCK_OUT="$LIST_OK" run "
auto_mode=sandbox; export WCHAT_AUTO_ASSUME_YES=1
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG='$W/mc.json'
mcp_boot >/dev/null 2>&1; echo \"ENABLED=\$MCP_ENABLE\"")
ck "无人值守 + 未点名 → 不启用" "$(printf '%s' "$out" | grep -o 'ENABLED=.*')" "ENABLED=0"
out=$(MOCK_OUT="$LIST_OK" run "
auto_mode=sandbox; export WCHAT_AUTO_ASSUME_YES=1
MCP_ENABLE=1; MCP_ONLY=(chrome); MCP_CONFIG='$W/mc.json'
mcp_boot >/dev/null 2>&1; echo \"ENABLED=\$MCP_ENABLE\"")
ck "无人值守 + 点名 → 启用" "$(printf '%s' "$out" | grep -o 'ENABLED=.*')" "ENABLED=1"

echo "=== H 存活收尾与单会话锁 ==="
# 夹具形状:每个用例是一段 quoted heredoc 落到 $W/<name>.sh,执行时路径全靠 env 传入。
# 不用 sh -c / bash -c 套字符串:三层引号下 $(( )) 会在"写文件时"就求值(本轮实测踩过 4 次,
# 每次现象都像"被测代码坏了",其实都是夹具自己)。
# 空壳启动器:用例若漏桩 mcp_cli,真实调用只会打到这里(输出为空 → boot 报"清单探测失败" → 断言红),
# 从而**物理上无法联网**。用绝对路径,不依赖 PATH(上一版用 /bin/true 就被 PATH 裁掉过)。
printf '#!/bin/sh\nexit 0\n' > "$W/noop-launcher"; chmod +x "$W/noop-launcher"
export NOOP_LAUNCHER="$W/noop-launcher"
for _c in lock foreign owned trunc; do mkdir -p "$W/h-$_c/home" "$W/h-$_c/tmp" "$W/h-$_c/clip"; done

cat > "$W/prelude.sh" <<'PEOF'
set +u
_mcp_load() { _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }
_mcp_load
mkdir -p "$CLIP_DIR" 2>/dev/null
STOPED=0
confirm_cmd() { return 0; }
# ⚠ 这里必须桩掉 mcp_cli:上一版 prelude 只桩了 confirm_cmd,结果 clipwipe 用例
#   **静默调用了真 npx 与真 daemon**,一个号称离线的闸门悄悄联网还没人发现。
#   闸门里"没桩到"必须是响的,不能是静默走网络:所有用例的 MCP_LAUNCHER 被换成 /bin/true,
#   漏桩时只会"空跑 → boot 报清单探测失败 → 断言红",不可能真去拉 npx 或连 daemon。
mcp_cli() {
    case "$*" in
        *'daemon stop'*) STOPED=$((STOPED+1)); return 0 ;;
        *'list --json'*) MCP_OUT="$CASE_OUT"; MCP_ERR=""; return 0 ;;
        *) MCP_OUT='{"servers":[]}'; MCP_ERR=""; return 0 ;;
    esac
}
PEOF

run_h() {   # $1=用例名
    W="$W" WCHAT_SRC="$REAL" HOME="$W/h-$1/home" TMPDIR="$W/h-$1/tmp" \
    MCP_LAUNCHER="$NOOP_LAUNCHER" \
    CLIP_DIR="$W/h-$1/clip" CASE_CFG="$W/mc.json" CASE_OUT="$LIST_OK" \
    "$BASHX" "$W/h-$1.sh" 2>&1
}

cat > "$W/h-lock.sh" <<'PEOF'
source "$W/prelude.sh"
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG="$CASE_CFG"
holder=$(cat "$W/holder.pid")
mkdir -p "$MCP_LOCK_DIR"
printf '%s\t%s\n' "$holder" 0 > "$MCP_LOCK_FILE"
mcp_boot >/dev/null 2>&1
echo "ENABLED=$MCP_ENABLE"
PEOF
echo "$$" > "$W/holder.pid"          # 活着的别的进程 pid(本测试自己)
ck "已有活会话持锁时,本次不启用 MCP" "$(run_h lock | grep -o 'ENABLED=.*')" "ENABLED=0"

cat > "$W/h-foreign.sh" <<'PEOF'
source "$W/prelude.sh"
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG="$CASE_CFG"
mcp_sock_state() { return 2; }                      # 永远连不上 = 不是本次拉起
mcp_cli() { case "$*" in *'daemon stop'*) STOPED=$((STOPED+1)); return 0;; esac; MCP_OUT="$CASE_OUT"; return 0; }
mcp_boot >/dev/null 2>&1
MCP_LOCK_KEEP=1
mcp_exit_stop >/dev/null 2>&1
echo "STOPED=$STOPED OWN=$MCP_OWN_DAEMON"
PEOF
ck "daemon 非本次拉起 → 退出不发 stop" "$(run_h foreign | grep -o 'STOPED=[0-9]*')" "STOPED=0"

cat > "$W/h-owned.sh" <<'PEOF'
source "$W/prelude.sh"
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG="$CASE_CFG"
SOCKLIVE=2
mcp_sock_state() { if [ "$SOCKLIVE" = 0 ]; then return 0; fi; return 2; }
mcp_cli() {
    case "$*" in
        *'daemon stop'*) STOPED=$((STOPED+1)); SOCKLIVE=2; return 0 ;;
        *) SOCKLIVE=0; MCP_OUT="$CASE_OUT"; return 0 ;;
    esac
}
mcp_boot
echo "OWN=$MCP_OWN_DAEMON ENABLE=$MCP_ENABLE"
MCP_LOCK_KEEP=1
mcp_exit_stop >/dev/null 2>&1
echo "STOPED=$STOPED"
PEOF
_owned=$(run_h owned)
ck "本次拉起的 daemon 退出时优雅 stop 一次" "$(printf '%s' "$_owned" | grep -o 'STOPED=[0-9]*')" "STOPED=1"
ck "归属判定为本次拉起" "$(printf '%s' "$_owned" | grep -o 'OWN=[0-9]*')" "OWN=1"
ck "该用例内 MCP 仍处于启用态(防空断言)" "$(printf '%s' "$_owned" | grep -o 'ENABLE=[0-9]*')" "ENABLE=1"

ck "收尾只走优雅 stop(函数体非注释行不含 kill)" \
   "$(awk '/^mcp_exit_stop\(\)/,/^}/' "$REAL" | grep -v '^[[:space:]]*#' | grep -c 'kill')" "0"
# 真机打穿过:会话缓存建在 CLIP_DIR 下,而 normal_line() 每轮输入开始就 rm -rf CLIP_DIR
# (那是给剪贴板图片用的)→ 交互会话里 mcp_tools/mcp_call 全报"server 未启用",
# 唯独 mcp_schema 正常(它不读缓存)。我的 -st 冒烟不经过 normal_line,所以全绿却漏掉。
ck "会话缓存目录不挂在 CLIP_DIR 下" \
   "$(awk '/MCP_STATE_DIR="\${TMPDIR/' "$REAL" | grep -c CLIP_DIR)" "0"
ck "图片落盘也不在 CLIP_DIR(否则路径下一轮就失效)" \
   "$(grep -c 'save-images "\$MCP_STATE_DIR/images"' "$REAL")" "1"
ck "EXIT 收尾会清掉会话缓存目录" \
   "$(grep -c 'mcp_exit_stop; rm -rf "\$CLIP_DIR" "\$MCP_STATE_DIR"' "$REAL")" "1"
cat > "$W/h-clipwipe.sh" <<'PEOF'
source "$W/prelude.sh"
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG="$CASE_CFG"
mcp_boot >/dev/null 2>&1
rm -rf "$CLIP_DIR"                                  # 模拟 normal_line 每轮输入的清理
fn_args='{"server":"chrome"}'
tool_mcp_tools >/dev/null 2>&1
printf '%s' "$content" | head -c 40
PEOF
_cw=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-clipwipe/home" TMPDIR="$W/h-clipwipe/tmp" CLIP_DIR="$W/h-clipwipe/clip" \
      CASE_CFG="$W/mc.json" CASE_OUT="$LIST_OK" "$BASHX" "$W/h-clipwipe.sh" 2>&1)
mkdir -p "$W/h-clipwipe/home" "$W/h-clipwipe/tmp"
printf '{"imports":[],"mcpServers":{"chrome":{"command":"npx"}}}' > "$W/mc.json"
_cw=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-clipwipe/home" TMPDIR="$W/h-clipwipe/tmp" CLIP_DIR="$W/h-clipwipe/clip" \
      CASE_CFG="$W/mc.json" CASE_OUT="$LIST_OK" "$BASHX" "$W/h-clipwipe.sh" 2>&1)
ck "抹掉 CLIP_DIR 后 mcp_tools 仍可用(行为级防回归)" \
   "$(printf '%s' "$_cw" | grep -c '未启用')" "0"

ck "跨会话锁不放 CLIP_DIR(会话私有目录等于没有锁)" \
   "$(awk '/^MCP_LOCK_DIR=/,/^MCP_LOCK_FILE=/' "$REAL" | grep -c 'CLIP_DIR')" "0"
ck "EXIT 收尾排在清理 CLIP_DIR 之前" \
   "$(grep -c 'mcp_exit_stop; rm -rf "$CLIP_DIR"' "$REAL")" "1"

mkdir -p "$W/h-reg/home" "$W/h-reg/tmp" "$W/h-reg/clip"
cat > "$W/h-reg.sh" <<'PEOF'
set +u
_mcp_load() { _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }
_mcp_load
missing=""
for f in mcp_cli mcp_extract_json mcp_sock_state mcp_lock_prune mcp_lock_claim \
         mcp_lock_release mcp_exit_stop mcp_boot inject_mcp mcp_catalog_text \
         tool_mcp_tools tool_mcp_schema tool_mcp_call; do
    declare -F "$f" >/dev/null 2>&1 || missing="$missing $f"
done
echo "MISSING[$missing]"
PEOF
# 这条锁是本轮一个真实事故的固化:函数体里一处**未闭合的双引号**让 bash 把后面两个函数
# 定义吞进了字符串里 —— `bash -n` 照样通过、调用处只留下一行 "command not found",
# 而 mcp_boot 继续往下跑(锁没建、独占没生效却"看起来成功了")。
# 只靠语法检查抓不到它,所以按名字逐个验注册。
ck "MCP 全部函数 source 后即注册(防未闭合引号把定义吞进字符串)" \
   "$(run_h reg | grep -o 'MISSING\[[^]]*\]')" "MISSING[]"

echo "=== I 结果侧截断必须自带出路 ==="
python3 -c "print('x'*20000)" > "$W/long.txt"
printf '{"content":[{"type":"text","text":"%s"}]}' "$(cat "$W/long.txt")" > "$W/long.json"
cat > "$W/h-trunc.sh" <<'PEOF'
source "$W/prelude.sh"
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG="$CASE_CFG"
MCP_STATE_DIR="$CLIP_DIR/mcp"; MCP_EXPOSED=chrome; mkdir -p "$MCP_STATE_DIR"
printf '%s' '{"server":"chrome","tools":[{"name":"evaluate","description":"x"}]}' > "$MCP_STATE_DIR/chrome.json"
MCP_MAX_CALLS=9; MCP_CALLS=0; auto_mode=sandbox
mcp_cli() { MCP_OUT="$(cat "$W/long.json")"; return 0; }
fn_args='{"server":"chrome","tool":"evaluate","args":{}}'
tool_mcp_call >/dev/null 2>&1
printf '%s' "$content"
PEOF
run_h trunc > "$W/trunc.out" 2>&1
ck "超长结果标注被截断"        "$(grep -c '被截断了' "$W/trunc.out")" "1"
ck "给出'别把半截当完整'的警告" "$(grep -c '别把半截当完整' "$W/trunc.out")" "1"
ck "截断时给出可执行收窄路径"  "$(grep -c 'mcp_schema' "$W/trunc.out")" "1"


# 剥掉控制序列与结尾换行:装载口注册的 EXIT trap 会恢复终端(打 \033[?25h\033[?7h),
# 只影响"整值相等"的断言;不改生产 trap,在夹具侧收拾。
_strip_ctl() { sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | tr -d '\n\r'; }
# ⚠ 行结构类断言**必须用这个**而不是 _strip_ctl:_strip_ctl 里的 tr -d '\n' 会把整个输出
#   压成一行,"^  · 提醒" 这种行首锚定就永远 0 命中 —— 现象是"断言说没这行,人眼看明明有"(刚踩过)。
_strip_ansi() { sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g'; }

echo "=== J 通用暴露面(§5.4.2:wchat 不解释第三方参数) ==="
# 这一段以前测的是"体检判据"(按包名匹配、逐个 flag 子串比对)。那套判据已整体删除,
# 所以这里的断言**换了对象**:不再测"我们能不能认出参数没加固",而测
#   ① 我们不因参数做任何区别对待(没有判据,也就没有判据腐烂);
#   ② 该给人看的事实(实际命令行、归属、缺用途说明)有没有如实说出来。
mkdir -p "$W/h-surf/home" "$W/h-surf/tmp" "$W/h-surf/clip"
# 字段形状照 2026-10-01 实测抄:失败者带 status=offline + error + issue.rawMessage + durationMs,
# 顶层带 counts。慢失败(30884ms/Timeout)与秒失败(70ms/Connection closed)**必须同时在场**,
# 因为"耗时"是把"配置写错"和"冷启动再来一次"分开的唯一证据。
printf '{"mode":"list","counts":{"ok":2,"auth":0,"offline":3,"http":0,"error":0},"servers":[%s,%s,%s,%s,%s]}' \
 '{"name":"tuned","description":"有用途说明的 server","status":"ok","durationMs":812,"transport":"STDIO npx -y some-mcp --redact-on","tools":[{"name":"alpha"}]}' \
 '{"name":"bare","status":"ok","durationMs":300,"transport":"STDIO node ./plain-server.js --verbose","tools":[{"name":"beta"},{"name":"gamma"}]}' \
 '{"name":"other","description":"另一种用途","status":"ok","durationMs":400,"transport":"STDIO uvx other-mcp","tools":[{"name":"do"}]}' \
 '{"name":"slow","status":"offline","durationMs":30884,"error":"Timeout","issue":{"kind":"offline","rawMessage":"Timeout"},"transport":"STDIO uvx slow-mcp","tools":[]}' \
 '{"name":"dead","status":"offline","durationMs":70,"error":"Connection closed","issue":{"kind":"offline","rawMessage":"Connection closed"},"transport":"STDIO node /nope.js","tools":[]}' \
 > "$W/surf_list.json"
# 配置里**多声明两个上游没报回来的**:ghost(缺席)与 -bad(名字不合法)—— 两类都是原来会静默的
printf '{"imports":[],"mcpServers":{"tuned":{"command":"npx"},"bare":{"command":"node"},"other":{"command":"uvx"},"slow":{"command":"uvx"},"dead":{"command":"node"},"ghost":{"command":"uvx"},"-bad":{"command":"npx"}}}' > "$W/surf_cfg.json"

cat > "$W/surf_prelude.sh" <<'PEOF'
set +u
_mcp_load() { _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }
_mcp_load
mkdir -p "$CLIP_DIR" 2>/dev/null
mcp_cli() { case "$*" in *'daemon stop'*) return 0;; *) MCP_OUT="$(cat "$SURF_JSON")"; return 0;; esac; }
mcp_sock_state() { return 2; }
mcp_lock_claim() { return 0; }
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG="$CASE_CFG"
PEOF
cat > "$W/h-surf.sh" <<'PEOF'
W="$W" source "$W/surf_prelude.sh"
mcp_boot 2>&1 >/dev/null
echo "EXPOSED=[$(printf '%s' "$MCP_EXPOSED" | tr '\n' ' ')]"
PEOF
_sfx=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-surf/home" TMPDIR="$W/h-surf/tmp" CLIP_DIR="$W/h-surf/clip" \
       SURF_JSON="$W/surf_list.json" CASE_CFG="$W/surf_cfg.json" "$BASHX" "$W/h-surf.sh" 2>&1 | _strip_ansi)
# 参数带不带"安全开关"不该影响暴露面 —— 我们不做那层判断了
ck "三个 server 全部暴露(不因参数差异区别对待)" \
   "$(printf '%s' "$_sfx" | tr '\n' ' ' | grep -o 'EXPOSED=\[[^]]*\]')" "EXPOSED=[tuned bare other]"
ck "实际命令行原样摊出(transport 逐字可见)" \
   "$(printf '%s' "$_sfx" | grep -c 'STDIO node ./plain-server.js --verbose')" "1"
ck "工具数如实来自 server(不是我们编的表)" \
   "$(printf '%s' "$_sfx" | grep -c 'bare · 2 个工具')" "1"
ck "缺用途说明的 server 被点名"        "$(printf '%s' "$_sfx" | grep -c 'bare 没有 description')" "1"
ck "写了用途说明的不再啰嗦"            "$(printf '%s' "$_sfx" | grep -c 'tuned 没有 description')" "0"
ck "通用提醒只发一次(不随 server 刷屏)" \
   "$(printf '%s' "$_sfx" | grep -c 'MCP 结果会进对话上下文并发到 API 端')" "1"
# —— 失败必须报原因(用户报的真问题:配了 3 个只报 1 个,剩下两个静默) ——
ck "慢失败报原因与耗时"   "$(printf '%s' "$_sfx" | grep -c 'slow · status=offline · 原因(上游原文): Timeout · 耗时 30884ms')" "1"
ck "秒失败报原因与耗时"   "$(printf '%s' "$_sfx" | grep -c 'dead · status=offline · 原因(上游原文): Connection closed · 耗时 70ms')" "1"
ck "声明了而上游没报回来" "$(printf '%s' "$_sfx" | grep -c 'ghost · 上游清单里')" "1"
ck "名字不合法要说成我们主动排除(不误报成上游故障)" \
   "$(printf '%s' "$_sfx" | grep -c '\-bad · 名字不合法,我们主动排除')" "1"
ck "失败者仍然把命令行摊出来" "$(printf '%s' "$_sfx" | grep -c '命令行: STDIO node /nope.js')" "1"
ck "汇总行引用上游 counts(不是我们自己数的判据)" "$(printf '%s' "$_sfx" | grep -c '上游 counts: ok=2')" "1"
ck "成功与失败计数如实:7 声明 / 3 可用 / 4 未启用" \
   "$(printf '%s' "$_sfx" | grep -o '共声明 [0-9]* 个:可用 [0-9]*,未启用 [0-9]*')" "共声明 7 个:可用 3,未启用 4"
# 点名点到不存在的名字:原来 `(( want )) || continue` 完全无声
_pn=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-surf/home" TMPDIR="$W/h-surf/tmp" CLIP_DIR="$W/h-surf/clip" \
      SURF_JSON="$W/surf_list.json" CASE_CFG="$W/surf_cfg.json" "$BASHX" -c '
set +u
_mcp_load(){ _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }; _mcp_load
MCP_STATE_DIR="'"$W"'/h-surf/state"; MCP_EXPOSED="tuned"; MCP_ONLY=(tuned blendor)
MCP_CONFIG="$CASE_CFG"; mkdir -p "$MCP_STATE_DIR"
mcp_show_surface "$SURF_JSON"' 2>&1)
ck "点名打错要明说配置里没有(不再伪装成 server 坏了)" \
   "$(printf '%s' "$_pn" | grep -c '点名 blendor:配置里')" "1"
# —— 行结构:两段文本喂一个 printf 会被当"格式+参数",现象是少换行、与下一行黏住 ——
ck "提示行不与前一行黏连(每行独立)" \
   "$(printf '%s\n' "$_sfx" | grep -c '^  \[mcp\] 本次共声明')" "1"
ck "提醒行独立成行" "$(printf '%s\n' "$_sfx" | grep -c '^    · 提醒:MCP 结果')" "1"
ck "耗时说明独立成行" "$(printf '%s\n' "$_sfx" | grep -c '^    · 耗时长的失败')" "1"
# —— 只给人看:失败原因**不许**进模型可见面(诱导它去修 server,而那层它无权限也不该碰)——
_mdl=$(MOCK_OUT="$(cat "$W/surf_list.json")" run "
MCP_ENABLE=1; MCP_CONFIG='$W/surf_cfg.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1
messages=\$(jq -n '[{role:\"system\",content:\"base\"}]'); inject_skills >/dev/null 2>&1 || true
inject_mcp; printf '%s' \"\$messages\" | jq -r '.[0].content'; tools | jq -c .")
ck "模型看到的 system 段里没有失败原因" "$(printf '%s' "$_mdl" | grep -c 'status=offline\|原因(上游原文)\|Connection closed')" "0"
ck "模型看到的 tools 里也没有"          "$(printf '%s' "$_mdl" | grep -c '上游 counts')" "0"
# 判据词汇必须彻底消失 —— 否则"删了体检"只是删了函数名,文案还在替第三方解释参数
ck "不再出现任何参数判据话术" \
   "$(printf '%s' "$_sfx" | grep -c '未设\|建议加\|修法\|不达标\|达标')" "0"
# 静态锁:本文件里不许再有第三方专名/flag 字面量(与闸门"清单逐字来自 tools/list"同族)
ck "源码零第三方 server 专名"          "$(grep -ci 'chrome\|playwright\|puppeteer' "$REAL")" "0"
ck "源码零第三方 flag 字面量"          "$(grep -c 'redactNetworkHeaders\|performanceCrux\|usageStatistics\|allowedUrlPattern' "$REAL")" "0"

# 残留进程自查串必须从**用户配置**生成:上一版在这里写死了一个具体包名,换 server 就提示不到东西
_pg=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-surf/home" TMPDIR="$W/h-surf/tmp" CLIP_DIR="$W/h-surf/clip" \
     CASE_CFG="$W/surf_cfg.json" "$BASHX" -c '
set +u
_mcp_load(){ _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }; _mcp_load
MCP_CONFIG="$CASE_CFG"; mcp_pgrep_pattern' 2>/dev/null | _strip_ctl)
ck "mcp_pgrep_pattern 给出配置里的三个命令" "$_pg" "node|npx|uvx"
_pg2=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-surf/home" TMPDIR="$W/h-surf/tmp" CLIP_DIR="$W/h-surf/clip" \
      "$BASHX" -c '
set +u
_mcp_load(){ _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }; _mcp_load
MCP_CONFIG="/nonexistent/mcp.json"; mcp_pgrep_pattern' 2>/dev/null | _strip_ctl)
ck "配置缺失时退回启动器名(提示永不空)" "$(printf '%s' "$_pg2" | grep -c mcporter)" "1"

# 探测把 daemon 带起来**之后**才决定禁用的那条路径,收尾责任不能跟着消失。
# 上一版靠 MCP_STRICT_CONFIG 触发它;判据删除后改用真实存在的形状:全部 server 无工具 → 暴露面为空。
cat > "$W/h-surf-empty.sh" <<'PEOF'
set +u
_mcp_load() { _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }
_mcp_load
mkdir -p "$CLIP_DIR" 2>/dev/null
STOPED=0; SOCKLIVE=2
mcp_sock_state() { if [ "$SOCKLIVE" = 0 ]; then return 0; fi; return 2; }
mcp_cli() { case "$*" in *'daemon stop'*) STOPED=$((STOPED+1)); SOCKLIVE=2; return 0;; *) SOCKLIVE=0; MCP_OUT="$(cat "$SURF_JSON")"; return 0;; esac; }
mcp_sock_state_boot() { :; }
mcp_lock_claim() { return 0; }
MCP_ENABLE=1; MCP_ONLY=(); MCP_CONFIG="$CASE_CFG"
mcp_boot 2>/dev/null >/dev/null
echo "ENABLE=$MCP_ENABLE OWN=$MCP_OWN_DAEMON"
mcp_exit_stop >/dev/null 2>&1
echo "STOPED=$STOPED"
PEOF
printf '{"servers":[{"name":"tuned","status":"ok","transport":"STDIO npx -y some-mcp","tools":[]}]}' > "$W/surf_empty.json"
_sfe=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-surf/home" TMPDIR="$W/h-surf/tmp" CLIP_DIR="$W/h-surf/clip" \
       SURF_JSON="$W/surf_empty.json" CASE_CFG="$W/surf_cfg.json" "$BASHX" "$W/h-surf-empty.sh" 2>&1)
ck "无可用工具时 MCP 确实关闭"          "$(printf '%s' "$_sfe" | grep -o 'ENABLE=[0-9]*')" "ENABLE=0"
ck "但归属仍记为我们拉起"              "$(printf '%s' "$_sfe" | grep -o 'OWN=[0-9]*')" "OWN=1"
ck "晚到的禁用不免除收尾责任(仍 stop 一次)" "$(printf '%s' "$_sfe" | grep -o 'STOPED=[0-9]*')" "STOPED=1"

# 清单必须是 server 自己上报的东西**逐字出现**,不许有任何硬编码的工具名表。
# 防的是"以后图省事写死一份常用名单" —— 那会让 wchat 报的能力与 server 真实能力**静默分叉**
# (模型只会以为某些工具不存在)。用文件式夹具:上一版在这里玩嵌套引号,当场把自己测红了。
mkdir -p "$W/h-faith/home" "$W/h-faith/tmp" "$W/h-faith/clip"
cat > "$W/h-faith.sh" <<'PEOF'
set +u
_mcp_load() { _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }
_mcp_load
MCP_STATE_DIR="$W/h-faith/state"; MCP_EXPOSED=chrome; mkdir -p "$MCP_STATE_DIR"
printf '%s' '{"server":"chrome","desc":"","tools":[{"name":"zzz_自定义_工具","description":"d"},{"name":"only_server_has_me","description":"d"}]}' > "$MCP_STATE_DIR/chrome.json"
mcp_catalog_text
PEOF
_faith=$(W="$W" WCHAT_SRC="$REAL" HOME="$W/h-faith/home" TMPDIR="$W/h-faith/tmp" CLIP_DIR="$W/h-faith/clip" \
        MCP_LAUNCHER="$NOOP_LAUNCHER" CASE_CFG="$W/mc.json" CASE_OUT="$LIST_OK" "$BASHX" "$W/h-faith.sh" 2>&1)
ck "清单逐字含 server 上报的工具名(无硬编码表)" "$(printf '%s' "$_faith" | grep -c 'only_server_has_me')" "1"
# 常驻段的文本错一次每轮都在付:上一版 MCP_ROUTE_HINT 用 $'\n'"...\n..." 拼接,
# 双引号内的 \n 是字面两字符 → 整段糊成一行带字面 \n 的垃圾。这条钉住它不回 Come。
ck "清单文本里没有字面 backslash-n(换行是真换行)" \
   "$(printf '%s' "$_faith" | grep -c '\\n')" "0"
ck "非常规命名(含中文)也能进清单"               "$(printf '%s' "$_faith" | grep -c 'zzz_自定义_工具')" "1"

echo "=== K 会话级授权(一次 vs 逐次的这条线是本轮的决定) ==="
mkdir -p "$W/h-grant/home" "$W/h-grant/tmp" "$W/h-grant/clip"
cat > "$W/h-grant.sh" <<'PEOF'
set +u
_mcp_load() { _WCHAT_LIB_ONLY=1 source "$WCHAT_SRC" >/dev/null 2>&1; }
_mcp_load
mkdir -p "$CLIP_DIR"
ASKS=0
confirm_cmd() { ASKS=$((ASKS+1)); return ${CONFIRM_RC:-0}; }
auto_audit() { :; }
mcp_cli() { MCP_OUT='{"content":[{"type":"text","text":"ok"}]}'; MCP_ERR=""; return 0; }
MCP_ENABLE=1; MCP_STATE_DIR="$GS"; MCP_EXPOSED=chrome; mkdir -p "$MCP_STATE_DIR"
printf '%s' '{"server":"chrome","desc":"","tools":[{"name":"evaluate","description":"x"},{"name":"navigate","description":"go"}]}' > "$MCP_STATE_DIR/chrome.json"
MCP_MAX_CALLS=50; MCP_CALLS=0
[[ -n "$ASAUTO" ]] && auto_mode="$ASAUTO"
[[ -n "$ACONF" ]] && MCP_CONFIRM="$ACONF"
fn_args='{"server":"chrome","tool":"evaluate","args":{"script":"1+1"}}'
tool_mcp_call >/dev/null 2>&1
fn_args='{"server":"chrome","tool":"evaluate","args":{"script":"2+2"}}'   # 不同 args,同一 server
tool_mcp_call >/dev/null 2>&1
fn_args='{"server":"chrome","tool":"navigate","args":{"url":"http://x/"}}'  # 不同 tool,同一 server
tool_mcp_call >/dev/null 2>&1
echo "ASKS=$ASKS"
PEOF
run_grant() {   # 额外变量用 env 传:$1 展开后才变成 A=b 的词,bash **不**当赋值前缀,
                # 会把它当命令名执行(上一版三条断言全空就是这么来的) → 必须交给 env。
    env W="$W" GS="$W/h-grant/state" WCHAT_SRC="$REAL" HOME="$W/h-grant/home" \
        TMPDIR="$W/h-grant/tmp" CLIP_DIR="$W/h-grant/clip" "$@" \
        "$BASHX" "$W/h-grant.sh" 2>&1 | grep -o 'ASKS=[0-9]*'
}
ck "默认会话级:3 次调用只问 1 次"        "$(run_grant XNONE=1)"       "ASKS=1"
ck "MCP_CONFIRM=always:3 次调用问 3 次"  "$(run_grant ACONF=always)"  "ASKS=3"
ck "auto 模式:一次都不问"                "$(run_grant ASAUTO=sandbox)" "ASKS=0"
ck "被拒后不记账:仍会再问"               "$(run_grant CONFIRM_RC=1)"  "ASKS=3"
ck "输入框有 MCP 授权常驻标记"            "$(grep -c '⚡ mcp 已授权本会话' "$REAL")" "1"
# ⚠ 上面那条只是"源码里有这句话",**不构成行为保证**:把 if 条件改成恒假,字符串还在,
# 照样绿(实测变异 3 就是这么逃过的)。所以补一条真调 normal_line 的断言。
marker_line() {   # $1=MCP_GRANTED 值 [$2=上限,默认 40] → 原样吐 normal_line 的输出
    env W="$W" GS="$W/h-grant/state" WCHAT_SRC="$REAL" HOME="$W/h-grant/home" \
        TMPDIR="$W/h-grant/tmp" CLIP_DIR="$W/h-grant/clip" GR="$1" LIM="${2-40}" \
        "$BASHX" -c "
set +u
_mcp_load(){ _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }   # ⚠ 必须函数上下文里 source
_mcp_load
MCP_ENABLE=1; MCP_GRANTED=\" \$GR\"; MCP_CALLS=3; MCP_MAX_CALLS=\$LIM
MCP_STATE_DIR=''; MCP_EXPOSED=''; CLIP_DIR='$W/clip'; AUDIT_FILE='$W/audit.txt'
auto_mode=''; ask_ai(){ :; }; tput(){ :; }
normal_line 2>/dev/null
" </dev/null | sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g'
}
_ml_on=$(marker_line 'chrome')       # 有授权 + 默认那档上限
_ml_off=$(marker_line '')            # 没授权
_ml_nolimit=$(marker_line 'chrome' 0)  # 有授权但不限次数(新默认)
ck "标记真打得出来(有授权时,行为断言)"        "$(printf '%s' "$_ml_on"  | grep -c 'mcp 已授权本会话')" "1"
ck "无授权时不打标记(不误报状态)"              "$(printf '%s' "$_ml_off" | grep -c 'mcp 已授权本会话')" "0"
ck "有上限时标记带分母 3/40"                   "$(printf '%s' "$_ml_on"  | grep -c '已调用 3/40 次')" "1"
# 新默认(不限)下**不许出现 "/0"** —— 那会被读成"配额已耗尽",比不写更糟
ck "不限时不带 /0 分母"                        "$(printf '%s' "$_ml_nolimit" | grep -c '/0')" "0"
ck "不限时仍显示已调用次数(刹车关了≠不记账)"   "$(printf '%s' "$_ml_nolimit" | grep -c '已调用 3 次')" "1"

# —— 常驻标记的「同一屏不许打两遍」契约(真机:-mcp + -auto=full 启动后标记出现两遍)——
# 病根:标记打在 normal_line 开头,而主循环对"什么都没提交"的一轮(空回车 / EOF /
# Ctrl+C)直接 continue 重新提示。启动时多按的那次回车被第一个输入框当空提交吃掉,
# 于是紧接着又走一遍 normal_line → 同一行原样重打。修法:标记受 marker_shown 管,
# 只在屏幕真正往前走了一步之后重打(printContent / 命令路径复位)。
marker_line2() {   # $1=MCP_GRANTED $2=auto_mode $3=marker_shown
    env W="$W" GS="$W/h-grant/state" WCHAT_SRC="$REAL" HOME="$W/h-grant/home" \
        TMPDIR="$W/h-grant/tmp" CLIP_DIR="$W/h-grant/clip" \
        GR="$1" AM="$2" MK="$3" \
        "$BASHX" -c "
set +u
_mcp_load(){ _WCHAT_LIB_ONLY=1 source '$REAL' >/dev/null 2>&1; }   # ⚠ 必须函数上下文里 source
_mcp_load
MCP_ENABLE=1; MCP_GRANTED=\" \$GR\"; MCP_CALLS=1; MCP_MAX_CALLS=0
MCP_STATE_DIR=''; MCP_EXPOSED=''; CLIP_DIR='$W/clip'; AUDIT_FILE='$W/audit.txt'
auto_mode=\"\$AM\"; auto_approvals=0; auto_session_id=probe
marker_shown=\$MK
ask_ai(){ :; }; tput(){ :; }
normal_line 2>/dev/null
" </dev/null | sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g'
}
_m0=$(marker_line2 'chrome' 'full' 0)     # 屏幕走过一整轮后:两个标记都该在
_m1=$(marker_line2 'chrome' 'full' 1)     # 上一轮什么都没提交:一个都不许重打
ck "marker_shown=0 时 auto 标记打得出"        "$(printf '%s' "$_m0" | grep -c '⚡ auto=full')" "1"
ck "marker_shown=0 时 mcp 标记打得出"         "$(printf '%s' "$_m0" | grep -c 'mcp 已授权本会话')" "1"
ck "marker_shown=1 时不重打 auto 标记"        "$(printf '%s' "$_m1" | grep -c '⚡ auto=')" "0"
ck "marker_shown=1 时不重打 mcp 标记"         "$(printf '%s' "$_m1" | grep -c 'mcp 已授权本会话')" "0"
# 负对照:两臂必须只差在 marker_shown 上,否则上面四条红/绿说明不了任何问题
ck "负对照:两臂除 marker_shown 外内容一致" \
   "$(diff <(printf '%s\n' "$_m0" | grep -v '⚡') <(printf '%s\n' "$_m1" | grep -v '⚡') >/dev/null && echo same || echo diff)" "same"
# 复位点必须在"屏幕往前走"的那两处(少了它标记就永远只打一次,比打两遍更糟)
ck "printContent 每轮开头复位 marker_shown" \
   "$(awk '/^printContent\(\)/,/^}/' "$REAL" | grep -c 'marker_shown=0')" "1"
ck "normal_line 两条标记各自记账 marker_shown=1" \
   "$(awk '/^normal_line\(\)/,/^}/' "$REAL" | grep -c 'marker_shown=1')" "2"
ck "授权表不用 declare -A(函数内 source 会 local 化)" \
   "$(awk '/^MCP_GRANTED=/,0' "$REAL" | head -3 | grep -c 'declare -A')" "0"

echo "=== G 注册与接线(经真 tools() 与源码静态锁) ==="
off=$(MOCK_OUT="$LIST_OK" run "MCP_ENABLE=0; tools | wc -c")
on=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1; tools | wc -c")
names_on=$(MOCK_OUT="$LIST_OK" run "
MCP_ENABLE=1; MCP_CONFIG='$W/mc.json'; MCP_ONLY=()
mcp_boot >/dev/null 2>&1; tools | jq -r '[.[].function.name]|join(\",\")'")
ck "-mcp 打开时新增的正是三条通道" "$(printf '%s' "$names_on" | grep -oc 'mcp_tools,mcp_schema,mcp_call')" "1"
ckn "打开后 schema 变大(说明真注册了)" "$on" "$off"
off_names=$(run "MCP_ENABLE=0; tools | jq -r '[.[].function.name]|join(\",\")'")
ck "关闭时不含任何 mcp_ 通道" "$(printf '%s' "$off_names" | grep -c 'mcp_')" "0"
ck "分发白名单点名三条通道" "$(grep -c 'mcp_tools|mcp_schema|mcp_call)' "$REAL")" "1"
# 注册之外还得有道执行期闸门:不注册 ≠ 不能被调用(历史里出现过 mcp_* 的会话、
# 或不守注册表的网关都能把它发回来)。上一版冒烟就是靠这条才发现漏了。
ck "分发处仍有 MCP_ENABLE 执行期闸门(注册与执行两道关)" \
   "$(awk '/mcp_tools\|mcp_schema\|mcp_call\)/,/fi ;;/' "$REAL" | grep -c 'MCP_ENABLE == 1')" "1"
# W12-5:注册表归 Python(tools.py)。条件追加不再是 tools() 里的 jq,而是 build() 里一处
# 独立变换;仍钉"只有一处、且没另起注册机制"——bash 侧已无该 jq(收口形态),Python 侧仅一处。
ck "tools 的条件追加只在 Python 一处(不另起注册机制)" \
   "$(grep -c 'tools + list(mcp_tools)' "$(cd "$(dirname "$REAL")" && pwd)/wchat_py/tools.py")" "1"
ck "bash 侧已无 MCP 追加 jq(已收口)" "$(grep -c -- '--argjson m "$MCP_TOOLS_JSON"' "$REAL")" "0"
# 收口形态(W9):存盘剥离链已移入 Python(`history pack` 的 mark/mark2/mark3 三刀),
# 不再 grep 已删的 bash `split($mark3)`。改两条:①行为——pack 确实剥掉第 3 刀(MCP 槽);
# ②接线——save_history 把 $MCP_MARK 当第 3 个标记传进 pack(单一出处仍在 bash)。
_mcp_strip=$(printf '%s' '[{"role":"system","content":"head M3 tail"}]' \
    | python3 "$(dirname "$REAL")/wchat_py/_run.py" history pack m disabled M1 M2 M3 \
    | jq -r '.messages[0].content')
ck "存盘剥离链含 MCP 标记(与 SKILLS/AUTO 同一条):pack 剥第 3 刀" "$_mcp_strip" "head "
ck "save_history 把 MCP 标记传进 pack" "$(grep -c 'history pack .*"\$MCP_MARK"' "$REAL")" "1"
ck "auto 签字面板会打印 MCP 面" "$(grep -c 'MCP:      已启用' "$REAL")" "1"
ck "mcp_* 没把 content 声明成 local(靠全局回传)" \
   "$(awk '/^tool_mcp_call\(\)/,/^}/' "$REAL" | grep -c 'local content')" "0"
ck "顶层首次注入只调 inject_skills(源化时 inject_mcp 未定义)" \
   "$(grep -A6 '^messages=$(jq -n' "$REAL" | grep -c '^system_inject_all$')" "0"

echo
TOTAL=$((pass + fail))
echo "RESULT total=$TOTAL pass=$pass fail=$fail src=$(sha256sum "$SRC" 2>/dev/null | cut -c1-12)"
(( fail == 0 )) || exit 1
#!/usr/bin/env bash
# ctmodel —— 终端里切换 ~/.ct/config 生效的模型(qwen / deepseek / 以后新增的任意家)
#
# 原理(只改注释状态,不改任何值):
#   config 里每条"跟着模型走"的赋值行,行尾带一个标记 `# @model <名字>`。
#   切到 X 模型 = 标记为 X 的行去掉注释、标记为其他模型的行加注释。
#   没打标记的行(普通注释、手写说明)一律原样输出,不动顺序、不动格式。
#
# 为什么用行尾标记而不是 sed 猜:
#   WCHAT_API_KEY="sk-xxxx" 的值里没有任何厂商线索(域名/模型名都不在),按关键词猜
#   必然猜错;把 key 抄进本脚本又等于制造第二个要同步的地方 —— wchat.sh 头部注释明确
#   要求"轮换后只改 ~/.ct/config 即可"。标记打在行上,配置里只有一份真值,加第四家
#   模型也只是多写几行带标记的行,本脚本零改动。
#
# 语法约束:标记必须是**行尾**注释(值本身可以是注释)。bash 里 `VAR="x" # 注释` 合法,
#           source 结果与不带注释时逐字节一致,所以对 wchat.sh 完全透明。
#
# 兼容:bash 3.2(macOS /bin/bash 就是 3.2)+ BSD awk。不用 declare -A / ${v,,} / mapfile。
#
# 用法:
#   ctmodel                 查看当前生效的模型
#   ctmodel qwen            切到 qwen
#   ctmodel deepseek -n     只预览 diff,不落盘
#   ctmodel -l | --list     列出配置里所有可用模型
#   ctmodel check [--fix]   校验配置(语法/唯一生效行/权限),--fix 顺手修权限
#   ctmodel undo            回滚到上一次切换前的备份 config.bak
set -uo pipefail

# ---------- 配置路径:与 wchat.sh 同名变量,方便指到别处做测试 ----------
CONFIG="${CT_CONFIG_FILE:-$HOME/.ct/config}"
[[ -n "${CTMODEL_CONFIG:-}" ]] && CONFIG="$CTMODEL_CONFIG"
BAK="${CONFIG}.bak"

# 每个模型都**必须恰好有一行生效**的指令(缺一个 wchat.sh 就会带着空值发请求)
REQ_VARS="BASE_URL WCHAT_API_KEY FLASH_MODEL PRO_MODEL"

# 共享标记:`# @model *` 或 `# @model all` 表示所有模型都保持生效
# 多模型共用一行:`# @model deepseek,mimo`(逗号分隔,任一命中即生效)

# ---------- awk:一份解析代码同时服务 report / switch 两种模式 ----------
# report: 每个带标记的赋值行输出  变量名 \t 标记名 \t on|off
# switch: 按 -v target 重写注释状态,只改**状态需要翻转**的行,其余原样输出
AWK='
function ltrim(s){ sub(/^[ \t]+/,"",s); return s }
function rtrim(s){ sub(/[ \t]+$/,"",s); return s }
function trim(s){ return rtrim(ltrim(s)) }
BEGIN{ OFS="\t" }
{
  raw = $0
  p = match(raw, /#[ \t]*@model[ \t]+[A-Za-z0-9.*,_-]+/)
  if (!p) {
    if (mode == "report") {
      if (raw ~ /^[ \t]*#/) next                       # 未打标记的注释行:与切换无关
      body = ltrim(raw)
      if (body !~ /^(export[ \t]+|declare[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*=/) next
      v = body; sub(/[ \t]*=.*/, "", v); sub(/^(export|declare)[ \t]+/, "", v)
      if (index(" " REQ " ", " " v " ") > 0) print v, "(未打标记)", "on", NR
      next
    }
    print raw; next
  }

  tag  = substr(raw, p, RLENGTH)
  gsub(/[ \t]+/, " ", tag)                             # 规范化成 "# @model xxx"
  name = tag; sub(/^# @model /, "", name)
  head = rtrim(substr(raw, 1, p - 1))
  prose = trim(substr(raw, p + RLENGTH))               # 标记后面还有内容(极少见,原样挪到行尾)
  ci = ltrim(head)
  indent = substr(head, 1, length(head) - length(ci))
  commented = 0; body = ci
  if (ci ~ /^#[ \t]*/) { body = ci; sub(/^#[ \t]*/, "", body); commented = 1 }
  body = rtrim(body)

  # 标记打在非赋值行上(例如手写说明):不参与切换
  if (body !~ /^(export[ \t]+|declare[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*=/) {
    if (mode == "report") print "(非赋值行)", name, (commented ? "off" : "on"), NR
    else print raw
    next
  }
  v = body; sub(/[ \t]*=.*/, "", v); sub(/^(export|declare)[ \t]+/, "", v)

  if (mode == "report") { print v, name, (commented ? "off" : "on"), NR; next }

  want = 0
  n = split(name, parts, /,/)
  for (i = 1; i <= n; i++) {
    m = trim(parts[i])
    if (m == target || m == "*" || m == "all") want = 1
  }
  if ((want && !commented) || (!want && commented)) { print raw; next }   # 状态已符合:零改动
  if (want) out = indent body
  else      out = indent "# " body
  out = out " " tag
  if (prose != "") out = out " " prose
  print out
}
'

run_report() { awk -v mode=report -v REQ="$REQ_VARS" "$AWK" "$1"; }

# ---------- 输出辅助 ----------
c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s⚠%s %s\n' "$c_yel" "$c_off" "$*"; }
die()  { printf '%s✗%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }

mask() {   # 密钥只在终端上显示头尾,别把整串 key 打进日志
  local v="$1"
  [[ ${#v} -le 14 ]] && { printf '%s' "$v"; return; }
  printf '%s…%s' "${v:0:6}" "${v: -4}"
}

file_mode() {   # $1=文件(省略=当前配置)  权限取值:GNU 用 -c,macOS/BSD 用 -f(与 wchat.sh 同一口径)
  local f="${1:-$CONFIG}" m
  m=$(stat -c '%a' -- "$f" 2>/dev/null) || m=""
  [[ "$m" =~ ^[0-9]+$ ]] || m=$(stat -f '%Lp' "$f" 2>/dev/null) || m=""
  printf '%s' "${m:-?}"
}

# source 一遍取生效值(子 shell,只回传变量,不外泄)
load_active() {   # $1=文件
  ( set -uo pipefail
    # shellcheck disable=SC1090
    source "$1" >/dev/null 2>&1 || exit 1
    for v in $REQ_VARS; do printf '%s\x1f%s\n' "$v" "${!v:-}"; done )
}

# ---------- 校验:每个必需变量恰好一行生效 ----------
validate() {   # $1=文件 $2=要切的模型(可为空,表示只体检)
  local f="$1" want="${2:-}" rep bad="" v names n lines
  rep=$(run_report "$f")
  [[ -n "$rep" ]] || { warn "$f 里没有任何 \`# @model\` 标记,脚本无从判断归属。"; return 1; }

  # 只查"这个文件里确实出现过的"指令:整族缺失(比如你压根不用 PRO_MODEL)不算错。
  for v in $REQ_VARS; do
    n=$(printf '%s\n' "$rep" | awk -F'\t' -v v="$v" '$1==v' | grep -c . || true)
    [[ "${n:-0}" -eq 0 ]] && continue
    lines=$(printf '%s\n' "$rep" | awk -F'\t' -v v="$v" '$1==v{printf "%s=%s@第%s行 ", $2, $3, $4}')
    n=$(printf '%s\n' "$rep" | awk -F'\t' -v v="$v" '$1==v && $3=="on"' | grep -c . || true)
    names=$(printf '%s\n' "$rep" | awk -F'\t' -v v="$v" '$1==v && $3=="on"{printf "%s(第%s行) ", $2, $4}')
    if [[ "${n:-0}" -eq 0 ]]; then
      bad+="    $v:一条生效的都没有${want:+(标记为 $want 的行不存在 —— 该模型缺这一项)};现存的行: ${lines:-无}\n"
    elif [[ "${n:-0}" -gt 1 ]]; then
      bad+="    $v:同时有 $n 行生效($names)→ 后面一行会覆盖前面一行,请只留一个 @model\n"
    elif [[ "$names" == *"(未打标记)"* ]]; then
      bad+="    $v:生效行没打 \`# @model\` 标记,脚本不敢动它(${names})—— 在该行末尾补标记,或注释掉它\n"
    fi
  done

  if [[ -n "$want" ]]; then
    local hit
    hit=$(printf '%s\n' "$rep" | awk -F'\t' -v w="$want" '$3=="on" && $2==w' | grep -c . || true)
    [[ "${hit:-0}" -gt 0 ]] || bad+="    标记为 $want 的行一条也没有(配置里只有下面列出的模型)\n"
  fi

  if [[ -n "$bad" ]]; then
    printf '%s配置不自洽:%s\n' "$c_red" "$c_off" >&2
    printf '%b' "$bad" >&2
    return 1
  fi
  return 0
}

# ---------- 子命令 ----------
# 从 rep(report 输出)里取模型名集合:$2=状态过滤(on/ALL)
tag_names() {   # $1=rep 文本 $2=on|off|ALL
  awk -F'\t' -v st="$2" '
    $1=="(非赋值行)" || $2=="(未打标记)" { next }
    st!="ALL" && $3!=st { next }
    { gsub(/[ \t]/, "", $2); n=split($2, a, ","); for (i=1;i<=n;i++) if (a[i]!="" && a[i]!="*" && a[i]!="all") print a[i] }
  ' <<<"$1" | sort -u
}

model_of_active_line() {   # $1=rep $2=变量名 → 该变量当前生效行的标记名
  awk -F'\t' -v v="$2" '$1==v && $3=="on"{print $2; exit}' <<<"$1"
}

# 当前模型的判据:取各必需变量"生效行标记"集合的**交集**。
# 为什么是交集:共用行(`# @model deepseek,mimo` / `# @model *`)会同时命中多个名字,
# 按并集判永远报"混合";交集只剩真正同时满足所有必需行的那个名字。
isect() {   # $1 $2 = 空格分隔词表 → 公共词
  local w res=""
  for w in $1; do [[ " $2 " == *" $w "* ]] && res="$res $w"; done
  printf '%s' "${res# }"
}

show_status() {   # $1=文件
  local f="${1:-$CONFIG}" rep models m v t cands first detail
  [[ -f "$f" ]] || die "找不到配置文件 $f"

  m=$(file_mode "$f")
  if [[ "$m" == "600" || "$m" == "400" ]]; then
    info "${c_dim}$f${c_off}  (权限 $m)"
  else
    info "${c_dim}$f${c_off}  (权限 ${c_red}$m${c_off} ← wchat.sh 只认 600/400,现在会拒绝加载)"
  fi

  rep=$(run_report "$f")
  models=$(tag_names "$rep" ALL | tr '\n' ' ')
  printf '可用模型: %s\n' "${models:-（还没打标记）}"

  # 逐必需变量收集生效行的标记名,边收边求交集
  cands=""; first=1; detail=""
  for v in $REQ_VARS; do
    t=$(model_of_active_line "$rep" "$v")
    if [[ -z "$t" ]]; then detail+="$v=— "; continue; fi
    detail+="$v=$t "
    t=$(printf '%s' "$t" | tr ',' ' ' | tr -s ' ')
    [[ "$t" == "*" || "$t" == "all" ]] && continue        # 共享行不作判据
    if (( first )); then cands="$t"; first=0; else cands=$(isect "$cands" "$t"); fi
  done
  cands=$(printf '%s' "$cands" | tr -s ' ')

  case $(printf '%s\n' $cands | grep -c .) in
    1) printf '当前模型: %s%s%s\n' "$c_grn" "$cands" "$c_off" ;;
    0) printf '当前状态: %s混合%s —— 各必需行指向不同模型(%s)\n       重跑一次 `ctmodel <模型>` 即可拉齐\n' \
         "$c_yel" "$c_off" "$detail" ;;
    *) printf '当前状态: %s%s%s(多个模型共用同一批生效行;明细 %s)\n' "$c_grn" "$cands" "$c_off" "$detail" ;;
  esac

  # 生效值直接 source 出来(子 shell,不回显 key 全文)
  local src; src=$(load_active "$f")
  printf '\n  %-16s %-12s %s\n' 变量 生效标记 当前值
  local val tag_v
  for v in $REQ_VARS; do
    val=$(printf '%s\n' "$src" | awk -F$'\037' -v v="$v" '$1==v{print $2}')
    tag_v=$(model_of_active_line "$rep" "$v")
    [[ -z "$tag_v" && -z "$val" ]] && continue          # 配置里根本没这一项
    case "$v" in WCHAT_API_KEY|API_KEY) val=$(mask "$val") ;; esac
    printf '  %-16s %-12s %s\n' "$v" "${tag_v:-—}" "${val:-（空）}"
  done
  # 非必需但带标记的行(如 VISION_MODEL/CTX_WINDOW)也列出来,免得切换后看不到它们跟着动了
  # 注意:这里刻意不用 `case X in p) ;; *)` 的空分支写法 —— bash 3.2 在命令替换里解析
  #       空 case 分支会直接 syntax error(bash 4+ 才接受),而本脚本的 #!/usr/bin/env bash
  #       在本机就落在 /bin/bash 3.2 上。
  local extra ev em
  extra=""
  while IFS=$'\t' read -r ev em; do
    [[ "$ev" == "(非赋值行)" || -z "$ev" ]] && continue
    [[ " $REQ_VARS " == *" $ev "* ]] && continue
    extra+="$ev=$em "
  done <<<"$(printf '%s\n' "$rep" | awk -F'\t' '$3=="on"{printf "%s\t%s\n", $1, $2}')"
  [[ -n "$extra" ]] && printf '  %s其他随行: %s%s\n' "$c_dim" "$extra" "$c_off"
  return 0   # 显式收尾:否则上面那条 [[ ]] 为假时函数返回 1,`ctmodel ... && x` 会被误判失败
}

cmd_list() {
  [[ -f "$CONFIG" ]] || die "找不到配置文件 $CONFIG"
  # "(非赋值行)" 记在 $1(变量名位置)、"(未打标记)" 记在 $2(标记名位置),两列都要滤 ——
  # 只滤 $2 会让说明性注释里的示例标记冒充成一个模型(实际踩过)。
  run_report "$CONFIG" \
    | awk -F'\t' '$1!="(非赋值行)" && $2!="(未打标记)" && $2!="" { gsub(/[ \t]/, "", $2); print $2 }' \
    | tr ',' '\n' | grep -vE '^[*all]+$' | grep -v '^$' | sort -u
}

cmd_check() {   # $1 可选 "--fix"
  local fix=0 badperm=""
  [[ "${1:-}" == "--fix" ]] && fix=1
  [[ -f "$CONFIG" ]] || die "找不到配置文件 $CONFIG"
  local m; m=$(file_mode "$CONFIG")
  if [[ "$m" != "600" && "$m" != "400" ]]; then
    badperm="$m"
    if (( fix )); then chmod 600 "$CONFIG" && ok "权限 $m → 600(已修,wchat.sh 要求 600/400 才肯加载)"
    else warn "权限是 $m,wchat.sh 会**拒绝加载**这个配置(它只认 600/400);修:ctmodel check --fix"; fi
  else
    ok "权限 $m"
  fi
  bash -n "$CONFIG" 2>/dev/null && ok "语法(bash -n)通过" || die "语法检查失败:$(bash -n "$CONFIG" 2>&1 | head -3)"
  if validate "$CONFIG"; then ok "每个必需变量都恰好一行生效"; else
    die "上面列出的问题会让切换后 wchat.sh 拿到空值/错值,先按提示补齐 \`# @model\` 标记"
  fi
  [[ -z "$badperm" ]] || (( fix )) || exit 1
}

cmd_undo() {
  [[ -f "$BAK" ]] || die "没有备份可回滚($BAK 不存在)"
  cp -p -- "$BAK" "$CONFIG" && ok "已回滚到上次切换前的 config($BAK)"
  show_status "$CONFIG"
}

cmd_switch() {   # $1=目标模型 $2=dry-run("1"/"0")
  local target="$1" dry="$2" tmp rep rc
  [[ -f "$CONFIG" ]] || die "找不到配置文件 $CONFIG"
  [[ -r "$CONFIG" && -w "$CONFIG" ]] || die "没有读写权限:$CONFIG"
  tmp=""   # 临时文件里是**含明文 key 的完整配置**,任何退出路径都要清掉
  cleanup_tmp() { [[ -n "${tmp:-}" && -e "$tmp" ]] && rm -f -- "$tmp"; }
  trap 'cleanup_tmp' EXIT
  trap 'cleanup_tmp; exit 130' INT TERM

  # 目标必须是配置里已有的模型名(允许大小写不敏感匹配,回显用配置里的写法)
  local known
  known=$(cmd_list)
  if [[ -z "$known" ]]; then
    info "$CONFIG 里还没有任何 \`# @model\` 标记,脚本无从判断哪行属于哪个模型。"
    info "给每条「跟着模型走」的赋值行加行尾标记即可(生效/注释不用自己维护):"
    info "  ${c_dim}BASE_URL=\"https://api.deepseek.com/chat/completions\" # @model deepseek${c_off}"
    info "  ${c_dim}# WCHAT_API_KEY=\"sk-xxx\" # @model deepseek${c_off}"
    exit 1
  fi
  printf '%s\n' "$known" | grep -qxF -- "$target" || {
    local hit
    hit=$(printf '%s\n' "$known" | grep -ixF -- "$target" | head -1 || true)
    [[ -n "$hit" ]] && target="$hit" || {
      info "${c_red}未知模型:${c_off} $target"
      info "可用: $(printf '%s\n' $known | tr '\n' ' ')"
      exit 1; }
  }

  local orig_mode; orig_mode=$(file_mode "$CONFIG")
  tmp=$(mktemp "${CONFIG}.ctmodel.XXXXXX") || die "创建临时文件失败"
  chmod 600 "$tmp" 2>/dev/null || true
  awk -v mode=switch -v target="$target" -v REQ="$REQ_VARS" "$AWK" "$CONFIG" > "$tmp"
  rc=$?
  if (( rc != 0 )); then rm -f "$tmp"; die "重写配置失败(awk 退出码 $rc)"; fi

  if ! validate "$tmp" "$target"; then
    rm -f "$tmp"
    info "未做任何改动。给配置加标记的例子(注释状态即可,切换脚本会自动开/关):"
    info "  ${c_dim}BASE_URL=\"https://api.xiaomimimo.com/v1/chat/completions\" # @model mimo${c_off}"
    info "  ${c_dim}# WCHAT_API_KEY=\"sk-xxx\" # @model mimo${c_off}"
    exit 1
  fi

  if [[ "$dry" == "1" ]]; then
    info "${c_yel}--dry-run:不落盘。以下是将要发生的改动:${c_off}"
    diff -u --label "$CONFIG(现状)" --label "$CONFIG(→ $target)" "$CONFIG" "$tmp" | sed '1,2d' >&2 || true
    rm -f "$tmp"; exit 0
  fi

  if diff -q "$CONFIG" "$tmp" >/dev/null; then
    rm -f "$tmp"; ok "已经是 $target,无需改动"; exit 0
  fi

  cp -p -- "$CONFIG" "$BAK" 2>/dev/null && info "${c_dim}旧版本已存 $BAK(ctmodel undo 可回滚)${c_off}"
  mv -f -- "$tmp" "$CONFIG" || die "替换配置失败(临时文件留在 $tmp)"
  chmod "$orig_mode" "$CONFIG" 2>/dev/null || chmod 600 "$CONFIG" 2>/dev/null || true
  ok "已切换到 $target"
  show_status "$CONFIG"
  info ""
  info "${c_yel}注意:config 只在 wchat 启动时 source 一次。${c_off}正在跑的会话仍用旧模型 —— 退出重开(或换终端)才生效;"
  info "会话内的 ${c_dim}-stp / -stf${c_off} 只切档位(flash/pro),不换厂商、不换 BASE_URL。"
}

usage() {
  cat <<'USAGE'
ctmodel —— 切换 ~/.ct/config 生效的模型

  ctmodel                 查看当前生效的模型(+各指令行来源)
  ctmodel qwen            切到 qwen:qwen 的行去注释,其他模型的行加注释
  ctmodel deepseek -n     只看 diff,不落盘
  ctmodel -l              列出配置里所有可用模型
  ctmodel check [--fix]   体检:语法 / 每个变量恰好一行生效 / 权限必须是 600
  ctmodel undo            回滚到上次切换前的 config.bak

配置文件(~/.ct/config)的写法:每条跟着模型走的赋值行,行尾加 `# @model <名字>`
  BASE_URL="https://api.deepseek.com/chat/completions"   # @model deepseek
  # BASE_URL="https://…aliyuncs.com/…"                   # @model qwen
注释掉与否**由脚本维护**,你只管加标记。以后加第四家 = 多写几行带标记的行,脚本不用改。
共享给所有模型的行写 `# @model *`;几家共用一行写 `# @model deepseek,mimo`。

环境变量:CT_CONFIG_FILE 指定别的配置文件(与 wchat.sh 同一个变量)。
USAGE
}

# ---------- 参数解析 ----------
DRY=0; ACTION=""; TARGET=""; CHECK_FIX=""
for a in "$@"; do
  case "$a" in
    -h|--help|help) ACTION="help" ;;
    -l|--list|list) ACTION="list" ;;
    -n|--dry-run)   DRY=1 ;;
    -s|--status|status) ACTION="status" ;;
    check)          ACTION="check" ;;
    --fix)          ACTION="check"; CHECK_FIX="--fix" ;;
    undo|rollback)  ACTION="undo" ;;
    -*)             die "未知参数:$a(ctmodel --help)" ;;
    *)              TARGET="$a" ;;
  esac
done

case "${ACTION:-switch}" in
  help)   usage ;;
  list)   cmd_list ;;
  status) show_status ;;
  check)  cmd_check "${CHECK_FIX:-}" ;;
  undo)   cmd_undo ;;
  switch)
    if [[ -z "$TARGET" ]]; then show_status; exit 0; fi
    cmd_switch "$TARGET" "$DRY" ;;
esac
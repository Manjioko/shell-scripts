#!/usr/bin/env bash
# 路径熔断闸门(不联网、不碰真实 ~/.ct)。
# 钉住 2026-10-02 重写的三条判据与"写成功才计数"的结构前提。
# 为什么要单独一道闸门:熔断是**行为**判据里最容易静默失效的一类 —— 键不一致、计数格式
# 变更、或有人把 record 挪回 path_access,表现都是"刹车悄悄解除"而不是报错。
# 用法:bash tests/write_fuse_test.sh [被测脚本]
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
SRC=${1:-}
[[ -n "$SRC" ]] || SRC="$(pwd)/wchat.sh"
[[ -f "$SRC" ]] || { printf '找不到被测脚本:%s\n' "$SRC"; exit 1; }
SRC_ABS=$(cd "$(dirname "$SRC")" && pwd -P)/$(basename "$SRC")
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$_b" ]] || continue
        [[ "$("$_b" -c 'echo ${BASH_VERSINFO[0]}' 2>/dev/null || echo 0)" =~ ^[5-9] ]] && exec "$_b" "$0" "$@"
    done
    printf '需要 bash5+\n'; exit 1
fi

W=$(mktemp -d "${TMPDIR:-/tmp}/wfuse.XXXXXX"); trap 'rm -rf "$W"' EXIT
mkdir -p "$W/root" "$W/auto"
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }
ck_ne() { if [[ "$2" != "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 不该等于 [%s]\n' "$1" "$3"; fi; }

# 在临时工作根里装载被测脚本(WORK_ROOT 由 source 时的 pwd -P 决定,所以先 cd 再 source)
# ⚠ body 里的 $W 是由**内层 bash** 展开的($1 的插入不会被外层再展开一次),所以 W 必须
#   显式传进内层。上一版没传 → 内层 $W 为空 → 路径变成 /root/...、mkdir 撞只读文件系统,
#   表现是"记账断言全红"这种看着像产品坏了的假信号(实测踩过,别再来一次)。
# ⚠ 同时把 W 归一成物理路径:macOS 上 $TMPDIR 是 /var/folders/...,而 resolve_path 走
#   cd+pwd -P 只会得到 /private/var/folders/... —— 同一个目录两种写法。测试要和被测代码
#   用**同一个口径**比键,否则"键一致性"这条断言测的是符号链接而不是逻辑。
load_into() {  # $1=要执行的测试函数体
    ( cd "$W/root" && _WCHAT_LIB_ONLY=1 API_KEY=x /opt/homebrew/bin/bash -c "
        W=\"\$(cd '$W' && pwd -P)\"
        source '$SRC_ABS' </dev/null >/dev/null 2>&1
        AUTO_DIR=\"\$W/auto\"; AUTO_AUDIT_LOG=\"\$W/auto/audit.log\"; AUTO_BACKUP_DIR=\"\$W/auto/backup\"
        auto_session_id='probe'; auto_mode='full'
        mkdir -p \"\$AUTO_DIR\"
        $1
    " < /dev/null )
}

echo "=== 1. 判据本体 ==="
load_into '
  L="$AUTO_DIR/writes-$auto_session_id.log"
  P="$W/root/f1.txt"
  # 上限触发
  AUTO_MAX_WRITES_PER_PATH=3
  : > "$L"; for i in 1 2 3; do printf "%s\t111 1\n" "$P" >> "$L"; done
  if auto_write_fuse "$P" 2>/dev/null; then echo "R1=allow"; else echo "R1=deny"; fi
  # 上限 0 = 关闭总量判据(不能变成"一律拒")
  # ⚠ 指纹要各不相同:相同指纹会命中 F3(冻结判据),那就变成"在验另一条规则"了
  AUTO_MAX_WRITES_PER_PATH=0
  : > "$L"; for h in 201 202 203; do printf "%s\t%s\n" "$P" "$h" >> "$L"; done
  if auto_write_fuse "$P" 2>/dev/null; then echo "R2=allow"; else echo "R2=deny"; fi
  # 非 auto 模式恒放行
  AUTO_MAX_WRITES_PER_PATH=1; auto_mode=""
  if auto_write_fuse "$P" 2>/dev/null; then echo "R3=allow"; else echo "R3=deny"; fi
' > "$W/o1.txt" 2>&1
ck "达到上限必须拒(F1)"        "$(grep -c 'R1=deny'  "$W/o1.txt")" "1"
ck "上限=0 是关闭而非全拒"      "$(grep -c 'R2=allow' "$W/o1.txt")" "1"
ck "非 auto 模式不判"          "$(grep -c 'R3=allow' "$W/o1.txt")" "1"

echo "=== 2. 形状判据(刹车该由形状踩,不由配额踩) ==="
load_into '
  L="$AUTO_DIR/writes-$auto_session_id.log"; P="$W/root/f1.txt"
  AUTO_MAX_WRITES_PER_PATH=99   # 故意抬到不可能触发,单独验形状
  # A↔B↔A↔B(指纹必须是单个无空格数值:生产侧 record 只存 cksum 的 CRC)
  : > "$L"; for h in 11 22 11 22; do printf "%s\t%s\n" "$P" "$h" >> "$L"; done
  if auto_write_fuse "$P" 2>/dev/null; then echo "OSC=allow"; else echo "OSC=deny"; fi
  # 线性推进 11 22 33 44 55 → 不该拒
  : > "$L"; for h in 11 22 33 44 55; do printf "%s\t%s\n" "$P" "$h" >> "$L"; done
  if auto_write_fuse "$P" 2>/dev/null; then echo "LIN=allow"; else echo "LIN=deny"; fi
  # 同一内容连写三次 → 拒
  : > "$L"; for h in 77 77 77; do printf "%s\t%s\n" "$P" "$h" >> "$L"; done
  if auto_write_fuse "$P" 2>/dev/null; then echo "FRZ=allow"; else echo "FRZ=deny"; fi
  # A B C A(隔得远的重复,不是来回改)→ 不该拒
  : > "$L"; for h in 11 22 33 11; do printf "%s\t%s\n" "$P" "$h" >> "$L"; done
  if auto_write_fuse "$P" 2>/dev/null; then echo "SLO=allow"; else echo "SLO=deny"; fi
  # 旧格式(无指纹列):计入次数但不参与形状判据
  : > "$L"; for i in 1 2; do printf "%s\n" "$P" >> "$L"; done
  if auto_write_fuse "$P" 2>/dev/null; then echo "LEG=allow"; else echo "LEG=deny"; fi
  n=$(awk -F"\t" -v p="$P" "\$1==p{c++} END{print c+0}" "$L"); echo "LEGCNT=$n"
' > "$W/o2.txt" 2>&1
ck "来回改 A↔B↔A↔B 必须拒(F2)"     "$(grep -c 'OSC=deny'  "$W/o2.txt")" "1"
ck "线性推进不许拒(误杀就是它在拦重构)" "$(grep -c 'LIN=allow' "$W/o2.txt")" "1"
ck "同内容连写三次必须拒(F3)"        "$(grep -c 'FRZ=deny'  "$W/o2.txt")" "1"
ck "远期重复(A B C A)不算来回改"     "$(grep -c 'SLO=allow' "$W/o2.txt")" "1"
ck "旧格式行仍被计数(升级不解除刹车)"   "$(grep -o 'LEGCNT=[0-9]*' "$W/o2.txt" | cut -d= -f2)" "2"
ck "旧格式行不触发形状判据"           "$(grep -c 'LEG=allow' "$W/o2.txt")" "1"

echo "=== 3. 键一致性(判与记必须同一个键,否则计数读成 0) ==="
load_into '
  L="$AUTO_DIR/writes-$auto_session_id.log"
  # resolve_path 靠 cd+pwd -P 归一**已存在的父目录**,所以先建目录再比键 ——
  # 否则两条路径都归一失败、各自退回不同兜底值,测的是"目录不存在"而不是键一致性(踩过)
  mkdir -p "$W/root/sub"
  a=$(resolve_path "sub/x.txt" 2>/dev/null || true); [[ -n "$a" ]] || a=$(expand_tilde "sub/x.txt")
  b=$(resolve_path "$W/root/sub/x.txt" 2>/dev/null || true); [[ -n "$b" ]] || b=$(expand_tilde "$W/root/sub/x.txt")
  echo "K1=$([[ "$a" == "$b" ]] && echo same || echo "diff:$a|$b")"
  printf "%s\t999\n" "$b" >> "$L"
  AUTO_MAX_WRITES_PER_PATH=1
  if auto_write_fuse "$a" 2>/dev/null; then echo "K2=allow"; else echo "K2=deny"; fi
' > "$W/o3.txt" 2>&1
ck "相对/绝对两种写法归一到同一个键" "$(grep -c 'K1=same' "$W/o3.txt")" "1"
ck "记一次之后按另一种写法判也必须看到(键同源)" "$(grep -c 'K2=deny' "$W/o3.txt")" "1"

echo "=== 4. 集成:失败的编辑不记账,成功的才记账 ==="
load_into '
  mkdir -p "$W/root/sub"; printf "alpha\nbeta\n" > "$W/root/sub/x.txt"
  L="$AUTO_DIR/writes-$auto_session_id.log"; : > "$L"
  AUTO_MAX_WRITES_PER_PATH=40
  # 1) old_string 不存在 → 未做任何修改 → 不该记账
  fn_args=$(jq -nc --arg p "sub/x.txt" "{path:\$p,old_string:\"NOPE\",new_string:\"z\"}")
  tool_edit_file >/dev/null 2>&1
  echo "MISSCNT=$(awk -F"\t" "END{print NR+0}" "$L")"
  # 2) old_string 出现两次 → 不唯一 → 不该记账
  printf "aa\naa\n" > "$W/root/sub/y.txt"
  fn_args=$(jq -nc --arg p "sub/y.txt" "{path:\$p,old_string:\"aa\",new_string:\"b\"}")
  tool_edit_file >/dev/null 2>&1
  echo "DUPCNT=$(awk -F"\t" "END{print NR+0}" "$L")"
  # 3) 成功一次 → 记一条且带指纹列
  fn_args=$(jq -nc --arg p "sub/x.txt" "{path:\$p,old_string:\"alpha\",new_string:\"ALPHA\"}")
  tool_edit_file >/dev/null 2>&1
  echo "OKCNT=$(awk -F"\t" "END{print NR+0}" "$L")"
  echo "OKMSG=${content:0:70}"
  echo "HASFIELD=$(awk -F"\t" "NF==2{f=1} END{print f?\"yes\":\"no\"}" "$L")"
  # 4) 真来回改 → 必须在远未触及上限(99)时被拒:证明"刹车由形状踩,不由配额踩"
  : > "$L"; r=""
  for i in 1 2 3 4 5 6 7; do
    if (( i % 2 )); then nw="V1"; else nw="V2"; fi
    cur=$(cat "$W/root/sub/x.txt")
    fn_args=$(jq -nc --arg p "sub/x.txt" --arg o "$cur" --arg n "$nw" "{path:\$p,old_string:\$o,new_string:\$n}")
    tool_edit_file >/dev/null 2>&1
    if [[ "$content" == *熔断* ]]; then r="refuse@$i"; break; fi
  done
  echo "OSC=$r"
  # 5) 同一内容反复"写" → 同样要早于上限被拒(F3)
  : > "$L"; r2=""
  for i in 1 2 3 4 5 6; do
    fn_args=$(jq -nc --arg p "sub/z.txt" --arg c "same content" "{path:\$p,content:\$c}")
    tool_write_file >/dev/null 2>&1
    [[ "$content" == *熔断* ]] && { r2="refuse@$i"; break; }
  done
  echo "FROZEN=$r2"
' > "$W/o4.txt" 2>&1
ck "old_string 未匹配:不记账"     "$(grep -c 'MISSCNT=0' "$W/o4.txt")" "1"
ck "old_string 不唯一:不记账"     "$(grep -c 'DUPCNT=0'  "$W/o4.txt")" "1"
ck "成功写入:记账 1 条"            "$(grep -c 'OKCNT=1'  "$W/o4.txt")" "1"
ck "记录含内容指纹列(形状判据有料)" "$(grep -c 'HASFIELD=yes' "$W/o4.txt")" "1"
ck "来回改在远未触及上限时被拒(F2 端到端)" \
   "$(grep -o 'OSC=refuse@[0-9]*' "$W/o4.txt" | sed 's/.*@//' | awk '{print ($1>=3 && $1<=6) ? "ok" : "bad"}')" "ok"
ck "同内容重新在远未触及上限时被拒(F3 端到端)" \
   "$(grep -o 'FROZEN=refuse@[0-9]*' "$W/o4.txt" | sed 's/.*@//' | awk '{print ($1>=3 && $1<=6) ? "ok" : "bad"}')" "ok"

echo "=== 5. 结构锁 ==="
# ⚠ 全部先剔掉注释行再数:熔断块与 path_access 的注释里**本来就要讨论** record
#   (解释"为什么不能挪回去"),不剔注释就是假红。同一口径见 compaction_pin_probe 的结构锁。
strip_comments() { sed -E 's/^[[:space:]]*#.*//'; }
pa_body=$(awk '/^path_access\(\)/{f=1} f{print} f && /^\}$/{exit}' "$SRC_ABS" | strip_comments)
fuse_body=$(awk '/^auto_write_fuse\(\)/{f=1} f{print} f && /^\}$/{exit}' "$SRC_ABS" | strip_comments)
ck "path_access 里不许出现记账调用(计数必须在写成功后)" "$(grep -c 'auto_write_fuse_record' <<<"$pa_body")" "0"
ck "auto_write_fuse 本体不许再追加日志(只判不记)" "$(grep -c '>> "\$log"' <<<"$fuse_body")" "0"
ck "两个写工具各有 record" \
   "$(strip_comments < "$SRC_ABS" | grep -c 'auto_write_fuse_record "\$resolved"')" "2"
ck "上限默认值是 40" "$(grep -oE '^AUTO_MAX_WRITES_PER_PATH="\$\{AUTO_MAX_WRITES_PER_PATH:-[0-9]+\}"' "$SRC_ABS" | grep -oE '[0-9]+\}"' | grep -oE '[0-9]+')" "40"
ck "指纹只存一个数(无空格,否则形状判据分词错位)" \
   "$(grep -c "cksum < \"\\\$abs\" 2>/dev/null | awk" "$SRC_ABS")" "1"
# 接线锁:两侧必须用**同一套两步归一**。为什么不用行为断言钉:resolve_path 对根内路径
# (含还不存在的文件)一律成功,分叉只在"真正越界 + 该路径已被用户放行"时才可能出现 ——
# 那种场景要在夹具里重建整条授权链,成本高到没人会维护。既然测不到,就退一步钉写法,
# 并在这里写清楚"这条锁覆盖什么、不覆盖什么",别假装行为测试覆盖了它。
ck "path_access 的键用两步归一(与 record 同源)" \
   "$(strip_comments <<<"$pa_body" | grep -cF '[[ -n "$npfuse" ]] || npfuse="$(expand_tilde "$p")"')" "1"
ck "越界路径不再退回原始 \$p 计数" \
   "$(strip_comments <<<"$pa_body" | grep -cF 'auto_write_fuse "${npfuse:-$p}"')" "0"

# 失败时把各段原始输出打出来。这道闸门自己就吃过这个亏:断言红、产品没错、看起来
# 像探针也没错 —— 真因是"键"的口径不一致(/var 与 /private/var)。只喊 FAIL 不给现场,
# 修的人只能靠猜,而猜的代价是连续三版假结论。
if (( fail > 0 )); then
    printf '\n\033[2m── 原始输出(排障用)──\033[0m\n'
    for _o in o1 o2 o3 o4; do
        [[ -s "$W/$_o.txt" ]] && { printf '  [%s]\n' "$_o"; sed 's/^/    /' "$W/$_o.txt"; }
    done
fi

printf '\nRESULT total=%d pass=%d fail=%d\n' "$((pass + fail))" "$pass" "$fail"
printf '结果:PASS=%d FAIL=%d\n' "$pass" "$fail"
(( fail == 0 ))
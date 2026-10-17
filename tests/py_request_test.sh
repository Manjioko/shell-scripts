#!/usr/bin/env bash
# W12-4b parity 装置:**Python 建体 vs bash/jq 建体,逐字节相同**。
# parity 期两实现并存(旧 jq 路径尚未删),所以这装置是"先对照、后收口"的对照尺。
# jq 版 make_request_body 是**参考实现**(curl 时代的生产路径,已被真实流量验过)。
#
# 三个坑(全踩过,全是"静默空输出/假红"形状,写在这里免得下次重踩):
#  1) 参考侧不能 `bash -c 'source …'`:wchat.sh 的 bash5 兜底**按 $0 判定"调用方是不是
#     脚本文件"**,不是就拒绝装载并**一字节输出都没有**(rc=0) —— 本装置被它骗了两次。
#     正解:运行时生成一个**真正的脚本文件**再执行它。
#  2) 那个脚本要用**真的 bash 5** 跑:`command -v bash` 在本机是 /bin/bash 3.2,指错目标
#     同样静默空输出。优先用当前 shell($BASH)并校验版本。
#  3) 参考侧尾部会被追加**终端转义**(退出时恢复光标/自动换行,属 A 阶段保留的终端层),
#     不是 make_request_body 的产物 —— 对照前剥掉;并单独钉"Python 侧不得含转义"。
# 比较一律**落盘 + cmp**(命令替换会吃末尾换行、还把差异藏进 [得]/[期望] 里看不到)。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
SRC="$RLDIR/wchat.sh"
PY=python3
B5="${WCHAT_BASH:-${BASH:-bash}}"
[[ "${BASH_VERSINFO[0]:-0}" -ge 5 ]] || B5="/opt/homebrew/bin/bash"   # 见坑 2
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT

# 参考侧脚本(见坑 1:必须是文件,不能 bash -c)
cat > "$W/ref.sh" <<'REF'
_WCHAT_LIB_ONLY=1 API_KEY=sk-stub
. "$1" < /dev/null > /dev/null
model="$2"; thinking="$3"; messages="$4"; no_tools=1
make_request_body
REF

bash_body() {  # <model> <thinking> <messages-json> -> stdout = jq 建的请求体
    "$B5" "$W/ref.sh" "$SRC" "$1" "$2" "$3" 2>"$W/ref.err"
}

check() {  # <名字> <model> <thinking> <messages-json> <期望 effort>
    local name="$1" mdl="$2" th="$3" msgs="$4" eff="$5"
    bash_body "$mdl" "$th" "$msgs" > "$W/bash.out"
    printf '{"model":%s,"thinking":%s,"messages":%s,"tools":[]}' \
        "$(jq -c -n --arg m "$mdl" '$m')" "$(jq -c -n --arg t "$th" '$t')" "$msgs" \
        | "$PY" "$RLDIR/wchat_py/_run.py" request body > "$W/py.out" 2>"$W/py.err"
    perl -pe 's/\e\[[0-9;?]*[A-Za-z]//g' "$W/py.out"   > "$W/py.s"
    perl -pe 's/\e\[[0-9;?]*[A-Za-z]//g' "$W/bash.out" > "$W/bash.s"
    if cmp -s "$W/py.s" "$W/bash.s"; then
        pass=$((pass+1)); printf '  ok   %s: 与 jq 逐字节相同\n' "$name"
    else
        fail=$((fail+1)); printf '  FAIL %s: 与 jq 逐字节相同(cmp 有差异)\n' "$name"
        diff "$W/py.s" "$W/bash.s" | head -8 | sed 's/^/       /'
        head -2 "$W/ref.err" | sed 's/^/       ref-err: /'
    fi
    ck "$name: Python 侧无终端转义" "$(grep -c "$(printf '\033')" "$W/py.out")" "0"
    ck "$name: 键顺序" "$(jq -r 'keys_unsorted|join(",")' "$W/py.out" 2>/dev/null)" \
                      "model,messages,thinking,reasoning_effort,stream,tools,stream_options"
    ck "$name: thinking.type 是标量" "$(jq -r '.thinking.type|type' "$W/py.out" 2>/dev/null)" "string"
    ck "$name: reasoning_effort" "$(jq -r '.reasoning_effort' "$W/py.out" 2>/dev/null)" "$eff"
}

TXT='[{"role":"system","content":"s"},{"role":"user","content":"hi"}]'
IMG='[{"role":"user","content":[{"type":"text","text":"看图"},{"type":"image_url","image_url":{"url":"data:image/png;base64,AA=="}}]}]'

check "纯文本/disabled" "m1" "disabled" "$TXT" "high"
check "纯文本/enabled"  "m1" "enabled"  "$TXT" "high"
check "带图/disabled"   "m1" "disabled" "$IMG" "none"
check "带图/enabled"    "m1" "enabled"  "$IMG" "high"

# ── 两实现并存的**临时锚点**（收口那一步要把这两条的期望反过来，见注释） ──────────
# 现在:生产仍由 jq 建体,Python 建体虽与它逐字节等价但**尚无调用方**。
# 这两个数就是"切换发生了没有"的判据:切换后应为 0 和 1(甚至把 0 那条删掉)。
# 注意:**不能**用 `grep -c 'request_body=$(make_request_body)'` 数——注释里有 3 处同款文本,
# 会数出 5 来(本轮实测踩过)。必须锚定"行首 + 真实调用形态"。
ck "生产仍走 jq 建体(切换前锚点)" "$(grep -Ec '^[[:space:]]*request_body=\$\(make_request_body\)' "$SRC")" "3"
ck "Python 建体尚无调用方(切换前锚点)" "$(grep -Fc 'py_run request body' "$SRC")" "0"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]
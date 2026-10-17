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

# ── 图片引用展开 parity（六族）：python 的 expand_refs 对照 jq 的 $_JQ_EXPAND_REFS ──
# 参考片段从 wchat.sh 里取（lib-only 装载后打印变量）。注意两点坑：
#   1) 必须用**脚本文件**跑 bash5（守卫按 $0 判"调用方是不是脚本文件"）；
#   2) 取回的片段尾部会带**终端转义**（退出时恢复光标/自动换行），不剥掉 jq 会报
#      INVALID_CHARACTER —— 这个坑本轮已踩三次，凡"用 $( ) 从 wchat.sh 取文本"都要剥。
cat > "$W/frag.sh" <<'REF'
_WCHAT_LIB_ONLY=1 API_KEY=sk-stub
. "$1" < /dev/null > /dev/null
printf '%s' "$_JQ_EXPAND_REFS"
REF
FRAG="$("$B5" "$W/frag.sh" "$SRC" 2>/dev/null | perl -pe 's/\e\[[0-9;?]*[A-Za-z]//g')"
ck "取到展开片段" "$([[ -n "$FRAG" ]] && echo y)" "y"

exp_case() {  # <名字> <messages-json> <refmap-json>
    local name="$1" msgs="$2" map="$3"
    printf '%s' "$map" > "$W/map.json"; printf '%s' "$msgs" > "$W/m.json"
    jq -c --rawfile map "$W/map.json" --arg gone_pre '<<G>>' --arg gone_hint '<<H>>' \
        '($map|fromjson) as $map | '"$FRAG" < "$W/m.json" > "$W/e.jq" 2>"$W/e.err"
    "$PY" -c 'import json,sys
sys.path.insert(0,sys.argv[1])
from wchat_py.request import expand_refs as f
print(json.dumps(f(json.load(open(sys.argv[2])),json.load(open(sys.argv[3])),"<<G>>","<<H>>"),
                 ensure_ascii=False,separators=(",",":")))' \
        "$RLDIR" "$W/m.json" "$W/map.json" > "$W/e.py" 2>"$W/e.pyerr"
    ck "展开 parity/$name" "$(perl -pe 's/\e\[[0-9;?]*[A-Za-z]//g' "$W/e.py")" "$(cat "$W/e.jq")"
}

exp_case "单图"         '[{"role":"user","content":"看图 [[IMGREF:a1]] 谢谢"}]' '{"a1":{"url":"data:1"}}'
exp_case "一条消息多图" '[{"role":"user","content":"两张 [[IMGREF:a1]] 和 [[IMGREF:a2]]"}]' '{"a1":{"url":"data:1"},"a2":{"url":"data:2"}}'
exp_case "已回收gone"   '[{"role":"user","content":"旧 [[IMGREF:g1]] 尾巴"}]'   '{"g1":{}}'
exp_case "混合活+回收"  '[{"role":"user","content":"活 [[IMGREF:a1]] 死 [[IMGREF:g1]]"}]' '{"a1":{"url":"data:1"},"g1":{}}'
exp_case "无引用"       '[{"role":"user","content":"纯文字"}]'                  '{"a1":{"url":"data:1"}}'
exp_case "非字符串content" '[{"role":"user","content":[{"type":"text","text":"x"}]}]' '{"a1":{"url":"data:1"}}'

# cmd_body 走**文件形态**的输入(messages_file + ref_map_file):切换后 bash 交的就是这个形状。
# 钉住两点:① 展开确实发生(1 张图 → image_url+text 两个部件);② **展开先于 effort 判据**
# (带图 + 关思考必须得 none —— 反过来先算 effort 就会拿到 high,qwen 会 400)。
_mf="$W/cm.json"; _rf="$W/cr.json"
printf '%s' '[{"role":"user","content":"看图 [[IMGREF:a1]]"}]' > "$_mf"
printf '%s' '{"a1":{"url":"data:1"}}' > "$_rf"
_pb=$(printf '{"model":"m","thinking":"disabled","messages_file":"%s","ref_map_file":"%s","gone_pre":"G","gone_hint":"H"}' "$_mf" "$_rf" \
      | "$PY" "$RLDIR/wchat_py/_run.py" request body 2>/dev/null)
ck "cmd_body 展开文件输入" "$(printf '%s' "$_pb" | jq -c '[.messages[0].content[].type]')" '["image_url","text"]'
ck "cmd_body 展开先于 effort 判据" "$(printf '%s' "$_pb" | jq -r '.reasoning_effort')" "none"

# ── 两实现并存的**临时锚点**（收口那一步要把这两条的期望反过来，见注释） ──────────
# 现在:生产仍由 jq 建体,Python 建体虽与它逐字节等价但**尚无调用方**。
# 这两个数就是"切换发生了没有"的判据:切换后应为 0 和 1(甚至把 0 那条删掉)。
# 注意:**不能**用 `grep -c 'request_body=$(make_request_body)'` 数——注释里有 3 处同款文本,
# 会数出 5 来(本轮实测踩过)。必须锚定"行首 + 真实调用形态"。
# ── 切换判据（W12-4b-1 已切换：jq 建体不再被调用，信封接管） ──────────────────────
# 注意:**不能**用 `grep -c 'request_body=$(make_request_body)'` 数——注释里有 3 处同款文本,
# 会数出 5 来(本轮实测踩过)。必须锚定"行首 + 真实调用形态"。
ck "jq 建体已无调用方" "$(grep -Ec '^[[:space:]]*request_body=\$\(make_request_body\)' "$SRC")" "0"
ck "信封构建接管(3 个调用点)" "$(grep -Ec '^[[:space:]]*request_body=\$\(make_request_inputs\)' "$SRC")" "3"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]
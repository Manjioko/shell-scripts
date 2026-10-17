#!/usr/bin/env bash
# 一次性探针(不进闸门):验证 python 的 expand_refs 与 jq 的 $_JQ_EXPAND_REFS **逐字节一致**。
# 参考侧取法:必须用**脚本文件**跑 bash5(wchat.sh 的 bash5 兜底按 $0 判"调用方是不是脚本文件")。
# ⚠ 状态:一次性探针,**尚未接入 run_gates**(接入前它不算"绿",只是人工验过)。
# 移植时按 py_request_test.sh 的写法包成 ck + RESULT 行,并登记 GATE_NAMES 等五组数组。

set -uo pipefail
RLDIR="$(cd "$(dirname "$0")/.." && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
P='<<已回收>>'; H='<<提示>>'

cat > "$W/frag.sh" <<'REF'
_WCHAT_LIB_ONLY=1 API_KEY=sk-stub
. "$1" < /dev/null > /dev/null
printf '%s' "$_JQ_EXPAND_REFS"
REF
FRAG="$(/opt/homebrew/bin/bash "$W/frag.sh" "$RLDIR/wchat.sh" 2>"$W/frag.err" | perl -pe 's/\e\[[0-9;?]*[A-Za-z]//g')"
if [[ -z "$FRAG" ]]; then echo "FAIL 参考侧取不到 _JQ_EXPAND_REFS"; head -3 "$W/frag.err"; exit 1; fi
echo "参考片段长度: ${#FRAG}"

case_one() {  # <名字> <messages-json> <refmap-json>
    local name="$1" msgs="$2" map="$3"
    printf '%s' "$map" > "$W/map.json"
    printf '%s' "$msgs" > "$W/msgs.json"
    jq -c --rawfile map "$W/map.json" --arg gone_pre "$P" --arg gone_hint "$H" \
        '($map|fromjson) as $map | '"$FRAG" < "$W/msgs.json" > "$W/jq.out" 2>"$W/jq.err"
    python3 -c '
import json,sys
sys.path.insert(0,sys.argv[1])
from wchat_py.request import expand_refs
msgs=json.load(open(sys.argv[2]))
print(json.dumps(expand_refs(msgs,json.load(open(sys.argv[3])),sys.argv[4],sys.argv[5]),ensure_ascii=False,separators=(",",":")))
' "$RLDIR" "$W/msgs.json" "$W/map.json" "$P" "$H" > "$W/py.out" 2>"$W/py.err"
    if cmp -s "$W/jq.out" "$W/py.out"; then echo "  ok   $name"
    else echo "  FAIL $name"; echo "    jq: $(cat "$W/jq.out")"; echo "    py: $(cat "$W/py.out")"; head -2 "$W/jq.err"; head -3 "$W/py.err"; fi
}

case_one "单图"       '[{"role":"user","content":"看图 [[IMGREF:a1]] 谢谢"}]'  '{"a1":{"url":"data:1"}}'
case_one "一条消息多图" '[{"role":"user","content":"两张 [[IMGREF:a1]] 和 [[IMGREF:a2]]"}]' '{"a1":{"url":"data:1"},"a2":{"url":"data:2"}}'
case_one "已回收(gone)" '[{"role":"user","content":"旧 [[IMGREF:g1]] 尾巴"}]'   '{"g1":{}}'
case_one "混合(活+回收)" '[{"role":"user","content":"活 [[IMGREF:a1]] 死 [[IMGREF:g1]]"}]' '{"a1":{"url":"data:1"},"g1":{}}'
case_one "无引用"      '[{"role":"user","content":"纯文字"}]'                  '{"a1":{"url":"data:1"}}'
case_one "非字符串content" '[{"role":"user","content":[{"type":"text","text":"x"}]}]' '{"a1":{"url":"data:1"}}'
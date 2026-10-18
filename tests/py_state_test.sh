#!/usr/bin/env bash
# W11：`wchat_py/state.py`（权限会话态形状）闸门。
# 它守的是**形状与语义**，因为这些值直接决定"该拦的有没有拦"：
#   · 幂等（同 mode+path 不重复）且**保持原有顺序**（顺序决定哪条先命中）；
#   · `rw ⊃ r`：写授权满足读请求，读授权**不得**满足写请求；
#   · 空列表必须是空串（不是 "\n"，否则 authz 侧会解析出一条坏行）；
#   · 坏行（无 `|`）跳过、不猜；
#   · 不做 IO 规范化（纯函数），规范化由调用方另追加一条。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }

st() { python3 - "$RLDIR" "$1" <<'PY' 2>&1 | tail -1
import sys; sys.path.insert(0, sys.argv[1])
from wchat_py.state import grant, render, has, parse
exec(sys.argv[2])
PY
}

ck "追加并保持顺序"     "$(st 'print(grant(grant("", "r","/a"), "rw","/b").replace(chr(10),";"))')" "r|/a;rw|/b"
ck "幂等(同 mode+path 不重复)" "$(st 't=grant("","r","/a");print(grant(t,"r","/a").count("|"))')" "1"
ck "同路径不同 mode 可共存" "$(st 't=grant(grant("","r","/a"),"rw","/a");print(len(parse(t)))')" "2"
ck "rw 满足读"          "$(st 't=grant("","rw","/b");print(has(t,"r","/b","r"))')" "True"
ck "r 不满足写"         "$(st 't=grant("","r","/a");print(has(t,"r","/a","rw"))')" "False"
ck "空列表是空串"       "$(st 'print(repr(render([])))')" "''"
ck "坏行跳过不猜"       "$(st 'print(parse("bad\nr|/x"))')" "[('r', '/x')]"
ck "含 | 的路径不截断"   "$(st 'print(parse("r|/a|b"))')" "[('r', '/a|b')]"

# CLI: 读 stdin 的当前状态,追加后整份输出(供 bash 直接赋回变量)
ck "CLI append 读改写"  "$(printf 'r|/a' | python3 "$RLDIR/wchat_py/_run.py" state append rw /b | tr '\n' ',')" "r|/a,rw|/b"
ck "CLI 非法 mode → rc=2" "$(printf '' | python3 "$RLDIR/wchat_py/_run.py" state append zz /x 2>/dev/null; echo $?)" "2"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]


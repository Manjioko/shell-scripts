#!/usr/bin/env bash
# W12-5：`wchat_py/tools.py`（工具注册表唯一出处）闸门。
# `tools=` 的字节会进请求体；前缀一变，缓存命中就从打折变全价，且屏幕上完全看不出来。
#  ① 无变换：bash 的 tools() 原样吐字面量（多行带缩进）⇒ 必须等于资源文件原文
#  ② provider=none：剔除 web_search（对齐 jq -c）
#  ③ MCP 追加：追加清单（对齐 jq -c）
#  ④ 组合：provider=none **且** MCP 开启时，MCP 不能被一起丢掉（两个变换必须独立）
# 对照口径：jq -c 带尾换行，而 bash 侧 `$( )` 会剥掉 ⇒ 比较时先去尾换行。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
SPEC="$RLDIR/wchat_py/tools_spec.json"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
tb() { python3 "$RLDIR/wchat_py/_run.py" tools build "$@"; }

tb x 0 "" > "$W/a.out"
ck "无变换: 与资源文件逐字节相同" "$(cmp -s "$W/a.out" "$SPEC" && echo same || echo diff)" "same"
ck "无变换: 末尾无换行" "$(tail -c 1 "$W/a.out" | od -c | head -1 | grep -c '\\n')" "0"

tb none 0 "" > "$W/b.out"
ck "provider=none: 不含 web_search" "$(jq '[.[]|select(.function.name=="web_search")]|length' "$W/b.out")" "0"
ck "provider=none: 工具数 10→9" "$(jq 'length' "$W/b.out")" "9"
jq -c 'map(select(.function.name != "web_search"))' "$SPEC" > "$W/b.ref"
ck "provider=none: 对齐 jq -c(去尾换行)" "$(python3 -c "
a=open('$W/b.ref','rb').read().rstrip(b'\n'); b=open('$W/b.out','rb').read()
print('same' if a==b else 'diff')")" "same"

printf '[{"type":"function","function":{"name":"mcp_x","parameters":{}}}]' > "$W/mcp.json"
tb x 1 "$W/mcp.json" > "$W/c.out"
ck "MCP 追加: 工具数 10→11" "$(jq 'length' "$W/c.out")" "11"
ck "MCP 追加: 最后一个就是追加的" "$(jq -r '.[-1].function.name' "$W/c.out")" "mcp_x"

tb none 1 "$W/mcp.json" > "$W/d.out"
ck "组合: none + mcp 仍有 MCP(两变换独立)" "$(jq '[.[]|select(.function.name=="mcp_x")]|length' "$W/d.out")" "1"
ck "组合: 且 web_search 已剔除" "$(jq '[.[]|select(.function.name=="web_search")]|length' "$W/d.out")" "0"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]


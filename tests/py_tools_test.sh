#!/usr/bin/env bash
# W12-5 地基闸门：`wchat_py/tools.py`（工具注册表唯一出处）。
# 三条分支都必须与**旧实现**逐字节相同——`tools=` 的字节会进请求体，前缀一变，
# 缓存命中就从打折变全价，而且屏幕上完全看不出来。
#  ① 无变换：bash 的 tools() 是**原样吐字面量**（多行带缩进）⇒ 必须等于资源文件原文
#  ② provider=none：剔除 web_search（对齐 jq -c）
#  ③ MCP 追加：追加清单（对齐 jq -c）
# 对照口径：jq -c 输出带尾换行，而 bash 侧 `$( )` 会剥掉 ⇒ 比较时先去尾换行。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
SPEC="$RLDIR/wchat_py/tools_spec.json"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT

# ① 无变换 = 资源文件**原样字节**（多行缩进也不能变）
python3 "$RLDIR/wchat_py/_run.py" tools build x 0 "" > "$W/a.out"
ck "无变换: 与资源文件逐字节相同" "$(cmp -s "$W/a.out" "$SPEC" && echo same || echo diff)" "same"
ck "无变换: 末尾无换行" "$(tail -c 1 "$W/a.out" | od -c | head -1 | grep -c '\\n')" "0"

# ② provider=none：剔除 web_search，且**其余工具一个不少**
python3 "$RLDIR/wchat_py/_run.py" tools build none 0 "" > "$W/b.out"
ck "provider=none: 不含 web_search" "$(jq '[.[]|select(.function.name=="web_search")]|length' "$W/b.out")" "0"
ck "provider=none: 工具数 10→9" "$(jq 'length' "$W/b.out")" "9"
jq -c 'map(select(.function.name != "web_search"))' "$SPEC" > "$W/b.ref"
ck "provider=none: 对齐 jq -c(去尾换行)" \
   "$(python3 -c "
a=open('$W/b.ref','rb').read().rstrip(b'\n'); b=open('$W/b.out','rb').read()
print('same' if a==b else 'diff')")" "same"

# ③ MCP 追加：清单落文件，追加到末尾
printf '[{"type":"function","function":{"name":"mcp_x","parameters":{}}}]' > "$W/mcp.json"
python3 "$RLDIR/wchat_py/_run.py" tools build x 1 "$W/mcp.json" > "$W/c.out"
ck "MCP 追加: 工具数 10→11" "$(jq 'length' "$W/c.out")" "11"
ck "MCP 追加: 最后一个就是追加的" "$(jq -r '.[-1].function.name' "$W/c.out")" "mcp_x"

# ④ 组合漏洞回归：provider=none **且** MCP 开启时，MCP **不能**被丢掉
python3 "$RLDIR/wchat_py/_run.py" tools build none 1 "$W/mcp.json" > "$W/d.out"
ck "组合: none + mcp 仍有 MCP(两个变换独立)" "$(jq -r '[.[]|select(.function.name=="mcp_x")]|length' "$W/d.out")" "1"
ck "组合: 且 web_search 已剔除" "$(jq '[.[]|select(.function.name=="web_search")]|length' "$W/d.out")" "0"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]


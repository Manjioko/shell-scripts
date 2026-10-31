#!/usr/bin/env bash
# W12-4a 适配层闸门:`wchat_py/llm.py` 的 seam 编码 / kwargs / 依赖探测 / **SDK 流式 e2e(桩网关)**。
#
# 不联网(桩网关在 127.0.0.1)。openai 须已装;未装时**明示跳过**(不假绿)。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat_ref}" || exit 2

# 依赖可能装在 user-site(随真实 HOME);py_prelude 覆写了 HOME → 显式补进 PYTHONPATH
USERSITE="$(HOME="${REAL_HOME:-$HOME}" python3 -c 'import site;print(site.getusersitepackages())' 2>/dev/null)"
[[ -n "$USERSITE" && -d "$USERSITE" ]] && export PYTHONPATH="${USERSITE}${PYTHONPATH:+:$PYTHONPATH}"
HAVE_OPENAI=0
python3 -c "import openai" >/dev/null 2>&1 && HAVE_OPENAI=1

echo "=== 依赖探测 ==="
if (( HAVE_OPENAI )); then
    ck "check → rc=0(openai 已装)" "$(python3 "$RLDIR/wchat_py/_run.py" llm check >/dev/null 2>&1; echo $?)" "0"
else
    ck "check 缺依赖 → rc=2" "$(python3 "$RLDIR/wchat_py/_run.py" llm check >/dev/null 2>&1; echo $?)" "2"
    ck "缺依赖报错提到 pip3 install openai" "$(python3 "$RLDIR/wchat_py/_run.py" llm check 2>&1 >/dev/null | grep -c 'pip3 install openai')" "1"
    echo "  NOTE ⚠ openai 未安装:跳过 SDK 流式 e2e(需 pip3 install openai)"
fi

echo "=== seam 事件编码(encode_chunk) ==="
cat > "$W/probe.py" <<'PY'
import json
from types import SimpleNamespace as N
from wchat_py import llm
def o(k, v): print("%s=%s" % (k, json.dumps(v, ensure_ascii=False)))
o("text", llm.encode_chunk(N(choices=[N(delta=N(content='hi'), finish_reason=None)], usage=None)))
o("reason_extra", llm.encode_chunk(N(choices=[N(delta=N(content=None, model_extra={'reasoning_content':'think'}), finish_reason=None)], usage=None)))
o("tool", llm.encode_chunk(N(choices=[N(delta=N(content=None, tool_calls=[N(index=0, id='c1', function=N(name='f', arguments='{}'))]), finish_reason=None)], usage=None)))
o("usage", llm.encode_chunk(N(choices=[], usage=N(prompt_tokens=3, completion_tokens=4, total_tokens=7))))
o("done", llm.encode_chunk(N(choices=[N(delta=N(content=None), finish_reason='stop')], usage=None)))
o("eff_plain", llm.build_kwargs('m', [{'role':'user','content':'x'}], [], 'disabled')['reasoning_effort'])
o("eff_img_dis", llm.build_kwargs('m', [{'role':'user','content':[{'type':'image_url'}]}], [], 'disabled')['reasoning_effort'])
o("think", llm.build_kwargs('m', [], [], 'enabled')['extra_body']['thinking']['type'])
_k = llm.build_kwargs('m', [], [], 'disabled'); o("stream", [_k['stream'], _k['stream_options']['include_usage']])
PY
PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}" python3 "$W/probe.py" > "$W/probe.out" 2>"$W/probe.err" \
    || { echo "  probe 失败:"; sed 's/^/    /' "$W/probe.err"; }
g() { sed -n "s/^$1=//p" "$W/probe.out"; }
ck "文本增量"        "$(g text)"         '[{"t": "text", "v": "hi"}]'
ck "reasoning 增量"  "$(g reason_extra)" '[{"t": "reasoning", "v": "think"}]'
ck "tool_calls 增量" "$(g tool)"         '[{"t": "tool_calls", "v": [{"index": 0, "id": "c1", "name": "f", "arguments": "{}"}]}]'
ck "usage 事件"      "$(g usage)"        '[{"t": "usage", "v": {"prompt": 3, "completion": 4, "total": 7, "cached": 0, "image": 0}}]'
ck "finish → done"   "$(g done)"         '[{"t": "done", "v": "stop"}]'
ck "纯文本 → high"   "$(g eff_plain)"    '"high"'
ck "带图+disabled → none" "$(g eff_img_dis)" '"none"'
ck "thinking → extra_body" "$(g think)"  '"enabled"'
ck "stream/usage 固定" "$(g stream)"     '[true, true]'

echo "=== SDK 流式 e2e(桩网关,不联网) ==="
if (( HAVE_OPENAI )); then
    PORT=$(( 20000 + RANDOM % 20000 ))
    cat > "$W/stub.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
PORT = int(sys.argv[1])
CHUNKS = [
    {"choices": [{"index": 0, "delta": {"content": "hi"}}]},
    {"choices": [{"index": 0, "delta": {"reasoning_content": "think"}}]},
    {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}}]},
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get('content-length') or 0); self.rfile.read(n)
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        for c in CHUNKS:
            self.wfile.write(("data: " + json.dumps(c) + "\n\n").encode()); self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
HTTPServer(('127.0.0.1', PORT), H).serve_forever()
PY
    python3 "$W/stub.py" "$PORT" & STUB=$!
    for _ in $(seq 1 40); do python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" && break; sleep 0.1; done
    printf '%s' "{\"base_url\":\"http://127.0.0.1:$PORT/v1\",\"api_key\":\"sk-test\",\"model\":\"m\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"tools\":[],\"thinking\":\"disabled\"}" \
        | PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}" python3 "$RLDIR/wchat_py/_run.py" llm stream > "$W/events.jsonl" 2> "$W/events.err"
    kill "$STUB" 2>/dev/null; wait "$STUB" 2>/dev/null
    ev() { grep "\"t\": \"$1\"" "$W/events.jsonl" | head -1 | jq -r '.v'; }
    ck "e2e 文本事件"     "$(ev text)"       "hi"
    ck "e2e reasoning 事件" "$(ev reasoning)" "think"
    ck "e2e tool_calls 事件" "$(grep '"t": "tool_calls"' "$W/events.jsonl" | jq -r '.v[0].name')" "f"
    ck "e2e done 事件"    "$(ev done)"       "stop"
    ck "e2e usage 事件"   "$(grep '"t": "usage"' "$W/events.jsonl" | jq -r '.v.total')" "7"
    ck "e2e 无 error 事件" "$(grep -c '"t": "error"' "$W/events.jsonl")" "0"
    # 凭据缺失/URL 非法:必须在 python 侧转成 {"t":"error"} 事件,而不是把 Traceback 糊到屏幕
    # (用户实跑见过 "⚠️ API 请求失败: Traceback ..." 一长串)。构造客户端阶段就抛、不联网,故可离线测。
    _cerr="$(printf '{}' | PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}" python3 "$RLDIR/wchat_py/_run.py" llm stream 2>&1)"
    ck "缺凭据时给出 error 事件" "$(printf '%s' "$_cerr" | grep -o '"t": "error"' | head -1)" '"t": "error"'
    ck "缺凭据时不糊 Traceback"  "$(printf '%s' "$_cerr" | grep -c Traceback)" "0"
    # 停滞兜底锁:必须是 httpx 的 **read** timeout(无字节进展即判死),**不是总超时** ——
    # 总超时会把健康的长流一起砍掉。curl 时代的 --speed-time/--speed-limit 等价物就是它
    # (bash 侧由 SSE_STALL_TIMEOUT 派生 export WCHAT_LLM_STALL)。
    _TT='import os;from wchat_py.llm import _build_timeout as b;t=b({});print(type(t).__name__,None if t is None else t.read)'
    ck "停滞兜底用 read timeout"     "$(PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}" WCHAT_LLM_STALL=7 python3 -c "$_TT")" "Timeout 7.0"
    ck "未设停滞变量则不传 timeout"  "$(PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}" python3 -c "$_TT")" "NoneType None"
    # TODO(W12-4b-1):`messages_file`/`no_tools` 的锁**待补** —— 我写了一版用 `$W` 落临时文件的三条,
    # 实测让本闸门**挂住不出结论行**(没定位到原因就回退了;先怀疑 `$W` 在该作用域未定义 + set -u)。
    # 这两个能力已实现(_resolve_inputs,人工验过三种形态),但**没有闸门覆盖**——下次补锁时注意:
    # 写完必须看到 RESULT 行,没结论行=失败(不敢报绿)。
else
    echo "  NOTE ⚠ 跳过(未装 openai)"
fi

echo "=== 结构锁 ==="
ck "llm.py 存在"               "$([[ -f "$RLDIR/wchat_py/llm.py" ]] && echo y)" "y"
ck "llm.py 不 shell-out"       "$(grep -cE 'subprocess|os\.system|Popen|check_output|check_call' "$RLDIR/wchat_py/llm.py")" "0"
ck "llm.py 用官方 SDK"         "$(grep -c 'from openai import OpenAI' "$RLDIR/wchat_py/llm.py")" "1"
ck "requirements 登记 openai"  "$(grep -c '^openai' "$RLDIR/requirements.txt")" "1"
# SOCKS 代理锁:只写 `httpx` 时,socks5 代理下 SDK 一构造就抛 ImportError
# ("'socksio' package is not installed"),而 llm.py 的 `except ImportError` 会把它
# 误报成"缺少 openai 依赖"——用户装了 openai 也照报,重装多少次都没用(实见)。
ck "requirements 给 httpx 带 socks 支持" "$(grep -cE '^httpx\[socks\]' "$RLDIR/requirements.txt")" "1"
ck "ImportError 不再一律当缺 openai" "$(grep -c '_has_dep(DEP)' "$RLDIR/wchat_py/llm.py")" "1"
ck "socks 缺 socksio 有专门文案" "$(grep -c '缺少 socksio 支持' "$RLDIR/wchat_py/llm.py")" "1"
ck "配置自检也对 socks 给人话" "$(grep -c '缺 socksio 支持' "$RLDIR/wchat_py/setup.py")" "1"
# W12-4c 收口锁:ask_ai 的流式请求必须走 Python(唯一活实现),旧 curl 主请求块必须已删。
# 只钉"调用点存在/旧路径不存在"这类结构事实;行为由 auto_test.sh 的 E5/E6(续发不被吞)覆盖。
ck "ask_ai 主请求走 py_run llm stream" "$(grep -c 'py_run llm stream' "$RLDIR/wchat_ref")" "1"
ck "ask_ai 旧 SSE curl 已删"           "$(grep -c -- '-sSN --connect-timeout' "$RLDIR/wchat_ref")" "0"
# 凭据接线锁:bash 侧 API_KEY 是普通 shell 变量(从不 export),子进程继承不到 ⇒
# 启动行必须**显式**注入。删掉这行会立刻在生产路报 SDK "Missing credentials"。
ck "ask_ai 显式注入凭据"               "$(grep -c 'WCHAT_API_KEY=\"\$API_KEY\"' "$RLDIR/wchat_ref")" "1"
# URL 形状锁:本项目 BASE_URL 是**完整端点**(脚本注释里列的三家地址),curl 直接 POST 它;
# 而 SDK 的 base_url 是**根**、会再拼 /chat/completions ⇒ 透传必得 404(用户实跑复现)。
_NB='from wchat_py.llm import _normalize_base_url as n; import sys; print(n(sys.argv[1]))'
ck "base_url 折掉完整端点后缀" "$(PYTHONPATH="$RLDIR" python3 -c "$_NB" 'https://api.deepseek.com/chat/completions')" "https://api.deepseek.com"
ck "base_url 兼容带前缀的端点" "$(PYTHONPATH="$RLDIR" python3 -c "$_NB" 'https://x.aliyuncs.com/compatible-mode/v1/chat/completions')" "https://x.aliyuncs.com/compatible-mode/v1"
ck "base_url 根形状原样保留"   "$(PYTHONPATH="$RLDIR" python3 -c "$_NB" 'http://127.0.0.1:9/v1')" "http://127.0.0.1:9/v1"
unset _NB
# thinking 形状锁:bash 请求体里是**对象** {"type": ...},python 侧必须先归一成字符串再包一层。
# 少了归一化就会双包 → 服务端 422 `unknown variant 'type'`(用户实跑复现),且
# "带图 + 关思考 ⇒ effort=none" 那条规则会因"对象 != 字符串"恒为假、从未生效。
_TK='import os,json;from wchat_py.llm import build_kwargs as b;v=json.loads(os.environ["TH"]);k=b("m",[],None,v);print(k["extra_body"]["thinking"]["type"],k["reasoning_effort"])'
ck "thinking 对象形状归一为字符串" "$(PYTHONPATH="$RLDIR" TH='{"type":"disabled"}' python3 -c "$_TK")" "disabled high"
ck "thinking 字符串形状原样保留"   "$(PYTHONPATH="$RLDIR" TH='"enabled"' python3 -c "$_TK")" "enabled high"
_TK2='import os,json;from wchat_py.llm import build_kwargs as b;m=[{"role":"user","content":[{"type":"image_url","image_url":{"url":"x"}}]}];print(b("m",m,None,json.loads(os.environ["TH"]))["reasoning_effort"])'
ck "带图+关思考 ⇒ effort=none"     "$(PYTHONPATH="$RLDIR" TH='{"type":"disabled"}' python3 -c "$_TK2")" "none"
unset _TK _TK2

finish
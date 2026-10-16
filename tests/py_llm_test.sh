#!/usr/bin/env bash
# W12-4a 适配层闸门:`wchat_py/llm.py` 的 seam 编码 / kwargs / 依赖探测 / **SDK 流式 e2e(桩网关)**。
#
# 不联网(桩网关在 127.0.0.1)。openai 须已装;未装时**明示跳过**(不假绿)。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/py_prelude.sh" "${1:-wchat.sh}" || exit 2

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
else
    echo "  NOTE ⚠ 跳过(未装 openai)"
fi

echo "=== 结构锁 ==="
ck "llm.py 存在"               "$([[ -f "$RLDIR/wchat_py/llm.py" ]] && echo y)" "y"
ck "llm.py 不 shell-out"       "$(grep -cE 'subprocess|os\.system|Popen|check_output|check_call' "$RLDIR/wchat_py/llm.py")" "0"
ck "llm.py 用官方 SDK"         "$(grep -c 'from openai import OpenAI' "$RLDIR/wchat_py/llm.py")" "1"
ck "requirements 登记 openai"  "$(grep -c '^openai' "$RLDIR/requirements.txt")" "1"

finish
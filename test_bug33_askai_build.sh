#!/opt/homebrew/bin/bash
# 提取自 ct.dev.sh 的真实 ask_ai,对着本地"接上后永不响应"的假服务端
# 做 ^C 打断验证。用法: build_askai_harness <源文件> <输出> <端口>
# 场景由环境变量 LEAK(1=模拟修复前 set -m 泄漏)控制。
SRC="$1" OUT="$2" PORT="$3"
{
  printf '#!/opt/homebrew/bin/bash\n# 提取自 %s 的 ask_ai 测试桩\n' "$SRC"
  cat <<EOF
PORT=$PORT
BASE_URL="http://127.0.0.1:\$PORT/v1/chat/completions"
DEEPSEEK_API_KEY=test
SSE_STALL_TIMEOUT=5
content="hello"
ai_active=0; ai_abort=0; input_aborted=0
full_response=""; raw_reasoning=""; thinking_response=""
last_prompt_tokens=0
declare -A tool_ids=() tool_names=() tool_args=()
handle_ctrl_c() { if (( ai_active )); then ai_abort=1; [[ -n "\${curl_pid:-}" ]] && kill -TERM "\$curl_pid" 2>/dev/null; else input_aborted=1; fi; }
trap handle_ctrl_c INT
retry_after_compact() { return 1; }
tool_run_and_parse() { return 0; }
stream_box_open() { :; }
stream_box_close() { :; }
stream_box_feed() { :; }
stream_box_append() { :; }
EOF
  awk '/^ask_ai\(\) \{/,/^# 把尚未渲染/' "$SRC"
  cat <<EOF

# ===== 测试主逻辑(必须在函数定义之后)=====
[[ "\$LEAK" == 1 ]] && set -m
# 假服务端:接受连接后保持沉默 60 秒(模拟服务端挂死)
python3 -c '
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", $PORT)); s.listen(1)
print("SERVER_READY", flush=True)
c, _ = s.accept()
time.sleep(60)
' &
sleep 0.7
echo "HARNESS_READY pgrp=\$(ps -o pgid= -p \$\$ | tr -d ' ')"
ask_ai '{"model":"x"}' 0
rc=\$?
echo "ASK_AI_RETURNED rc=\$rc ai_abort=\$ai_abort input_aborted=\$input_aborted"
echo "HARNESS_DONE"
EOF
} > "$OUT"
chmod +x "$OUT"
bash -n "$OUT" && echo "built $OUT (syntax ok, $(wc -l < "$OUT") lines)"

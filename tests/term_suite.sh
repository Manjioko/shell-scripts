#!/usr/bin/env bash
# 终端引擎聚合闸门：raw_mode / pty 执行 / 信号语义 / run_shell_command 生产分支 四个用例文件的 rc 全 0 才通过。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
pass=0; fail=0
for t in term_test termpty_test termpty_sig_test toolrun_test; do
    if python3 "$HERE/$t.py" </dev/null >/dev/null 2>&1; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1)); printf 'FAIL %s\n' "$t"
    fi
done
printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass + fail))" "$pass" "$fail"
#!/usr/bin/env python3
"""window_output 逐字节 parity：Python termwin.window_output vs bash 复制版(rek 环+拼装)。

覆盖：无省略 / 恰好填满 / 环绕溢出+省略 / 空输入。末行 RESULT ...。
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bashbin import bash_bin  # noqa: E402
from wchat_py.termwin import window_output  # noqa: E402

BASH = bash_bin()

REF = r'''#!/usr/bin/env bash
OUT_HEAD_LINES=$1; OUT_BUF_SIZE=$2; shift 2
total=0; out_head=""; declare -a tail_buf=(); tail_idx=0
for line in "$@"; do
  total=$((total+1))
  if (( total <= OUT_HEAD_LINES )); then out_head+="$line"$'\n'
  else tail_buf[$((tail_idx % OUT_BUF_SIZE))]="$line"; tail_idx=$((tail_idx+1)); fi
done
tail_count=0
if (( total > OUT_HEAD_LINES )); then tail_count=$((total-OUT_HEAD_LINES)); (( tail_count>OUT_BUF_SIZE )) && tail_count=$OUT_BUF_SIZE; fi
tstart=$(( (tail_idx - tail_count + OUT_BUF_SIZE) % OUT_BUF_SIZE ))
window_output="$out_head"
if (( total > OUT_HEAD_LINES + tail_count )); then window_output+=$'\n'"... (中间 $((total-OUT_HEAD_LINES-tail_count)) 行已省略) ..."$'\n'; fi
for ((ti=0; ti<tail_count; ti++)); do window_output+="${tail_buf[$(( (tstart+ti) % OUT_BUF_SIZE ))]}"$'\n'; done
printf '%s' "$window_output"
'''

cases = [
    (3, 5, []),
    (3, 5, ["L%d" % i for i in range(1, 21)]),
    (50, 300, ["L%d" % i for i in range(1, 61)]),
    (50, 30, ["L%d" % i for i in range(1, 101)]),
    (5, 5, ["L%d" % i for i in range(1, 6)]),
]

fd, path = tempfile.mkstemp(suffix=".sh")
os.write(fd, REF.encode())
os.close(fd)
os.chmod(path, 0o755)

p = f = 0
try:
    for i, (head, buf, lines) in enumerate(cases):
        want = subprocess.run([BASH, path, str(head), str(buf)] + lines,
                              capture_output=True).stdout
        got = window_output(lines, head, buf).encode()
        if want == got:
            p += 1
            print("  ok   case%d" % i)
        else:
            f += 1
            print("  FAIL case%d\n   bash=%r\n   py  =%r" % (i, want, got))
finally:
    os.unlink(path)
print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)
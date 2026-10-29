#!/usr/bin/env python3
"""paint 逐字节 parity：Python termwin.paint vs bash 里同款 printf 实现。

做法：把从 wchat.sh 复制的 paint 写进临时脚本，用若干 (drawn, partial, buf) 跑，输出以
\\x01 分隔；Python 侧对同样的输入调 termwin.paint，逐字节比对。末行 RESULT ...。
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bashbin import bash_bin  # noqa: E402
from wchat_py.termwin import paint  # noqa: E402

BASH = bash_bin()

REF = r'''#!/usr/bin/env bash
paint() {
    local drawn="$1" partial="$2"; shift 2; local -a buf=("$@"); local i
    printf '\r'
    (( drawn > 0 )) && printf '\033[%dA' "$drawn"
    for i in "${buf[@]}"; do printf '\033[2K\033[2m│\033[0m %s\r\n' "$i"; done
    printf '\033[2K'
    if [[ -n "$partial" ]]; then printf '\033[2m│\033[0m \033[33m%s\033[0m\033[?25h' "$partial"
    else printf '\033[?25l'; fi
}
paint 0 "" a b; printf '\001'
paint 2 "typing" x y; printf '\001'
paint 3 "" "中文行" "another"; printf '\001'
'''

cases = [
    (0, "", ["a", "b"]),
    (2, "typing", ["x", "y"]),
    (3, "", ["中文行", "another"]),
]

fd, path = tempfile.mkstemp(suffix=".sh")
os.write(fd, REF.encode())
os.close(fd)
os.chmod(path, 0o755)
try:
    out = subprocess.run([BASH, path], capture_output=True).stdout
finally:
    os.unlink(path)

parts = out.split(b"\x01")
p = f = 0
for i, (drawn, partial, buf) in enumerate(cases):
    want = parts[i] if i < len(parts) else b""
    got = paint(buf, partial, drawn).encode()
    if want == got:
        p += 1
        print("  ok   case%d" % i)
    else:
        f += 1
        print("  FAIL case%d\n   bash=%r\n   py  =%r" % (i, want, got))
print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)
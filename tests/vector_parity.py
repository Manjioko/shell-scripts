#!/usr/bin/env python3
"""把 tests/test_hardwall.sh 的 expect 向量灌进 Python hardwall，量真实一致率（只读，不改源码）。

用法: python3 tests/vector_parity.py
输出: 每条差异 + `VEC total=N same=N diff=N`。用于判断"Python 硬墙能否替代 bash 判据体"。
"""
import os
import re
import shlex
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from wchat_py.hardwall import rule  # noqa: E402

SRC = os.path.join(REPO, "tests/test_hardwall.sh")

rows = []
for ln in open(SRC, encoding="utf-8"):
    s = ln.strip()
    if not s.startswith("expect "):
        continue
    rest = s[len("expect "):]
    try:
        toks = shlex.split(rest, posix=True)
    except ValueError:
        continue
    if len(toks) < 3:
        continue
    want = toks[0]
    cmd = " ".join(toks[2:])
    # 期望规则: NP=放行; 其余为规则号; 有的写 'NP'
    rows.append((want, cmd))

same = diff = skip = 0
diffs = []
for want, cmd in rows:
    want = want.strip("'\"")
    try:
        r, _soft = rule(cmd, auto=want.startswith("P"), work_root="/tmp", home="/Users/x")
    except Exception as e:  # noqa: BLE001
        skip += 1
        continue
    got = r or "NP"
    if got == want:
        same += 1
    else:
        diff += 1
        if len(diffs) < 20:
            diffs.append((want, got, cmd))

for d in diffs:
    print("DIFF want=%-3s got=%-3s  %s" % (d[0], d[1], d[2][:60]))
print("VEC total=%d same=%d diff=%d skip=%d" % (len(rows), same, diff, skip))
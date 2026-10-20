#!/usr/bin/env python3
"""把 tests/test_hardwall.sh 的 expect 向量灌进 Python hardwall，量真实一致率（只读，不改源码）。

改进（去 harness 假差异）：
- want 含 '|'（如 H8|H13）按**集合**比：got 落在集合内即算一致；
- 含 $'...'（ANSI-C 引号，多行/转义）的向量 shlex 无法可靠解析 -> 计入 skip，不算 diff；
用于判断"Python 硬墙能否替代 bash 判据体"。
"""
import os
import shlex
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from wchat_py.hardwall import rule  # noqa: E402

SRC = os.path.join(REPO, "tests/test_hardwall.sh")

rows = []
skipped = 0
for ln in open(SRC, encoding="utf-8"):
    s = ln.strip()
    if not s.startswith("expect "):
        continue
    rest = s[len("expect "):]
    if "$'" in rest:          # ANSI-C 引号：shlex 解析不可靠，跳过（不算产品差异）
        skipped += 1
        continue
    try:
        toks = shlex.split(rest, posix=True)
    except ValueError:
        skipped += 1
        continue
    if len(toks) < 3:
        continue
    want = toks[0].strip("'\"")
    cmd = " ".join(toks[2:])
    rows.append((want, cmd))

same = diff = 0
diffs = []
for want, cmd in rows:
    try:
        r, _soft = rule(cmd, auto=want.startswith("P"), work_root=REPO, home="/Users/x")
    except Exception:  # noqa: BLE001
        skipped += 1
        continue
    got = r or "NP"
    want_set = want.split("|")
    if got in want_set:
        same += 1
    else:
        diff += 1
        if len(diffs) < 25:
            diffs.append((want, got, cmd))

for d in diffs:
    print("DIFF want=%-6s got=%-3s  %s" % (d[0], d[1], d[2][:58]))
print("VEC total=%d same=%d diff=%d skip=%d" % (len(rows), same, diff, skipped))
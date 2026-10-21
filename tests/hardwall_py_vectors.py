#!/usr/bin/env python3
"""硬墙判定向量 → Python hardwall（迁移落点，B §12 硬墙收口配套）。

从 tests/test_hardwall.sh 抽 `expect <期望> <说明> <命令>` 向量，逐条跑
`wchat_py.hardwall.rule`，对**冻结期望**断言（期望即 bash 当初的判定）。这是"删 bash
判据体"后替代 `test_hardwall` 判定部分的测试：判据来源从"跑 live bash 体"改为"对冻结期望"。

- `$_`/`$'...'` 动态向量静态不可判 → 计入 skip，不算 fail（与 vector_parity 同口径）。
- 末行 `RESULT total=N pass=N fail=0`，fail>0 退出码 1。
"""
import os
import shlex
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pp = os.environ.get("HW_VEC_PYPATH")
sys.path.insert(0, _pp if _pp else REPO)   # 负对照可用坏副本覆盖包路径
from wchat_py.hardwall import rule  # noqa: E402

SRC = os.path.join(REPO, "tests/test_hardwall.sh")


def main():
    rows = []
    skipped = 0
    for ln in open(SRC, encoding="utf-8"):
        s = ln.strip()
        if not s.startswith("expect "):
            continue
        rest = s[len("expect "):]
        if "$'" in rest or "$_" in rest:
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

    p = f = 0
    for want, cmd in rows:
        want_set = want.split("|")
        try:
            r, _ = rule(cmd, auto=want.startswith("P"), work_root=REPO, home="/Users/x")
        except Exception:
            r = "\x00err"
        got = r or "NP"
        if got in want_set:
            p += 1
        else:
            f += 1
            print("  FAIL want=%-6s got=%-4s %s" % (want, got, cmd[:60]))
    print("RESULT total=%d pass=%d fail=%d skip=%d" % (p + f, p, f, skipped))
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
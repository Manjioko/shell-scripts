#!/usr/bin/env python3
"""hard_deny_cmd 差分语料探针（B 阶段第 2 步，风险 1）。

- 读 tests/hdcmd_corpus.json 的命令串，逐条喂给 **bash 的 hard_deny_cmd**（只判定，绝不执行）。
- 抽取判定：hd_rule（空=放行）、HD_SOFT（降级放行标记）。
- 记录到 tests/hdcmd_baseline.tsv 作为差分 oracle。
- 用法：python3 tests/hdcmd_probe.py            # 打印 + 写基线
        python3 tests/hdcmd_probe.py --check    # 只跑不写，和现有基线比，差异即退出码 1
"""
import json, os, re, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH = os.environ.get("BASH_BIN", "/opt/homebrew/bin/bash")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # wchat.sh 退出 trap 会打 \x1b[?25h 等, 必须剥
CORPUS = os.path.join(REPO, "tests/hdcmd_corpus.json")
BASE = os.path.join(REPO, "tests/hdcmd_baseline.tsv")

FX = ('cd "$2"; _WCHAT_LIB_ONLY=1 API_KEY=sk-stub; '
      '. "$1" </dev/null >/dev/null 2>&1; '
      'hard_deny_cmd "$3"; printf "%s\\t%s" "${hd_rule:-}" "${HD_SOFT:-}"')


def verdict(cmd):
    out = subprocess.run([BASH, "-c", FX, "_", REPO + "/wchat.sh", "/tmp", cmd],
                         capture_output=True, env=dict(os.environ))
    s = out.stdout.decode("utf-8", "replace")
    s = ANSI.sub("", s).strip()
    rule, _, soft = s.partition("\t")
    return rule, soft


def main(argv):
    check = "--check" in argv
    cases = json.load(open(CORPUS, encoding="utf-8"))["cases"]
    rows = []
    for c in cases:
        rule, soft = verdict(c["cmd"])
        rows.append((c["id"], c["cat"], rule or "-", soft or "-", c["cmd"]))
        print("%-6s %-14s rule=%-4s soft=%-3s %s" % (c["id"], c["cat"], rule or "-", soft or "-", c["cmd"][:44]))

    if check and os.path.exists(BASE):
        old = []
        for ln in open(BASE, encoding="utf-8"):
            if ln.startswith("#") or not ln.strip():
                continue
            p = ln.rstrip("\n").split("\t")
            old.append((p[0], p[1], p[2], p[3]))
        new = [(r[0], r[1], r[2], r[3]) for r in rows]
        if old != new:
            for a, b in zip(old, new):
                if a != b:
                    print("DIFF %s: 旧=%s 新=%s" % (a[0], a[2:], b[2:]))
            print("基线不一致")
            return 1
        print("基线与当前一致(%d 条)" % len(new))
        # Python hard_deny_cmd 差分（rule/soft）：bash 基线 vs Python 实现
        try:
            from wchat_py import hardwall
        except Exception as e:
            print("无法导入 hardwall: %s" % e)
            return 1
        bad = 0
        for (i, cat, rl, sf, cmd) in rows:
            pr, ps = hardwall.rule(cmd)
            pr = pr or "-"
            if (pr, ps or "-") != (rl, sf):
                bad += 1
                print("PY-DIFF %s bash=(%s,%s) py=(%s,%s) %s" % (i, rl, sf, pr, ps or "-", cmd[:40]))
        if bad:
            print("硬墙差分 Python 不一致: %d/%d" % (bad, len(rows)))
            return 1
        print("硬墙差分 Python 一致(%d/%d)" % (len(rows), len(rows)))
        return 0

    with open(BASE, "w", encoding="utf-8") as f:
        f.write("# id\tcat\trule\tsoft\tcmd\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    n_deny = sum(1 for r in rows if r[2] != "-")
    print("wrote %s · 共 %d 条（拒 %d / 放行 %d）" % (BASE, len(rows), n_deny, len(rows) - n_deny))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
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
      'hard_deny_cmd "$3"; printf "###HD###%s\\t%s\\n" "${hd_rule:-}" "${HD_SOFT:-}"')
FX_AUTO = ('cd "$2"; _WCHAT_LIB_ONLY=1 API_KEY=sk-stub; '
           '. "$1" </dev/null >/dev/null 2>&1; '
           'auto_mode=full; AUTO_SHELL_PATHS=1; '
           'hard_deny_cmd "$3"; printf "###HD###%s\\t%s\\n" "${hd_rule:-}" "${HD_SOFT:-}"')


def verdict(cmd, auto=False):
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + ((":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    out = subprocess.run([BASH, "-c", FX_AUTO if auto else FX, "_",
                          os.path.join(REPO, "tests/fixtures/wchat_hardwall_oracle.sh"), "/tmp", cmd],
                         capture_output=True, env=env)
    s = ANSI.sub("", out.stdout.decode("utf-8", "replace"))
    idx = s.rfind("###HD###")
    s = s[idx + len("###HD###"):].split("\n", 1)[0].strip() if idx >= 0 else ""
    rule, _, soft = s.partition("\t")
    return rule.strip(), soft.strip()


def main(argv):
    check = "--check" in argv
    cases = json.load(open(CORPUS, encoding="utf-8"))["cases"]
    rows = []
    for c in cases:
        rule, soft = verdict(c["cmd"], c.get("auto", False))
        rows.append((c["id"], c["cat"], rule or "-", soft or "-", c["cmd"], bool(c.get("auto", False))))
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
        # ⚠ 两侧的**工作根必须显式对齐**:bash 夹具是 `cd /tmp` 后判的,所以 Python 侧
        #   也必须按 /tmp 判。不显式给的话 `rule()` 会读 WORK_ROOT 环境变量,而它是
        #   **进程继承**来的 —— 从家目录启动 wchat 时 WORK_ROOT == HOME,`_creds_all_exempt`
        #   会按设计让豁免失效(不许因为"整台机器都是工作区"就白读全局 .npmrc/.pypirc),
        #   于是 Python 判 H6、bash 判放行。那**不是判据错**,是这道闸门没钉住输入条件:
        #   "同一个命令串"必须配"同一个工作根"才谈得上 parity。
        _wr, _hm = "/tmp", os.path.expanduser("~")
        for (i, cat, rl, sf, cmd, _auto) in rows:
            pr, ps = hardwall.rule(cmd, _auto, work_root=_wr, home=_hm)
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
            f.write("\t".join((r[0], r[1], r[2], r[3], r[4])) + "\n")
    n_deny = sum(1 for r in rows if r[2] != "-")
    print("wrote %s · 共 %d 条（拒 %d / 放行 %d）" % (BASE, len(rows), n_deny, len(rows) - n_deny))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
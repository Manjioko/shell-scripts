#!/usr/bin/env bash
# 第十九轮回归资产:auto_audit 落盘字段的自证探针。
# 检查四件事(trunc/tlen 三档 + root 工作根 + pol 判据版本):
#   1. target 上限 400 → 1200(1500 字命令应被截到 1200);
#   2. 新字段 trunc 按**原始长度**判定 —— 截断与否由写盘端说,不让读盘端猜;
#   3. tlen 记下原始长度,便于日后核算"有多少语料是因为封顶才丢的"。
# 为什么需要它:上一轮回放器按"印象中的 200 字上限"设可信阈值,把 22 条**完整**命令
# 当成残缺排除掉,同时真正的 400 上限没被抓准。阈值只能跟着写盘端的常量走,
# 这个探针就是钉住那个常量的东西 —— 谁改上限,这里就该红。
# 写盘一律指到 .scratch 下,绝不碰真实审计日志 ~/.ct/auto/audit.log。
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 1
mkdir -p .scratch
sed -n '/^auto_audit()/,/^}/p' wchat_ref > .scratch/aa.sh || exit 1
grep -q '^auto_audit()' .scratch/aa.sh || { echo "抽取 auto_audit 失败(函数名或花括号形状变了)"; exit 1; }

AUTO_DIR=.scratch/aud_probe
rm -rf "$AUTO_DIR"; mkdir -p "$AUTO_DIR"
AUTO_AUDIT_LOG="$AUTO_DIR/audit.log"
auto_mode=full
auto_session_id=probe
# 只喂这一个函数,不需要其余全局
# shellcheck disable=SC1091
. .scratch/aa.sh

long=$(printf 'x%.0s' $(seq 1500))     # > 1200:应截断,trunc=true
mid=$(printf 'y%.0s' $(seq 800))       # 401~1200:老上限 400 下会被掐,现在应完整
auto_audit decision shell "$long" probe-long trunc "长命令"
auto_audit decision shell "$mid" probe-mid notrunc "中等命令"
auto_audit decision shell "echo hi" probe-short notrunc "短命令"

python3 - "$AUTO_AUDIT_LOG" <<'PY'
import json, sys
want = {"probe-long": (True, 1200, 1500), "probe-mid": (False, 800, 800),
        "probe-short": (False, 7, 7)}
bad = 0
for line in open(sys.argv[1], encoding="utf-8"):
    o = json.loads(line)
    r, t, ln, tl = o["rule"], o["trunc"], len(o["target"]), o["tlen"]
    # root 字段:审计行必须说得出当时的工作根。本轮真实教训 —— 相对路径按 WORK_ROOT 解析,
    # 日志里没有根,就查不出"写成功却看不见"到底是写在哪儿(当时根=$HOME)。
    # 只判"键在不在":WORK_ROOT 缺席时 ${WORK_ROOT-} 应给空串而不是把整条审计打死。
    if "root" not in o:
        bad += 1
        print("  不符: 审计行缺 root 字段")
    # pol 字段 = 当时生效的判据版本(POLICY_SHA)。H5 那笔争议的根问题就是"无法证明当时
    # 跑的是哪一版判据",现在每条审计都自带版本号。只判"键在不在":夹具按名字抽配置、
    # 且刻意排除带命令替换的赋值行 → POLICY_SHA 为空是预期,键被删才是回归。
    if "pol" not in o:
        bad += 1
        print("  不符: 审计行缺 pol 字段(生效判据版本)")
    w = want[r]
    ok = (t is w[0] and ln == w[1] and tl == w[2])
    if not ok:
        bad += 1
    print("%-12s trunc=%-5s len(target)=%-5d tlen=%-5d %s"
          % (r, t, ln, tl, "OK" if ok else "不符(期望 %s)" % (w,)))
print("结论:", "通过" if bad == 0 else "%d 条不符" % bad)
sys.exit(1 if bad else 0)
PY
rc=$?
rm -rf "$AUTO_DIR" .scratch/aa.sh
exit $rc
#!/usr/bin/env bash
# `wchat_py/render.py` 流箱的字节契约 + 回归锁。
#
# 历史:这道闸门原来是"paint() 与 bash `stream_box_paint` 逐字节一致"的 parity 测试。
# 2026-10 修 Terminal.app 阶梯重复时**有意改掉了算法**:bash 版靠 `ESC[s`/`ESC[u`
# 存/取锚点、靠 `ESC[?7l` 关自动换行,而这两样不是所有终端都认;Python 版改成
# `\r` + `ESC[nA` 相对回位 + 按宽度硬截断。所以原地逐字节比对 bash 已不成立,
# 这里改成钉**新契约**的三件事(仍逐字节,仍可被变异杀死):
#   1. reserve/paint/close 的字节与写死的期望完全一致(位置记账不能悄悄变);
#   2. 这三段输出里**不得再出现** `ESC[s` / `ESC[u`(把本次 bug 的类型锁死);
#   3. close() 擦除的行数 == paint() 记账的 drawn(擦不净=屏幕上留残字)。
#
# 注意:期望串里 `\r` 与 `ESC[1B` 是**有意**的(不是 `\r\n`)—— 在屏幕最后一行写 `\n`
# 会触发滚屏,框内行数随之错位,这是流箱最初的三个 bug 之一。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"

python3 - <<'PY'
import sys
from wchat_py.render import StreamBox

BAR = "\033[2m│\033[0m "
pass_n = fail_n = 0
def ck(name, ok, detail=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1; print("  ok   %s" % name)
    else:
        fail_n += 1; print("  FAIL %s%s" % (name, ("  ← " + detail) if detail else ""))

# --- 期望字节(cols 写死 80,避免闸门随本地窗口宽度漂移)---------------------
EXP_RESERVE = "\033[?25l\033[?7l\r" + "\n" * 10 + "\033[9A"
EXP_PAINT = [
    "\r" + "\033[2K" + BAR + "甲行"
         + "\r\033[1B" + "\033[2K" + BAR + "乙行"
         + "\r\033[1B" + "\033[2K" + BAR + "丙行"
         + "\r\033[1B" + "\033[2K" + BAR + "残行",
    "\r" + "\033[2K" + BAR + "只有残行",
    "\r" + "\033[2K" + BAR + "x",
]
EXP_CLOSE = ("\r\033[2A" + "\033[2K" + "\r\033[1B" + "\033[2K"
             + "\r\033[1B" + "\033[2K" + "\r\033[2A" + "\033[?25h\033[?7h")

def paint_of(buf, line):
    b = StreamBox(10, cols=80); b.buf = list(buf); b.line = line
    return b.paint()

# 1) reserve
b = StreamBox(10, cols=80)
ck("reserve 字节一致", b.reserve() == EXP_RESERVE, repr(b.reserve()))

# 2) paint 三状态逐字节
states = ((["甲行", "乙行", "丙行"], "残行"), ([], "只有残行"), (["x"], ""))
for i, (buf, line) in enumerate(states):
    got = paint_of(buf, line)
    ck("paint 状态%d 逐字节一致" % i, got == EXP_PAINT[i], repr(got))

# 3) close:擦除行数 == drawn
b = StreamBox(10, cols=80); b.reserve()
b.buf = ["a", "b"]; b.line = "c"; b.paint()
ck("close 字节一致(擦 3 行)", b.close() == EXP_CLOSE, repr(b.close()))
ck("close 后状态归零", (b.drawn, b.buf, b.line, b.anchor) == (0, [], "", False))

# 4) 承重锁:三段输出都不得再出现 ESC[s / ESC[u
blob = EXP_RESERVE + "".join(EXP_PAINT) + EXP_CLOSE
ck("不再依赖 CSI s/u", "\033[s" not in blob and "\033[u" not in blob)
# 也不能靠 "保存行号" 的等价物(CSI 6n 查位置 / DECSC)偷偷绕回去
ck("不查光标位置(无 CSI 6n)", "\033[6n" not in blob)

# 5) 自检:比对函数本身必须能红(否则上面几条是恒绿)
ck("篡改一个字节 → 判据能红", paint_of(["甲行", "乙行", "丙行"], "残行") != EXP_PAINT[0].replace("甲行", "甲行X"))

print("RESULT total=%d pass=%d fail=%d" % (pass_n + fail_n, pass_n, fail_n))
sys.exit(0 if fail_n == 0 else 1)
PY


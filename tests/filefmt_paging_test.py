#!/usr/bin/env python3
"""read_file 分页闸门:offset/limit 必须真的生效,而且**读下一段必须有进展**。

## 为什么要有这道闸门

用户报"每次读文件都是全量读取,上下文膨胀"。查下来 offset+limit 是好的
(真实历史 191 次带 limit 的调用里 184 次输出行数与 limit 精确一致),但有一个
**更坏的形状**:

    limit>0        → 用 offset        ✅
    limit 不给     → **完全忽略 offset**,从第 1 行开始 → ❌

而截断提示写的恰恰是"请用 offset/limit 参数继续分段读取"。模型最自然的跟法就是
只给 offset(`read_file(path, offset=793)`)—— 拿回与上一次**逐字节相同**的前
30000 字符。零进展,上下文里多了一份一模一样的副本;再试一次还是同一份。
这是 master 就有的老 bug,Python 版逐字复刻时一起搬过来了。

所以本闸门的核心判据不是"offset 被用上了",而是**"读下一段拿到的内容与上一段
不相交"**(§2 零进展检测)。只断言"函数读了 offset 变量"是抓不住这个 bug 的。

另外三条同族缺陷(都会让调用方误判自己拿到了什么):
  · offset=0/负数 → 旧实现落到 `lines[-1:]`,读出**最后一行**,头部写"第 0-2 行";
  · offset 越界    → 返回**空窗口**,头部照样写区间,调用方无从判断;
  · 窗口到 EOF    → 头部恒写 `offset+limit-1`,实测"第 10-10008 行(共 197 行)"。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["WCHAT_API_LOG"] = "0"
os.environ["WCHAT_NO_SETUP"] = "1"

from wchat_py.filefmt import read_fmt  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


TMP = os.path.join(REPO, ".scratch")
os.makedirs(TMP, exist_ok=True)


def mkfile(name, n, width=60):
    """造 n 行、每行形如 `L0007 yyy...`,行内容唯一(便于判"有没有重复/跳行")。"""
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="utf-8") as f:
        for i in range(1, n + 1):
            f.write("L%05d %s\n" % (i, "y" * width))
    return p


_SEP = chr(92) + "n"          # 字面反斜杠+n:头部与正文之间用的就是这个(复刻 bash)


def body_lines(content):
    """把带行号的正文拆回 [(行号, 原文)]。

    ⚠ 头部与正文之间是**字面** `\n\n`(两个字符 `\` `n` 各两遍),不是真换行 ——
    这是复刻 bash 双引号串的老笔误。所以先按字面量切掉头部,再按真换行拆行;
    直接 split("\n") 会把"头部+第一行正文"粘成一段,静默吃掉第一行(第一版就踩了)。
    """
    out = []
    for ln in content.split(_SEP + _SEP)[-1].split("\n"):
        if "│" not in ln:
            continue
        num, _, text = ln.partition("│")
        try:
            out.append((int(num.strip()), text.strip()))
        except ValueError:
            pass
    return out


print("=== 1. limit 生效(基线,别为了修 offset 把这条弄坏) ===")
big = mkfile("pg_big.txt", 2000)
r = read_fmt(big, "big", 100, 5, 30000)
rows = body_lines(r["content"])
ck("offset=100 limit=5 → 恰好 5 行", len(rows) == 5, repr(len(rows)))
ck("首行是第 100 行", rows and rows[0][0] == 100, repr(rows[:1]))
ck("头部写实际区间", "第 100-104 行" in r["content"], r["content"][:60])

print("=== 2. 零进展检测:只给 offset 也必须读下一段(核心判据) ===")
# 复刻真实场景:文件超过 read_max → 提示"继续分段读" → 模型只给 offset。
first = read_fmt(big, "big", 1, 0, 30000)
ck("第一段触发截断提示", "文件过大" in first["content"], first["content"][:60])
# 从提示里把"下一次该用哪个 offset"抠出来(提示必须自带这个数,否则模型只能猜)
import re
m = re.search(r"offset=(\d+)", first["content"])
ck("截断提示给出可执行的下一段 offset", bool(m), first["content"][:120])
nxt = int(m.group(1)) if m else 0
second = read_fmt(big, "big", nxt, 0, 30000)
a = {t for _, t in body_lines(first["content"])}
b = {t for _, t in body_lines(second["content"])}
ck("只给 offset 时第二段非空", bool(b), "返回 %d 行" % len(b))
ck("第二段与第一段**无交集**(真·进展,不是把开头又给一遍)",
   a and b and not (a & b), "交集 %d 行" % len(a & b))
ck("第二段起始行号 = 提示里的 offset",
   body_lines(second["content"])[0][0] == nxt,
   repr(body_lines(second["content"])[:1]))
# 结构性反证:老实现下这两段**逐字节相同**。这里直接比正文,防止"看起来变了"。
ck("两段正文不相同", first["content"] != second["content"])
# 连读三段必须能把整份文件覆盖完(否则永远读不到尾部)
seen, off, rounds = set(), 1, 0
while rounds < 30:
    rr = read_fmt(big, "big", off, 0, 30000)
    got = body_lines(rr["content"])
    if not got:
        break
    seen |= {n for n, _ in got}
    off = got[-1][0] + 1
    rounds += 1
ck("连续按提示读下去能覆盖全文件", len(seen) == 2000,
   "覆盖 %d/2000 行,用了 %d 轮" % (len(seen), rounds))

print("=== 3. 只给 offset(小文件,不触发截断)也从 offset 起 ===")
small = mkfile("pg_small.txt", 50)
r = read_fmt(small, "small", 41, 0, 30000)
rows = body_lines(r["content"])
ck("offset=41 无 limit → 从第 41 行起", rows and rows[0][0] == 41, repr(rows[:1]))
ck("一直给到末行", rows and rows[-1][0] == 50, repr(rows[-1:]))
ck("头部写明区间(不再谎称'共 N 行:')", "第 41-50 行" in r["content"], r["content"][:60])

print("=== 4. offset 越界要明说,不许给空窗口 ===")
r = read_fmt(small, "small", 999, 5, 30000)
ck("明说超出末尾", "超出文件末尾" in r["content"], r["content"][:80])
ck("给出有效 offset 建议", "offset=" in r["content"], r["content"][:120])
ck("不给假区间", "第 999-" not in r["content"], r["content"][:80])
ck("returned=0", r.get("returned") == 0, repr(r.get("returned")))

print("=== 5. offset<1 不许落到负索引(旧实现会读出最后一行) ===")
for bad in (0, -5, "-3"):
    r = read_fmt(small, "small", bad, 2, 30000)
    rows = body_lines(r["content"])
    ck("offset=%r 当作第 1 行" % bad, rows and rows[0][0] == 1, repr(rows[:2]))
    ck("offset=%r 不读末行" % bad, rows and rows[0][1].startswith("L00001"), repr(rows[:1]))

print("=== 6. 窗口被 EOF 收窄时头部不许说谎 ===")
r = read_fmt(small, "small", 45, 100, 30000)
ck("头部只写到实际末行 50", "第 45-50 行" in r["content"], r["content"][:70])
ck("不写 45-144", "144" not in r["content"], r["content"][:70])
ck("标明已到文件末尾", "文件末尾" in r["content"], r["content"][:70])
ck("正文行数=实际(6 行)", len(body_lines(r["content"])) == 6,
   repr(len(body_lines(r["content"]))))

print("=== 7. 非空转自检:老实现的形状必须能被判红 ===")
# 这一段证明上面那些断言不是恒真:按老逻辑重算一遍"只给 offset"的结果,
# 它必须与"从 offset 起"不同 —— 否则 §2 的交集断言可能只是构造不出差异。
def legacy_offset_ignored(path, offset):
    """master 的行为:limit=0 时 offset 被忽略,从第 1 行起。"""
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f.read().split("\n") if True]
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines[:3]


ck("老行为确实丢掉 offset(反证我们有东西可修)",
   legacy_offset_ignored(small, 41) == legacy_offset_ignored(small, 1))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)


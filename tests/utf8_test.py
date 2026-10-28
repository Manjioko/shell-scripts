#!/usr/bin/env python3
"""UTF-8 完整性闸门:合法 UTF-8 经过 wchat 的截断/清洗/解码链路,不许变成 U+FFFD。

## 这道闸门从哪来

用户报"最后一段文本出现很多乱码",给了这条命令的输出:

    tail -c 200 .scratch/g12.log | cat -v | tail -2

复现结论:**不是 wchat 的 bug**。两个原因都不在我们这边:

  1. `tail -c 200` 是按**字节**切,正好落在多字节字符中间 → 输出开头就是非法 UTF-8;
  2. `cat -v` 把 >=0x80 的字节转成 `M-^T` 这种表示(这是它的职责),而在 UTF-8 locale 下
     它会把**合法**序列原样放过、把**非法**的逐字节转义 —— 于是屏幕上出现
     "合法中文 + M-^T" 混排,看着像解析错误。

wchat 用 `errors="replace"` 解码,非法字节就显示成 `�` —— 那是**忠实反映输入非法**,
不是我们编造出来的。

## 为什么要留一道闸门

"按字节截断"在这个项目里到处都是(`clip_bytes` / `truncate_output` / 头尾省略 /
tty 分块读取),**任何一个环节在多字节字符中间下刀,都会产出这种乱码**,而它的症状
与"用户的命令选错了"完全一样 —— 下次真出问题时会没人信。所以:

  1. 钉住"合法 UTF-8 进来,出去不含 U+FFFD"(多组极端参数);
  2. 钉住"非法字节进来**必须**变成 U+FFFD"(证明上一条不是恒真);
  3. 钉住 pty 侧**先 join 再解码**(逐块解码是最典型的引入方式 —— 65536 字节的读取边界
     随时可能劈开一个中文字符)。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from wchat_py import textutil  # noqa: E402
from wchat_py.termwin import clean_line  # noqa: E402

BAD = "\ufffd"
P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


# ---------------- 1) 合法 UTF-8 不许变成替换字符 ----------------
print("=== 1. 合法输入 -> 不出现替换字符 ===")

CASES = [
    ("纯中文", "一行中文测试内容" * 400),
    ("emoji 混排", "😀 emoji 与 中文 混排 " * 200),
    ("组合字符", "e\u0301\u0327 组合" * 200),
    ("CJK 扩展", "𠀀𠀁 生僻字" * 100),
    ("多行", "\n".join("第%d行:中文 😀" % i for i in range(2000))),
]

for tag, text in CASES:
    worst = 0
    worst_arg = None
    # 故意用很窄的列宽:最容易在字符中间下刀的地方
    for cols in (2000, 500, 100, 30, 13, 7, 4, 3, 2, 1):
        for lines in (400, 10, 3, 1):
            out = textutil.truncate_output(text, cols, lines)
            n = out.count(BAD)
            if n > worst:
                worst, worst_arg = n, (cols, lines)
    ck("truncate_output 不引入替换字符: %s" % tag, worst == 0,
       "最多 %d 个(参数 %r)" % (worst, worst_arg))

# clip_bytes(grep/skill 走它)
for tag, text in CASES[:3]:
    worst = 0
    for maxb in (1, 2, 3, 4, 5, 7, 13, 100, 2000):
        out = textutil.clip_bytes(text.encode("utf-8"), maxb, "…".encode("utf-8"))
        n = out.decode("utf-8", "replace").count(BAD)
        worst = max(worst, n)
    ck("clip_bytes 不引入替换字符: %s" % tag, worst == 0, "最多 %d 个" % worst)

# clean_line(显示前每行都过它)
for t in ("中文 😀 正常", "带\x1b[2mANSI\x1b[0m的中文", "中文\r回车",
          "\x1b[1;31m红\x1b[0m", "退格\b残留\b\b中文"):
    ck("clean_line 不引入替换字符: %r" % t[:14], clean_line(t).count(BAD) == 0,
       repr(clean_line(t)))

# ---------------- 2) 非空转自检:非法输入必须变成替换字符 ----------------
# 否则上面那些"== 0"可能只是因为检查写错了(永远是 0)。
print("=== 2. 非空转自检(非法输入必须被抓到) ===")
broken = b"\xe5\x90\x88"[:2]            # 合 的前两个字节:E5 90,非法
out = textutil.truncate_output(broken.decode("utf-8", "replace"), 100, 10)
ck("非法字节经 replace 会变成替换字符(证明第 1 节不是恒真)",
   out.count(BAD) >= 1, repr(out))
# 直接喂非法字节给 clip_bytes,确认它自己不制造问题、但解码时如实暴露
cl = textutil.clip_bytes(broken, 100, b"")
# clip_bytes 是"流"语义:每行后面补 \n。所以比的是"去掉尾部换行后与输入一致"。
ck("clip_bytes 原样保留非法字节(不在字节层编造)",
   cl.rstrip(b"\n") == broken, repr(cl))
ck("但解码时会如实出现替换字符", cl.decode("utf-8", "replace").count(BAD) == 1,
   repr(cl.decode("utf-8", "replace")))

# ---------------- 3) 结构锁:pty 必须先 join 再解码 ----------------
print("=== 3. pty 解码顺序 ===")
pty_src = open(os.path.join(REPO, "wchat_py", "termpty.py"), encoding="utf-8").read()
ck("pty 用 join(chunks) 一次性解码",
   'b"".join(chunks).decode' in pty_src or "b''.join(chunks).decode" in pty_src)
# 逐块解码是这类乱码最典型的引入方式(os.read 的 65536 边界随时劈开中文字符)
lines_with_decode = [ln.strip() for ln in pty_src.split("\n")
                     if ".decode(" in ln and not ln.strip().startswith("#")]
bad = [ln for ln in lines_with_decode if "join(chunks)" not in ln]
ck("没有逐块解码(每处 decode 都在 join 之后)", not bad, repr(bad))

# 端到端:真跑一个输出中文的 pty 命令,看回来的字节解码后有没有替换字符
from wchat_py.termpty import pty_run  # noqa: E402
disp, full, rc, to, intr = pty_run(
    ["bash", "-c", "printf '中文输出😀不带换行'"],
    timeout=20, win=10, env=dict(os.environ))
ck("pty_run 真跑中文输出:无替换字符", BAD not in full, repr(full[:80]))
ck("pty_run 真跑中文输出:内容正确", "中文输出😀" in full, repr(full[:80]))

# ---------------- 4) 复现用户那条命令,确认责任在命令本身 ----------------
print("=== 4. 复现 tail -c + cat -v(责任归属) ===")
import subprocess  # noqa: E402

_log = os.path.join(REPO, ".scratch", "_utf8_case.log")
os.makedirs(os.path.dirname(_log), exist_ok=True)
with open(_log, "w", encoding="utf-8") as f:
    f.write("\n" * 40)
    f.write("\x1b[1m── 合计 pass=58 fail=0 · 源码 6a95f697e7c7 ──\x1b[0m\n")
    f.write("GATES run=58 pass=58 fail=0\n")

# 确定性地找一个"切在多字节字符中间"的字节数 —— 不能靠 200 这种巧合。
_data = open(_log, "rb").read()
_split_n = None
for n in range(1, min(len(_data), 200)):
    seg = _data[-n:]
    try:
        seg.decode("utf-8")
    except UnicodeDecodeError:
        _split_n = n
        break
ck("能找到'切在多字节中间'的字节数(前提成立)", _split_n is not None, str(_split_n))
if _split_n:
    raw_tail = subprocess.run(["bash", "-c", "tail -c %d %s" % (_split_n, _log)],
                              capture_output=True).stdout
    try:
        raw_tail.decode("utf-8")
        ck("`tail -c N` 会切出非法 UTF-8(说明乱码来自命令,不是我们)", False,
           repr(raw_tail[:20]))
    except UnicodeDecodeError:
        ck("`tail -c N` 会切出非法 UTF-8(说明乱码来自命令,不是我们)", True)

raw_all = subprocess.run(["bash", "-c", "cat %s" % _log], capture_output=True).stdout
try:
    raw_all.decode("utf-8")
    ck("同一文件整读是合法 UTF-8(文件本身没问题)", True)
except UnicodeDecodeError:
    ck("同一文件整读是合法 UTF-8(文件本身没问题)", False)

# 用 tail -n(按行)就不会切坏 —— 这是给用户的正确用法
raw_lines = subprocess.run(["bash", "-c", "tail -n 6 %s" % _log],
                           capture_output=True).stdout
try:
    raw_lines.decode("utf-8")
    ck("`tail -n`(按行)不切坏字符 —— 正确用法", True)
except UnicodeDecodeError:
    ck("`tail -n`(按行)不切坏字符 —— 正确用法", False)

os.unlink(_log)

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)


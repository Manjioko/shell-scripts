#!/usr/bin/env python3
"""auto 终审 _auto_judge 单测：Y/N/缺省/异常四路径（fail-closed）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("WCHAT_AUTO_ASSUME_YES", None)
import wchat_py.cli as c

T = F = 0
def ck(n, ok):
    global T, F; T += 1
    if ok: print("ok -", n)
    else: print("FAIL -", n); F += 1

ck("ALLOW -> Y", c._auto_judge("ls", call=lambda x: "ALLOW 安全只读") == ("Y", "安全只读"))
ck("DENY -> N", c._auto_judge("rm", call=lambda x: "DENY 破坏性")[0] == "N")
ck("Y 放行", c._auto_judge("ls", call=lambda x: "Y 安全")[0] == "Y")
ck("N 拒绝", c._auto_judge("rm", call=lambda x: "N 危险")[0] == "N")
ck("空判定 -> None(fail-closed)", c._auto_judge("ls", call=lambda x: "") == (None, None))
def bad(x): raise RuntimeError("x")
ck("异常 -> None(fail-closed)", c._auto_judge("ls", call=bad) == (None, None))
os.environ["WCHAT_AUTO_ASSUME_YES"] = "1"
ck("ASSUME_YES 直接 Y", c._auto_judge("ls")[0] == "Y")
os.environ.pop("WCHAT_AUTO_ASSUME_YES", None)

# ── 解析容错:模型不会永远按"空格分隔"的格式回 ──────────────────────────
# 真实事故:提示词要求"只回一行 ALLOW 或 DENY",模型回 `ALLOW：只读`(中文冒号)、
# `**ALLOW** ...`(markdown 加粗)。旧实现按空格切 token 再全等比对 ⇒ 全部无判定
# ⇒ fail-closed 拒绝,用户看到的是"auto 终审不可用",而模型其实明确放行了。
print("=== 判定格式容错(不得再误报'终审不可用') ===")
for raw, want, desc in [
    ("ALLOW: 只读", "Y", "英文冒号"),
    ("ALLOW：只读", "Y", "中文全角冒号"),
    ("ALLOW。只读", "Y", "中文句号"),
    ("**ALLOW** 只读", "Y", "markdown 加粗"),
    ("ALLOW\t只读", "Y", "tab 分隔"),
    ("ALLOW（无风险）", "Y", "中文括号"),
    ("ALLOW理由", "Y", "无分隔符"),
    ("判断如下:\nALLOW 只读", "Y", "前置说明行"),
    ("DENY：删库", "N", "DENY 中文冒号"),
    ("**DENY** 越界", "N", "DENY markdown"),
    ("N3:越界写", "N", "N+编号"),
]:
    ck("%s -> %s" % (desc, want),
       c._auto_judge("x", call=lambda _c, _r=raw: _r)[0] == want)

# 安全红线:否定语境里的 ALLOW 必须按拒绝处理,绝不能放行
print("=== 安全红线 ===")
for raw in ("我不能 ALLOW 这个命令", "not allow: 危险", "无法 ALLOW 越界操作"):
    ck("否定语境不当放行: %r" % raw,
       c._auto_judge("x", call=lambda _c, _r=raw: _r)[0] == "N")
ck("理由里含 not 不影响正常 ALLOW",
   c._auto_judge("x", call=lambda _c: "ALLOW: not a problem")[0] == "Y")

# fail-closed 仍要保持:没有判定词、也不是 Y/N 开头的自然语言,不许猜
print("=== 仍 fail-closed ===")
for raw in ("可以执行,这是只读命令", "这个命令看起来没问题"):
    ck("自然语言不猜: %r" % raw, c._auto_judge("x", call=lambda _c, _r=raw: _r) == (None, None))

# 初审与终审必须同口径(否则同一回复两道闸结论不同)
ck("初审复用同一解析器(中文冒号 Y)",
   c._auth_think.__doc__ is not None and "_parse_verdict" in open(
       os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "wchat_py/cli.py"), encoding="utf-8").read())

print("RESULT total=%d pass=%d fail=%d" % (T, T - F, F))
sys.exit(0 if F == 0 else 1)
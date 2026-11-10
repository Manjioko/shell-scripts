#!/usr/bin/env python3
"""docread 自检:.docx/.pdf → Markdown 的转换逻辑 + read_file 集成 + 各降级分支。

**两档运行**(都必须是绿的):
  · 装了 markitdown(brew venv / 3.10+ 环境):跑真实转换(sample.docx/pdf 抽出预期文字、
    扫描版判 empty、缓存命中、.doc 给干净提示而非乱码、文本文件路径不变)。
  · 没装(如 deb / 3.9 开发机):只跑"未启用"降级分支 —— 断言不抛栈、给的是可行动提示。

夹具在 tests/fixtures/sample.docx|sample.pdf|sample_scan.pdf(预先生成,测试不依赖
python-docx/reportlab)。末行 RESULT ...。
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("WCHAT_NO_SETUP", "1")

from wchat_py import docread  # noqa: E402
from wchat_py.tools_run import read_file  # noqa: E402

FIX = os.path.join(REPO, "tests", "fixtures")
DOCX = os.path.join(FIX, "sample.docx")
PDF = os.path.join(FIX, "sample.pdf")
SCAN = os.path.join(FIX, "sample_scan.pdf")

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def ck_skip(name, why):
    print("  --   %s(跳过:%s)" % (name, why))


def rf(path, display=None, off=None, lim=None, rmax="30000"):
    c, s = read_file(path, display or os.path.basename(path), off, lim, rmax, os.path.dirname(path))
    return s, c


# ── 1) 纯判定(无 markitdown 也成立)─────────────────────────────────────────
ck("is_doc(.docx) 真", docread.is_doc("a/x.docx") is True)
ck("is_doc(.PDF) 真(大小写不敏感)", docread.is_doc("x.PDF") is True)
ck("is_doc(.txt) 假", docread.is_doc("x.txt") is False)
ck("is_doc(无扩展) 假", docread.is_doc("Makefile") is False)
ck("is_legacy_word(.doc) 真", docread.is_legacy_word("x.doc") is True)
ck("is_legacy_word(.docx) 假", docread.is_legacy_word("x.docx") is False)
ck("legacy_hint 含'已忽略'且点出 .docx", ("已忽略" in docread.legacy_hint("x.doc")
   and ".docx" in docread.legacy_hint("x.doc")))
ck("available() 返回 bool", isinstance(docread.available(), bool))

# ── 2) .doc:无论有没有 markitdown,都必须是"干净提示"而不是二进制乱码 ──────────
_d = tempfile.mkdtemp()
_olddoc = os.path.join(_d, "legacy.doc")
open(_olddoc, "wb").write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
_s, _c = rf(_olddoc, "legacy.doc")
ck(".doc 走'已忽略'提示(不 decode 成乱码)", "已忽略" in _c and "\ufffd" not in _c, repr(_c[:80]))

# ── 3) 文本文件路径不受影响(没有'已转 Markdown'字样)────────────────────────
_txt = os.path.join(_d, "note.txt")
open(_txt, "w", encoding="utf-8").write("第一行\n第二行\n")
_s, _c = rf(_txt, "note.txt")
ck("纯文本仍走原路径(无'已转 Markdown')", "已转 Markdown" not in _s and "第一行" in _c and "第二行" in _c, repr(_s))

# ── 4) 按 markitdown 是否可用分两档 ──────────────────────────────────────────
if docread.available():
    print("  [markitdown 可用 → 跑真实转换]")
    _cd = os.path.join(_d, "cache", "doc")
    st, md, mdf = docread.convert(DOCX, _cd)
    ck("docx 转换 status=ok", st == "ok", st)
    ck("docx 抽出标题文字", "DocRead 测试标题" in md, repr(md[:60]))
    ck("docx 保留正文与列表项", "这是测试文档的正文内容" in md and "alpha beta gamma" in md)
    ck("docx 落盘缓存文件存在", bool(mdf) and os.path.isfile(mdf), repr(mdf))
    # 缓存命中:第二次应返回同一个 md_file 且内容一致
    st2, md2, mdf2 = docread.convert(DOCX, _cd)
    ck("docx 二次命中缓存(同文件同内容)", st2 == "ok" and mdf2 == mdf and md2 == md, repr(mdf2))
    ck("缓存文件名是 sha1(.md)", bool(mdf) and os.path.basename(mdf).endswith(".md")
       and len(os.path.basename(mdf)[:-3]) == 40, os.path.basename(mdf or ""))

    st, md, _ = docread.convert(PDF, _cd)
    ck("pdf 转换 status=ok", st == "ok", st)
    ck("pdf 抽出文字", "PDF Test Content 2024" in md and "Second line" in md, repr(md[:80]))

    st, md, _ = docread.convert(SCAN, _cd)
    ck("扫描版 pdf 判为 empty(非 ok 也非抛栈)", st == "empty", st)
    ck("扫描版提示提到'没有可提取的文字'", "没有可提取的文字" in md, repr(md[:80]))

    st, md, _ = docread.convert(os.path.join(_d, "nope.docx"), _cd)
    ck("不存在的文件 → error(被兜住)", st == "error", st)

    # read_file 集成:docx 走转换,状态行标注 + 带行号
    _s, _c = rf(DOCX, "sample.docx")
    ck("read_file(docx) 状态含'已转 Markdown'", "已转 Markdown" in _s, repr(_s))
    ck("read_file(docx) 正文含行号与内容", "1│" in _c and "DocRead 测试标题" in _c, repr(_c[:80]))
    # 分页:limit 生效
    _s2, _c2 = rf(DOCX, "sample.docx", 1, 2)
    ck("read_file(docx) 支持 limit 分页", "第 1-2 行" in _c2 or "共" in _c2, repr(_c2[:60]))
else:
    print("  [markitdown 不可用 → 只验降级分支]")
    ck_skip("docx/pdf 真实转换", "本环境无 markitdown")
    st, md, _ = docread.convert(DOCX, None)
    ck("未装 markitdown:status=unavailable", st == "unavailable", st)
    ck("未装提示含'未安装'与安装指引", "未安装" in md and "requirements-doc" in md, repr(md[:80]))
    _s, _c = rf(DOCX, "sample.docx")
    ck("read_file(docx) 未装时给'文档未转换'", "文档未转换" in _s and "markitdown" in _c, repr(_s))

# ── 3) Ctrl-F 自动读 + pdfminer 字体警告压制 ────────────────────────────────
# 现场(2026-11 用户实测):① Ctrl-F 选中的文件只传路径,模型还得自己 read_file 一遍;
# ② 某些 PDF 的字体缺 FontBBox,pdfminer 每个字形刷一条 WARNING,673KB 文档刷了 71 行。
print("=== 3. Ctrl-F 自动读(小文件直接给内容)与 pdfminer 日志压制 ===")
import logging as _logging                                          # noqa: E402
from wchat_py import repl as _repl                                  # noqa: E402

_before = _logging.getLogger("pdfminer").level
with docread._quiet_pdf_warnings():
    _during = _logging.getLogger("pdfminer").level
_after = _logging.getLogger("pdfminer").level
ck("转换期间把 pdfminer 压到 ERROR(否则字体警告刷屏)", _during >= _logging.ERROR, str(_during))
ck("退出后逐级恢复正常(不在全局留副作用)", _after == _before, "%s vs %s" % (_after, _before))

_tmp = tempfile.mkdtemp()
os.environ["CT_HOME"] = _tmp
_small = os.path.join(_tmp, "s.txt")
open(_small, "w", encoding="utf-8").write("第一行内容\n第二行内容\n")
_o, _i = _repl.parse_placeholders("📋 [文件] " + _small + "\u200b\n问题")
ck("Ctrl-F 小文本直接注入内容", "第一行内容" in _o and "第二行内容" in _o, repr(_o[:60]))
ck("Ctrl-F 小文本不再要求模型 read_file(省一轮往返)",
   "请先调用 read_file" not in _o and "无需再调用" in _o, repr(_o[-40:]))

_big = os.path.join(_tmp, "b.txt")
open(_big, "w", encoding="utf-8").write("x" * 40000)
_o, _i = _repl.parse_placeholders("📋 [文件] " + _big + "\u200b\n问题")
ck("超过 FILE_AUTOREAD_MAX 退回分页提示", "read_file" in _o and "超过自动读取上限" in _o, repr(_o[:60]))
ck("大文件不把整段内容塞进上下文", "x" * 100 not in _o)

_o, _i = _repl.parse_placeholders("📋 [文件] " + PDF + "\u200b\n这文件说什么")
if docread.available():
    ck("Ctrl-F 选 PDF 直接附带内容", "PDF Test Content 2024" in _o, repr(_o[:80]))
    ck("Ctrl-F 选 PDF 不要求再 read_file", "请先调用 read_file" not in _o, repr(_o[:40]))
else:
    ck("未装 markitdown:给出未安装说明(不是让模型白读一次)", "未安装" in _o, repr(_o[:80]))

# 图片占位符不能受影响(仍走转述通道,不进文本)
_img = os.path.join(_tmp, "a.png")
open(_img, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
_ot, _im = _repl.parse_placeholders("📋 [图片] " + _img + "\u200b\n看图")
ck("图片占位符仍走转述(文本里不留路径)", _img not in _ot and _im == [_img], "%r %r" % (_ot, _im))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)
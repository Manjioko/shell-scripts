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

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)
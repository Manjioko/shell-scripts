#!/usr/bin/env python3
"""docread —— 把 .docx / .pdf 转成 Markdown 文本,供 `read_file` 工具读取。

为什么单独一层:**转换是可选能力**。底层用 `markitdown`(微软),它依赖
`magika`→`onnxruntime`(重、且 aarch64 Linux 无 wheel)。所以这套依赖**不进基础
requirements.txt**,只在 `requirements-doc.txt` 里做"尽力安装";缺失时本模块所有调用
都返回**可行动说明**而不是抛栈 —— wchat 主流程不因没有它而不可用(与 pillow 缺了就退回
sips 是同一档"可选组件"哲学)。deb 等装不上的平台天然走"未启用"分支。

对外接口(**纯函数风格,便于离线单测**):
  · DOC_EXTS / is_doc(path)          —— 按扩展名判定要不要走文档通道
  · available()                      —— markitdown 是否可导入(只查 spec,不重型 import)
  · convert(path, cache_dir=None)    —— 返回 (status, text, md_file)
        status ∈ {"ok","unavailable","empty","error"}
          ok          → text 是 markdown;md_file 是落盘的 .md(便于复用分页/行号),可能 None
          unavailable → 未安装转换能力
          empty       → 转出来没有可提取文字(扫描版/纯图 PDF 或空文档)
          error       → 转换抛错(损坏/加密/老式 .doc 等)
        非 ok 时 text 是**给人看的说明**,可直接当 read_file 的 content 用。

设计要点(都是实测踩出来的):
  · 只用 `convert_local()`,**不用**宽松的 `convert()` —— 后者能抓远程 URI,
    而我们的输入是"已通过授权、已 resolve 的本地路径",绝不能给它开 SSRF/读任意资源的门。
  · 扫描版 PDF 不报错而是**返回空串**(实测)→ 必须自己判空,给"可能是扫描件"的话,
    而不是把空内容喂给模型让它以为文件是空的。
  · 老式 .doc / 空 PDF / 损坏文件会抛不同异常 → 统一兜成 error,说明里点出常见原因。
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os

DOC_EXTS = (".docx", ".pdf")
# 明确**不支持**的老式 Word:markitdown 解析不了 .doc。若不在这里拦,read_file 会把它
# 当普通文本 decode 成乱码(实测)。给一句"另存为 .docx"的干净提示,符合"转不了就说清楚"。
LEGACY_WORD = (".doc",)

_UNAVAILABLE_MSG = (
    "⚠ 无法读取该文档:{name}\n"
    "原因:未安装文档转换能力(markitdown)。它需要 Python 3.10+ 且有 wheel 的平台。\n"
    "安装:brew 用户 `brew reinstall wchat`;源码用户 `pip install -r requirements-doc.txt`。")


def is_doc(path):
    """扩展名是否属于走文档转换通道的类型(.docx/.pdf)。大小写不敏感。"""
    return os.path.splitext(path or "")[1].lower() in DOC_EXTS


def is_legacy_word(path):
    """是不是明确不支持的老式 Word(.doc):read_file 据此给"另存为 .docx"提示而非乱码。"""
    return os.path.splitext(path or "")[1].lower() in LEGACY_WORD


def legacy_hint(path):
    return ("⚠ 不支持直接读取 %s:这是老式 Word(.doc)格式,markitdown 无法解析。\n"
            "请把文件另存为 .docx 后再读取(或先转成纯文本)。已忽略。"
            % os.path.basename(path))


def available():
    """markitdown 能否导入。只查 find_spec(不真正 import),避免重型导入副作用。"""
    try:
        import importlib.util as _ilu
        return _ilu.find_spec("markitdown") is not None
    except Exception:
        return False


def cache_file(path, cache_dir):
    """按 (绝对路径, 大小, mtime) 生成缓存 .md 路径;cache_dir 为空则返回 None(不缓存)。"""
    if not cache_dir:
        return None
    try:
        st = os.stat(path)
        key = "%s|%d|%d" % (os.path.abspath(path), st.st_size, int(st.st_mtime))
    except OSError:
        key = os.path.abspath(path)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, h + ".md")


def _one_line(e):
    """异常 → 一行可读文本(取第一条非空行,避免把整段 traceback 塞进上下文)。"""
    s = str(e).strip()
    for ln in s.splitlines():
        ln = ln.strip()
        if ln:
            return ln
    return s or repr(e)


# pdfminer 的字体警告:"Could not get FontBBox from font descriptor because None
# cannot be parsed as 4 floats" —— 它走 logging(模块级 `log = getLogger(__name__)`,
# 没有自己的 handler,靠 lastResort 落到 stderr)。某些 PDF 的字体描述符缺 FontBBox 时
# **每个字形刷一条**,实测一个 673KB 的需求文档刷了 71 行,把用户终端整个淹掉。
# 这条警告对"能不能读到文字"毫无影响(FontBBox 只用于版面坐标,解析失败就退 0),
# 而且同一句重复到根本没有信息量 —— 属于纯噪音,转换期间压到 ERROR。
_QUIET_LOGGERS = ("pdfminer", "pdfminer.pdffont", "pdfminer.pdfinterp",
                  "pdfminer.pdfpage", "pdfminer.pdfdocument", "pdfminer.psparser")


@contextlib.contextmanager
def _quiet_pdf_warnings():
    """转换期间把 pdfminer 的 logger 压到 ERROR,退出时**逐个恢复**原级别。

    只动 pdfminer 这一支,不碰 root / 别家 logger;用 finally 恢复,异常路径也不留副作用。
    """
    saved = {}
    for n in _QUIET_LOGGERS:
        lg = logging.getLogger(n)
        saved[n] = lg.level
        if lg.level < logging.ERROR:
            lg.setLevel(logging.ERROR)
    try:
        yield
    finally:
        for n, lv in saved.items():
            logging.getLogger(n).setLevel(lv)


def convert(path, cache_dir=None):
    """把文档转成 markdown。返回 (status, text, md_file)。语义见模块 docstring。"""
    name = os.path.basename(path)
    if not available():
        return ("unavailable", _UNAVAILABLE_MSG.format(name=name), None)
    cf = cache_file(path, cache_dir)
    if cf and os.path.isfile(cf):
        try:
            with open(cf, encoding="utf-8") as f:
                return ("ok", f.read(), cf)
        except OSError:
            pass                       # 缓存读不了就当没缓存,重新转
    try:
        from markitdown import MarkItDown
        with _quiet_pdf_warnings():
            res = MarkItDown(enable_plugins=False).convert_local(path)
        md = getattr(res, "markdown", None)
        if md is None:
            md = getattr(res, "text_content", "") or ""
    except Exception as e:               # UnsupportedFormatException / FileConversionException / ...
        return ("error",
                "⚠ 无法把该文档转成文本:{name}\n原因:{why}\n"
                "(文件可能损坏、加密,或格式不受支持;老式 .doc 请先另存为 .docx)".format(
                    name=name, why=_one_line(e)), None)
    if not md.strip():
        return ("empty",
                "📄 {name} 转换后没有可提取的文字(可能是扫描版/纯图片 PDF,或空文档)。"
                "如需读取图像内容,可改用视觉通道或先做 OCR。".format(name=name), None)
    if cf:
        try:
            os.makedirs(os.path.dirname(cf), exist_ok=True)
            with open(cf, "w", encoding="utf-8") as f:
                f.write(md)
        except OSError:
            cf = None                  # 缓存写不了不影响本次返回
    return ("ok", md, cf)
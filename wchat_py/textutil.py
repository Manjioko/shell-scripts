r"""textutil —— 字节安全的文本整形(迁移自 wchat.sh 的 `clip_line_width`)。

为什么值得迁:原实现是一段 `LC_ALL=C awk` 的逐字节宽度截行,注释里记了两类
真事故 —— 中文/emoji 被从**字节中间**切断,下游 `sed`/渲染助手遇非法 UTF-8
整篇中止(现象是"加载历史只渲染到一半")。Python 里按字节边界处理更直观,
顺带去掉了对 awk 实现的隐式依赖(BSD/GNU awk 的 length/substr 都是字节语义,
但行为差异一旦出现极难归因)。

退出码(见 `_contract`):
  0  成功,结果在 stdout
  1  未知子命令
语义与旧 awk **逐字节等价**(由 tests/py_channel_test.sh 的 parity 断言钉住):
  · 按 b'\n' 拆行,末尾换行不产生额外空记录(对齐 awk RS="\n");
  · 只在字符边界下刀;越界/游离续字节一律按 1 字节跳过,不卡死;
  · 行宽 ≤ max 的行原样输出;max ≤ 0(含非数值)不截断;
  · <max> 缺省/空 → 200;<mark> 缺省 → '…',显式给空串 → 空。

用法:
  python3 -m wchat_py.textutil clip_line_width <max_bytes> [mark]          # stdin -> stdout
  python3 -m wchat_py.textutil truncate_output <max_col> <max_lines> [mark] # stdin -> stdout
"""

from __future__ import annotations

import sys

from . import _contract

DEFAULT_MAX = 200
DEFAULT_MARK = "\u2026"          # … 与 bash 侧 "${2-…}" 的默认值一致


def _need(lead: int) -> int:
    """按 UTF-8 首字节推算该字符占几字节。越界/游离续字节返回 1(跳过不卡死)。"""
    if lead < 0x80:        # ASCII
        return 1
    if lead <= 0xBF:       # 游离续字节(非法)
        return 1
    if lead <= 0xDF:       # 2 字节前导
        return 2
    if lead <= 0xEF:       # 3 字节前导(含中文)
        return 3
    return 4               # 0xF0-0xFF(emoji 等)


def _clip(line: bytes, maxb: int) -> bytes:
    """只在字符边界下刀:返回能放进 maxb 字节的前缀。"""
    n = len(line)
    i = 0
    while i < n:
        need = _need(line[i])
        if i + need > n:        # 序列被截尾,别越界
            need = 1
        if i + need > maxb:     # 放不下 → 在字符边界停刀
            break
        i += need
    return line[:i]


def _split_lines(data: bytes) -> "list[bytes]":
    r"""按 b'\n' 拆行;语义与 awk 的 RS="\n" 一致(末尾换行不产生额外空记录)。"""
    if not data:
        return []
    if data.endswith(b"\n"):
        return data[:-1].split(b"\n")
    return data.split(b"\n")


def _clip_stream(data: bytes, maxb: int, mark: bytes) -> bytes:
    recs = _split_lines(data)
    if not recs:
        return b""
    out = []
    for line in recs:
        if maxb > 0 and len(line) > maxb:
            out.append(_clip(line, maxb) + mark)
        else:
            out.append(line)
    return b"\n".join(out) + b"\n"


def clip_bytes(data: bytes, maxb: int, mark: bytes) -> bytes:
    """公共入口:按字节边界截行(供 skill 等模块复用,避免"同一条规矩抄三遍")。"""
    return _clip_stream(data, maxb, mark)


def _parse_max(raw: str) -> int:
    """与 `awk -v max=` 等价:非数值按 0 处理(即不截断)。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def cmd_clip_line_width(argv) -> int:
    # 与 bash 的 "${1:-200}" / "${2-…}" 语义对齐:
    #   max 缺省或空 -> 200;mark 未给 -> '…',显式给空串 -> 空。
    raw_max = argv[0] if (len(argv) > 0 and argv[0] != "") else str(DEFAULT_MAX)
    mark = argv[1] if len(argv) > 1 else DEFAULT_MARK
    data = _contract.read_stdin_bytes()
    out = _clip_stream(data, _parse_max(raw_max), mark.encode("utf-8"))
    _contract.write_bytes(out)
    return _contract.EXIT_OK


# 工具输出截断:命令输出是上下文里最占 token 的部分,超长只留头尾
# (头=摘要/表头,尾=报错/最终结果,中间多为重复日志)。两层限制:
# 先截超长单行(靠 _clip_stream),再按行数留头尾。
TRUNCATE_MARK = "...(该行过长已截断)"


def _render_slice(seg: "list[bytes]", ends_at_end: bool) -> bytes:
    """复现 `head`/`tail` 的字节行为:每行间补 \\n;只有当这段**不落在输入末尾**
    时才补结尾换行。实测(macOS/BSD,`printf 'a\\nb' | head -n 5 | od -c`):末行无
    换行时 head/tail 都**不补** —— 所以尾段(末尾那段)恒不加结尾换行。"""
    if not seg:
        return b""
    out = b"\n".join(seg)
    if not ends_at_end:
        out += b"\n"
    return out


def _truncate_stream(data: bytes, max_col: int, max_lines: int, mark: bytes) -> bytes:
    """与 bash 旧实现逐字节等价(parity 由 tests/py_channel_test.sh 钉住):
      · `lines` 用 **换行符个数**(对齐 `wc -l`,不是"行数");
      · `cropped=$(...)` 会剥掉**全部**结尾换行 —— 这里 rstrip 复现;
      · 超限时 head/tail 各取一半;中间插入省略行;末段按 head/tail 实测字节行为拼接。
    """
    lines = data.count(b"\n")
    cropped = _clip_stream(data, max_col, mark).rstrip(b"\n")
    if lines > max_lines:
        h = max_lines // 2
        parts = _split_lines(cropped)      # b"" -> []
        if h > 0:
            head = _render_slice(parts[:h], ends_at_end=(h >= len(parts)))
            tail = _render_slice(parts[len(parts) - h:], ends_at_end=True)
        else:
            head = b""
            tail = b""
        mid = ("\n... (中间 %d 行已省略) ...\n\n" % (lines - max_lines)).encode("utf-8")
        return head + mid + tail
    return cropped


def cmd_truncate_output(argv) -> int:
    # <max_col> <max_lines> [mark];正文经 stdin 传入(大字符串绝不走 argv)。
    max_col = _parse_max(argv[0] if len(argv) > 0 else "")
    max_lines = _parse_max(argv[1] if len(argv) > 1 else "")
    mark = argv[2] if len(argv) > 2 else TRUNCATE_MARK
    data = _contract.read_stdin_bytes()
    out = _truncate_stream(data, max_col, max_lines, mark.encode("utf-8"))
    _contract.write_bytes(out)
    return _contract.EXIT_OK


_COMMANDS = {
    "clip_line_width": cmd_clip_line_width,
    "truncate_output": cmd_truncate_output,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "textutil: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
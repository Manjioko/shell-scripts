"""filefmt —— 文件类工具的**纯格式化**(迁移自 wchat.sh 的 tool_read_file 输出段)。

只做"给定已解析路径 + 分页/截断参数 → 带行号的展示内容",不做任何权限裁决
(`resolve_path`/`path_access` 留 bash)。

退出码(见 `_contract`):
  0  成功,单行 JSON 写 stdout:`{"content": "...", "lines": N}`
  1  未知子命令

用法:
  python3 -m wchat_py.filefmt read <resolved> <display_path> <offset> <limit> <read_max>
"""

from __future__ import annotations

import json
import sys

from . import _contract


def _int(s, default=0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def read_fmt(resolved: str, path: str, offset, limit, read_max) -> dict:
    """带行号输出。行号供 edit_file 的 old_string 定位与沟通引用。

    分档(顺序即优先级):
    · offset 越界        → 明说"超出末尾"并给可用的 offset,不给空窗口;
    · limit>0            → 取 [offset, offset+limit-1] 窗口(到 EOF 提前收,头部报实际区间);
    · offset>1 且超长    → **从 offset 起**按字符累计截断(旧版从第 1 行起,offset 被吞);
    · 其余               → 从 offset 起到末尾全给。
    `out` 与 bash 的 `$(...)` 一致**剥掉结尾换行**(命令替换会吃掉)。

    以 offset 为起点的两种"无 limit"分支是**有意的行为修正**,不再是 master 的逐字复刻:
    master 只在 `limit>0` 时才用 offset,于是"只给 offset"会静默返回文件**开头**
    (见下方 2026-xx 实测)。而截断提示恰恰在说"请用 offset/limit 继续分段读取" ——
    模型照着只给 offset,拿回与上一次**逐字节相同**的前 30000 字符,零进展,还可能反复读。
    这条由 `tests/filefmt_paging_test.py` 的"零进展检测"钉住。
    """
    with open(resolved, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    total_lines = text.count("\n")          # 对齐 wc -l
    total_chars = len(text)                 # 对齐 wc -m
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    offset = _int(offset, 1)
    limit = _int(limit, 0)
    read_max = _int(read_max, 0)
    # offset<1 一律当第 1 行:旧实现 offset=0 会走到 lines[-1:],读出**最后一行**,
    # 头部还写"第 0-2 行"(实测)。越界的负索引是静默错误,不能留。
    if offset < 1:
        offset = 1
    start = offset - 1
    n = len(lines)
    # 展示用的"总行数":`wc -l`(数换行符)在**末尾没有换行**的文件上会比真实行数少 1,
    # 于是出现过"第 2070-2079 行(共 2078 行)"这种自相矛盾的头部。对外说行数时取两者
    # 的较大值;`lines` 字段仍保持 wc -l 口径(与 master 的 `wc -l` 回显一致)。
    shown_total = max(total_lines, n)
    returned = 0          # 真正返回的行数(用户/模型据此判断"我拿到多少")
    if start >= n:
        # 空窗口是最没用的回答:头部写着区间、正文空,调用方无法判断是"文件空了"
        # 还是"我要的位置不存在"。明确告诉它总行数与有效 offset。
        return {"content": ("📄 %s · offset=%d 超出文件末尾(共 %d 行),未返回内容。"
                            "offset 从 1 开始;读最后一段请用 offset=%d。"
                            % (path, offset, shown_total, max(1, n))),
                "lines": total_lines, "returned": 0}
    if limit > 0:
        window = lines[start:start + limit]
        returned = len(window)
        end = offset + returned - 1
        out = "".join("%6d│ %s\n" % (offset + i, ln) for i, ln in enumerate(window))
        # 头部必须报**实际**区间:旧版恒写 offset+limit-1,到 EOF 被提前收窄时就成了谎话
        # (实测"第 10-10008 行(共 197 行)")。满窗时两种写法完全一致,parity 不受影响。
        if len(window) < limit:
            head = "第 %d-%d 行(共 %d 行;已到文件末尾)" % (offset, end, shown_total)
        else:
            head = "第 %d-%d 行(共 %d 行)" % (offset, end, shown_total)
        # ⚠ 头部用**字面** `\n`(反斜杠+n):复刻 bash 双引号串 `"...:\n\n$out"` ——
        #   bash 双引号里 `\n` 是两个字符。原实现即如此(疑似历史笔误,应为 $'\n'),
        #   迁移只做等价、不改行为;要修另开一刀。
        content = "📄 %s · %s:\\n\\n%s" % (path, head, out.rstrip("\n"))
    elif read_max > 0 and sum(len(x) + 1 for x in lines[start:]) > read_max:
        parts = []
        acc = 0
        for i in range(start, n):
            acc += len(lines[i]) + 1
            if acc > read_max:
                break
            parts.append("%6d│ %s\n" % (i + 1, lines[i]))
        returned = len(parts)
        end = start + returned
        content = ("📄 %s · 第 %d-%d 行(共 %d 行 / %d 字符),文件过大仅返回前半部分"
                   "(本次 %d 行)。继续请用 offset=%d 读取下一段:\\n\\n%s"
                   % (path, offset, end, shown_total, total_chars, len(parts),
                      end + 1, "".join(parts).rstrip("\n")))
    else:
        returned = n - start
        out = "".join("%6d│ %s\n" % (i + 1, ln) for i, ln in enumerate(lines[start:], start))
        if offset > 1:
            content = "📄 %s · 第 %d-%d 行(共 %d 行):\\n\\n%s" % (
                path, offset, n, shown_total, out.rstrip("\n"))
        else:
            content = "📄 %s · 共 %d 行:\\n\\n%s" % (path, shown_total, out.rstrip("\n"))
    return {"content": content, "lines": total_lines, "returned": returned,
            "shown": shown_total}



def cmd_read(argv) -> int:
    a = list(argv) + ["", "", "", "", ""]
    out = read_fmt(a[0], a[1], a[2], a[3], a[4])
    _contract.write_text(json.dumps(out, ensure_ascii=False) + "\n")
    return _contract.EXIT_OK


_COMMANDS = {
    "read": cmd_read,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "filefmt: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
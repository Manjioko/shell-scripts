#!/usr/bin/env python3
"""W11：权限会话态的形状维护（纯函数 + CLI，**尚未接线**）。

为什么是"形状"而不是"新类型"
------------------------------
`granted_files` 在 bash 里是**数组**，但 `is_granted()` 每次都临时把它拼成
"换行分隔的 `mode|path` 列表"喂给 `py_run authz is_granted`（`py_authz` 20 条断言守着）。
也就是说**形参早就是字符串**，只是 bash 侧还持着数组。

所以搬移 = 让 bash 直接持有这份字符串（本节这些函数负责维护它），
**`py_authz` / `py_pathrule` 的接口一个字符都不用改** —— 已收口模块不动，是这一步最大的安全点。

形状约定（沿用现状，不重新编码）
--------------------------------
· 一行一条 `mode|path`，`mode ∈ {r, rw}`，`rw ⊃ r`；
· 顺序有意义（先授权的先命中），**去重时不打乱顺序**；
· 空列表 = 空字符串（不是 `"\n"`）。
"""
import sys


def parse(text):
    """'r|/a\\nrw|/b' → [('r','/a'), ('rw','/b')]。空串 → []。坏行（无 `|`）**跳过**并留在原位语义外。"""
    out = []
    for line in (text or "").split("\n"):
        if not line:
            continue
        if "|" not in line:
            continue          # 坏行直接跳过：历史实现里无 `|` 的旧格式一律按最小权限 r 处理，
                              # 这里更保守 —— 不猜，直接不纳入（调用方本就不该产生这种行）
        mode, path = line.split("|", 1)
        out.append((mode, path))
    return out


def render(items):
    """[('r','/a')] → 'r|/a'。空 → ''。"""
    return "\n".join("%s|%s" % (m, p) for m, p in items)


def grant(text, mode, path):
    """追加一条授权，**幂等**（同 mode+path 已存在则不重复），保持原有顺序。

    注意：这里**不做去重以外的任何规范化** —— 物理规范化形式（`cd … && pwd -P`）由调用方
    另追加一条，因为那需要访问文件系统，纯函数不该碰 IO。
    """
    items = parse(text)
    if (mode, path) not in items:
        items.append((mode, path))
    return render(items)


def has(text, mode, path, mode_wanted=None):
    """是否已授权。`rw` 授权满足 `r` 请求（rw ⊃ r）。仅供测试/排查，生产判定在 authz.py。"""
    want = mode_wanted or mode
    for m, p in parse(text):
        if p != path:
            continue
        if m == want or m == "rw":
            return True
    return False


# ── CLI（bash 接线用：append 读 stdin 的当前状态，参数给 mode/path）─────────────
def cmd_append(argv) -> int:
    mode = argv[0] if argv else ""
    path = argv[1] if len(argv) > 1 else ""
    if mode not in ("r", "rw") or not path:
        sys.stderr.write("state: 参数非法(需要 mode=r|rw 与 path)\n")
        return 2
    sys.stdout.write(grant(sys.stdin.read(), mode, path))
    return 0


_COMMANDS = {"append": cmd_append}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("state: 未知子命令 %r(可用: %s)\n"
                         % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS))))
        return 2
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


"""pathutil —— 路径归一族(迁移自 wchat.sh 的 expand_tilde / norm_path / resolve_path)。

**纯逻辑边界**:只做"字符串预处理 + 对**最近的已存在祖先**做物理归一(cd+pwd -P =
os.path.realpath,两者在 macOS firmlink 上实测逐字节一致)"。**不含**任何裁决/IO/审计 ——
边界与权限判定在 bash 的 hard_deny_path / in_root_exempt / path_access(读全局多,留 bash)。

退出码(见 `_contract`):
  0  成功,归一结果写 stdout
  1  业务性失败(空入参 / 缺失段含 `..` / 祖先不可达 / 结果越出 WORK_ROOT)—— 调用方**不回退**
  2  未知子命令

用法(三个入参都显式传入,不在 python 侧读环境):
  python3 -m wchat_py.pathutil expand_tilde  <p> <home> <work_root>
  python3 -m wchat_py.pathutil norm_path     <p> <home> <work_root>
  python3 -m wchat_py.pathutil resolve_path  <p> <home> <work_root>
"""

from __future__ import annotations

import os
import posixpath
import sys

from . import _contract


def expand_tilde(p: str, home: str, work_root: str = "") -> str:
    """对齐 bash:仅 `~` 与 `~/` 前缀展开;其余原样(含 `~user` 不处理)。

    `work_root` 仅为统一调用口径(`_run` 三个入参),本函数不用。
    """
    if p == "~":
        return home
    if p.startswith("~/"):
        return home + "/" + p[2:]
    return p


def _dirname(p: str) -> str:
    """对齐 POSIX `dirname`(仅用于绝对路径)。"""
    if p != "/":
        p = p.rstrip("/") or "/"
    return posixpath.dirname(p) or "/"


def _basename(p: str) -> str:
    """对齐 POSIX `basename`:根目录的 basename 是 `/`(python 会得到空串)。"""
    if p == "/":
        return "/"
    p = p.rstrip("/") or "/"
    return posixpath.basename(p)


def _canon(d: str):
    """`cd "$d" && pwd -P` 的等价:物理路径(消解符号链接/firmlink)。"""
    try:
        return os.path.realpath(d)
    except OSError:
        return None


def norm_path(p: str, home: str, work_root: str):
    """见 wchat.sh norm_path。返回归一后的绝对路径;失败返回 None。"""
    if p == "":
        return None
    p = expand_tilde(p, home)
    if not p.startswith("/"):
        p = work_root + "/" + p
    # 存在的目录:直接物理归一(内核消解 .. 与符号链接)
    if os.path.isdir(p):
        c = _canon(p)
        if c is not None:
            return c
    # 字符串层面只去掉 "/./" 与结尾 "/."
    p = p.replace("/./", "/")
    if p.endswith("/."):
        p = p[:-2]
    d = _dirname(p)
    base = _basename(p)
    missing = ""
    while not os.path.isdir(d):
        if d == "/":
            break
        missing = _basename(d) + "/" + missing
        d = _dirname(d)
    for seg in missing.split("/"):
        if seg == "..":
            return None
    canon = _canon(d)
    if canon is None:
        return None
    if base in (".", ".."):          # 尾段是 . / .. 的兜手滑拼写
        return canon
    prefix = canon[:-1] if canon.endswith("/") else canon
    return "%s/%s%s" % (prefix, missing, base)


def resolve_path(p: str, home: str, work_root: str):
    """见 wchat.sh resolve_path:归一 + 强制落在 work_root 内。失败返回 None。"""
    if p == "":
        return None
    p = expand_tilde(p, home)
    if not p.startswith("/"):
        p = work_root + "/" + p
    # 尾斜杠会让 basename 吃掉最后一段(去掉一个即可)
    if p != "/" and p.endswith("/"):
        p = p[:-1]
    d = _dirname(p)
    base = _basename(p)
    missing = ""
    while not os.path.isdir(d):
        if d == "/":
            return None
        missing = _basename(d) + "/" + missing
        d = _dirname(d)
    for seg in missing.split("/"):
        if seg == "..":
            return None
    canon = _canon(d)
    if canon is None:
        return None
    prefix = canon[:-1] if canon.endswith("/") else canon
    tgt = "%s/%s%s" % (prefix, missing, base)
    if not (tgt == work_root or tgt.startswith(work_root + "/")):
        return None
    return tgt


def in_root_exempt(np: str, work_root: str, home: str) -> bool:
    """见 wchat.sh in_root_exempt:`np` 是否落在工作根内(边界豁免)。**纯字符串判定**。

    两条例外(边界本身失效,整体不豁免):工作根==HOME 或 ==/。
    """
    if np == "":
        return False
    if work_root == home or work_root == "/":
        return False
    return np == work_root or np.startswith(work_root + "/")


def _run(fn, argv) -> int:
    a = list(argv) + ["", "", ""]
    r = fn(a[0], a[1], a[2])
    if r is None:
        return _contract.EXIT_FAIL
    _contract.write_text(r)
    return _contract.EXIT_OK


def cmd_in_root_exempt(argv) -> int:
    a = list(argv) + ["", "", ""]
    return (_contract.EXIT_OK if in_root_exempt(a[0], a[1], a[2])
            else _contract.EXIT_FAIL)


_COMMANDS = {
    "expand_tilde": lambda argv: _run(expand_tilde, argv),
    "norm_path": lambda argv: _run(norm_path, argv),
    "resolve_path": lambda argv: _run(resolve_path, argv),
    "in_root_exempt": cmd_in_root_exempt,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "pathutil: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_ENV
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
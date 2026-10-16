"""authz —— 会话级授权判定(迁移自 wchat.sh 的 is_granted / auto_allow_hit)。

**纯判定**:会话态(`granted_files` / `auto_allow_ro` / `auto_allow_rw`)**全部由调用方传入**
(单一出处仍在 bash),Python 侧不持有状态。这样 `path_access` 的"判定部分"将来可纯化,
而"编排部分"(审计/弹确认/备份/熔断)留在 bash。

退出码(见 `_contract`):0=命中(授权/白名单内) / 1=未命中 / 2=未知子命令。

用法(列表参数用换行分隔):
  python3 -m wchat_py.authz is_granted <mode> <home> <work_root> <granted_nl>   # stdin=path
  python3 -m wchat_py.authz auto_allow_hit <mode> <home> <work_root> <ro_nl> <rw_nl>
"""

from __future__ import annotations

import posixpath
import os
import sys

from . import _contract
from . import pathutil


def is_granted(p: str, mode: str, granted, home: str, work_root: str) -> bool:
    """`p` 是否命中会话级授权名单。名单项格式 `mode|path`(`rw` 覆盖 `r`)。"""
    canon = ""
    d = posixpath.dirname(p)
    if os.path.isdir(d):
        canon = pathutil.norm_path(d, home, work_root) or ""
    base = posixpath.basename(p)
    for g in granted:
        gmode, _, gpath = g.partition("|")
        if not (gmode == mode or gmode == "rw"):
            continue
        if gpath == p:
            return True
        if canon and gpath == canon + "/" + base:
            return True
        # 子树命中:名单里是某个已存在目录,请求路径在它下面
        if os.path.isdir(gpath) and p.startswith(gpath + "/"):
            return True
        if canon and os.path.isdir(gpath) and canon.startswith(gpath + "/"):
            return True
    return False


def auto_allow_hit(p: str, mode: str, allow_ro, allow_rw, home: str, work_root: str) -> bool:
    """`p` 是否在 `--allow`/`--allow-write` 名单内。rw 只认 rw 名单;r 认 ro∪rw。"""
    ep = pathutil.norm_path(p, home, work_root) or p
    entries = allow_rw if mode == "rw" else (list(allow_ro) + list(allow_rw))
    for e in entries:
        if ep == e or ep.startswith(e + "/"):
            return True
    return False


def _nl(s: str):
    return [x for x in s.split("\n") if x]


def cmd_is_granted(argv) -> int:
    a = list(argv) + [""] * 4
    p = _contract.read_stdin_text()
    ok = is_granted(p, a[0], _nl(a[3]), a[1], a[2])
    return _contract.EXIT_OK if ok else _contract.EXIT_FAIL


def cmd_auto_allow_hit(argv) -> int:
    a = list(argv) + [""] * 5
    p = _contract.read_stdin_text()
    ok = auto_allow_hit(p, a[0], _nl(a[3]), _nl(a[4]), a[1], a[2])
    return _contract.EXIT_OK if ok else _contract.EXIT_FAIL


_COMMANDS = {
    "is_granted": cmd_is_granted,
    "auto_allow_hit": cmd_auto_allow_hit,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("authz: 未知子命令 %r\n" % (argv[0] if argv else ""))
        return _contract.EXIT_ENV
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


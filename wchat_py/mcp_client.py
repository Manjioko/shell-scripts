"""mcp_client —— MCP 客户端"与 mcporter 打交道"的那一层(迁移自 wchat.sh)。

只放**纯逻辑**:从噪声输出里抽 JSON 主体、名字校验等。真正跑进程、管墙钟/中断的
`mcp_cli`、以及权限/审计/上屏的壳层,留 bash(见 docs/migration-to-python.md 的
W5 边界)。

退出码(见 `_contract`):
  0  成功,结果写 stdout
  1  未找到(业务性结果,不回退)
  2  未知子命令

用法:
  python3 -m wchat_py.mcp_client extract_json [cap]   # stdin=命令输出 -> stdout=JSON 主体
"""

from __future__ import annotations

import json
import sys

from . import _contract

EXTRACT_CAP = 25          # 最多逐行试 25 个起点(防在大段非 JSON 输出上跑成一屏 subprocess)


def extract_json(all_text: str, cap: int = EXTRACT_CAP):
    """从"命令输出 + 夹进来的 stderr 噪声"里抽出 JSON 主体;抽不到返回 None。

    不能简单"取第一个 { 之后":mcporter 的 --json 是**带缩进多行**,而它的告警行又常以
    `[` 开头,两种取法都会切错。做法是确定性地逐行往后试起点,第一个能整体解析的
    后缀就是 JSON 主体;上限 cap 行。
    ⚠ 对齐 `jq -e .`:解析结果是 null/false 时 jq 退出 1,故也不算命中。
    """
    content = all_text + "\n"                 # 对齐 bash 的 printf '%s\n'
    lines = content.split("\n")[:-1]          # 末尾换行不产生额外记录
    n = len(lines)
    if n > cap:
        n = cap
    for i in range(1, n + 1):
        txt = "\n".join(lines[i - 1:])        # 对齐 $(...) 剥尾换行
        if not txt:
            continue
        try:
            v = json.loads(txt)
        except Exception:
            continue
        if v is None or v is False:           # jq -e 对 null/false 退出非 0
            continue
        return txt
    return None


def cmd_extract_json(argv) -> int:
    cap = EXTRACT_CAP
    if argv:
        try:
            cap = int(argv[0])
        except (TypeError, ValueError):
            cap = EXTRACT_CAP
    out = extract_json(_contract.read_stdin_text(), cap)
    if out is None:
        return _contract.EXIT_FAIL
    _contract.write_text(out)
    return _contract.EXIT_OK


_COMMANDS = {
    "extract_json": cmd_extract_json,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "mcp_client: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


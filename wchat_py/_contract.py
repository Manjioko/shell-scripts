"""迁移通道的共享契约 —— 退出码、stdin/stdout 读写。

**本文件是"接缝契约"的唯一落点**(docs/migration-to-python.md §三)。
任何模块不得自己另立退出码或 stdout 格式;要加东西,改这里并升
`wchat_py.CONTRACT_VERSION`。

退出码(与既有 input_helper.py 的语义一致,不另立):

  0  EXIT_OK     成功,数据在 stdout
  1  EXIT_FAIL   业务性失败(入参非法 / 对象不存在);调用方读 stdout 里的说明,**不回退**
  2  EXIT_ENV    环境异常(缺 python3 / 缺依赖 / 无法工作);调用方**回退 bash 旧实现**

≥3 由各模块自定义,必须在模块 docstring 里写清。
"""

from __future__ import annotations

import sys

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ENV = 2

__all__ = [
    "EXIT_OK",
    "EXIT_FAIL",
    "EXIT_ENV",
    "read_stdin_bytes",
    "read_stdin_text",
    "write_bytes",
    "write_text",
]


def read_stdin_bytes() -> bytes:
    """一次性读干 stdin 的原始字节(不做解码:调用方要按字节处理就按字节)。"""
    return sys.stdin.buffer.read()


def read_stdin_text() -> str:
    """读 stdin 并按 UTF-8 宽松解码(坏字节降级为 U+FFFD,不整篇崩)。"""
    return sys.stdin.buffer.read().decode("utf-8", "replace")


def write_bytes(data: bytes) -> None:
    """结果写 stdout 原始字节。只调用一次;日志一律走 stderr。"""
    sys.stdout.buffer.write(data)


def write_text(s: str) -> None:
    """结果写 stdout 文本(UTF-8)。"""
    sys.stdout.buffer.write(s.encode("utf-8"))


#!/usr/bin/env python3
"""W12-4d：`messages` 的**文件 store**（单一所有者：文件是唯一真相，bash 只拿路径）。

⚠ 尚未接线：目前 `messages` 仍是 bash 全局字符串。这里先把**语义**定下来，
接线时 bash 的读写改成 `py_run store …`，调用点形状不变。

设计的两个要点
--------------
1. **回滚 = 截断到记录的长度**，不是拷贝整份历史。
   工具调用期间对 messages 只有**追加**（`add_message` 只往末尾加），所以"调用前"的状态
   恰好是当前数组的前 N 条。于是快照只要记 N（O(1)），恢复就是 `messages[:N]`。
   —— 前提不变量：**快照与恢复之间不得发生对旧消息的改写**（压缩/注入都在 turn 边界做，
   不在工具调用内部）。若哪天有代码在工具调用内部改旧消息，这条会失效，必须改用内容快照。
   这条比"整份复制"省掉每次工具调用的 MB 级拷贝，也没有"快照 id 分配/回收"的状态。

2. **写文件是原子的**：同目录临时文件 + `os.replace`，避免中途崩溃留下半截 JSON
   （半截文件会让下次启动读不出历史 —— 而那正是本项目最贵的一类故障）。
"""
import json
import os
import tempfile


def load(path, init=None):
    """读 messages。文件不存在 → 返回 `init`（默认 `[]`）；损坏 → **抛错**（不静默返回空）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return list(init) if init is not None else []
    if not isinstance(data, list):
        raise ValueError("messages store 不是数组: %r" % type(data).__name__)
    return data


def save(path, messages):
    """原子写。同目录临时文件 → fsync → `os.replace`。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".msgs_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def snapshot(messages) -> int:
    """回滚快照 = 当前长度（见模块头第 1 点）。O(1)，不拷贝历史。"""
    return len(messages)


def restore(messages, n: int):
    """恢复到快照时的长度。`n` 非法（越界/非整数）→ 原样返回，**不静默截断到错的位置**。"""
    if not isinstance(n, int) or n < 0 or n > len(messages):
        return messages
    return messages[:n]


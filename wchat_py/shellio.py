#!/usr/bin/env python3
"""跨进程回传 bash 变量的**安全编码**（W12-5 收尾项的地基）。

问题：`hard_deny_cmd` 用 bash 全局通信（`hd_rule` / `hd_msg` / `HD_SOFT` / `HD_PAYLOAD`），
子进程设不了 bash 全局。迁 Python 后必须把这些值**编码成 shell 赋值语句**返回，
由 bash 侧 `eval` 回去。

为什么不用 base64：macOS 的 `base64 -D` 与 GNU 的 `-d` 不一致，多一个平台坑；
而 `shlex.quote` 是纯 Python、POSIX 安全、零外部依赖。

为什么必须严格引用：`hd_msg` 是**自由文本**（含拒绝理由、路径、可能的引号/换行/`$`/反引号），
一旦引错，`eval` 会把其中的 `$(...)` 当命令执行 —— 那是注入，不是显示错误。

用法（bash 侧）：
    eval "$(py_run hd cmd "$cmd")"
"""
import shlex


def assign(values) -> str:
    """把 {name: value} 编成可 `eval` 的 shell 赋值语句（一行一条）。

    - 值一律 `shlex.quote`，**不做任何类型推断**（全部当字符串；数字也走引号，无害）；
    - 键必须是合法的 shell 变量名，否则抛错（不允许静默生成坏语句）。
    """
    out = []
    for k in values:
        if not isinstance(k, str) or not k.replace("_", "").isalnum() or k[0].isdigit():
            raise ValueError("非法变量名: %r" % (k,))
    for k, v in values.items():
        if not isinstance(v, str):
            v = "" if v is None else str(v)
        out.append("%s=%s" % (k, shlex.quote(v)))
    return "\n".join(out)


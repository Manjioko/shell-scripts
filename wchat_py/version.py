"""版本号与**最低 Python 版本**的唯一出处。

发布脚本(packaging/release.py)只改 `__version__`;`wchat --version` 与 Formula 的
version 都从它读,避免"包是 0.3.0、Formula 还写 0.2.0"这种对不上。

## 为什么最低 Python 版本要写在这里

`wchat` 的 shebang 是 `#!/usr/bin/env python3` —— 它只能表达"要个 python3",
**表达不了"要 3.9 以上"**,更表达不了 brew 装的那份 3.12。于是同一条代码有三个入口,
选到的解释器可能完全不同:

| 入口 | 解释器从哪来 |
|---|---|
| `brew install` 后的 `wchat` | Formula 生成的 wrapper:`exec <venv>/bin/python <libexec>/wchat` —— **shebang 根本没被读** |
| 仓库里 `./wchat` | 内核读 shebang → PATH 上第一个 `python3`(本机实测是 CommandLineTools 的 **3.9.6**) |
| 自举兜底建 venv | `bootstrap._base_python()` 挑一个能跑的解释器 |

前两条如果落在不同版本上,行为差异会以"我这能跑、brew 装的不行"(或反过来)的形式出现,
而且**没有任何一处会报错**。所以把版本要求写成数据(`MIN_PYTHON`),由 bootstrap 在
启动时强制校验:低于它就去找一个合规的解释器 re-exec,找不到就明说,不静默降级。
"""
__version__ = "0.4.0"

# 最低 Python 版本(major, minor)。
# 2026:下限由 3.9 抬到 3.10 —— 唯一的硬驱动是**文档读取**(.docx/.pdf→Markdown 用
# markitdown,它 requires_python >=3.10)。历史上下限锁 3.9 只是因为开发机默认 3.9;
# 而 wchat 实际经 brew 用固定版本 Python(Formula 用 python@3.12)运行,不依赖用户默认
# 解释器,所以抬这一档没有兼容负担。抬到 3.10 后仓库直跑也允许用 3.10+ 语法
# (match、isinstance 里的 `X | Y`)。
MIN_PYTHON = (3, 10)
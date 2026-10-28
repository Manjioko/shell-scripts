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
__version__ = "0.2.7"

# 最低 Python 版本(major, minor)。代码在 3.9 上实测可用(全包 compileall + 各子命令),
# 所以下限是 3.9,而不是 brew 依赖的 3.12:下限说"能跑",brew 说"我们发布/测试的是哪个"。
# ⚠ 用 3.10+ 语法(match、isinstance 里的 `X | Y`)之前先抬这个数,否则仓库直跑会在
# 一个跟语法毫不相干的报错位置上炸。
MIN_PYTHON = (3, 9)


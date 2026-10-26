"""wchat_py 的启动器 —— 被 `py_run` 以**绝对路径**调用。

为什么不用 `python3 -m wchat_py.<mod>`:
  `-m` 会把 **cwd** 放进 sys.path[0],于是用户在任意目录启动 wchat 时,若那个目录
  恰好有个 `wchat_py/`(或 `wchat_py.py`),就会**遮蔽**我们的包 —— 生产上是"换个目录
  就加载到别人的同名模块",测试上是"负对照塞的坏副本被仓库原件盖掉"(实测踩过,
  tests/hardwall_py_test.sh 的接线证明当场变假绿)。

这里把**包父目录**插到 sys.path 最前,保证无论 cwd 是什么都加载本包;脚本式调用还
天然不带 cwd 进 sys.path。用法:
  python3 /abs/path/wchat_py/_run.py <module> [args...]
"""

import importlib
import os
import sys


def main(argv) -> int:
    if not argv:
        sys.stderr.write("_run: 缺模块名\n")
        return 2
    here = os.path.dirname(os.path.abspath(__file__))
    pkg_parent = os.path.dirname(here)
    sys.path.insert(0, pkg_parent)          # 优先于 cwd/其它路径
    try:
        mod = importlib.import_module("wchat_py." + argv[0])
    except Exception as e:                   # 模块缺失/导入错误 → 非 0,交调用方回退
        sys.stderr.write("_run: 无法导入 wchat_py.%s: %s\n" % (argv[0], e))
        return 2
    return mod.main(argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        # 终端 Ctrl+C 由调用方(父进程)负责"取消本轮";这里静默退 130 而不是
        # 打一段 traceback —— 迁移版用户实测:父子两份栈同时刷屏,看不出发生了什么。
        sys.exit(130)
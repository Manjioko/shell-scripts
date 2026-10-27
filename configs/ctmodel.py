#!/usr/bin/env python3
"""ctmodel —— `wchat model` 的兼容壳(逻辑只有一份,在 wchat_py/setup.cmd_model)。

保留这个文件是两个原因:
  1. 老用户的习惯与脚本里写着 `ctmodel qwen` —— 不能因为收纳主干就让它们坏掉;
  2. 发布包里的 `bin/ctmodel` 仍然指向这里,只是它现在 **exec `wchat model`**,
     所以不存在"两套实现漂移"的问题。

**不要在这里加逻辑**:加任何处理都等于同时存在两套实现,那正是本次要消灭的东西。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wchat_py import setup as _setup  # noqa: E402

if __name__ == "__main__":
    sys.exit(_setup.cmd_model(sys.argv[1:]))


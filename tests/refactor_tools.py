#!/usr/bin/env python3
"""§12 收口(4)：把 bash 的 tool_read_file/tool_write_file/tool_edit_file/tool_list_dir/tool_grep
函体替换为 fail-closed 桩（Python 已是默认活实现；此步删死代码，py 缺席时明确拒、不静默）。

安全：先备份 .scratch/wchat.pre_tools.bak；按 `name() {` 到其后首行顶格 `}` 定位；打印被换行数。
由人手动执行（平台终审拒 AI 改源码）：  python3 tests/refactor_tools.py && bash -n wchat.sh
"""
import os
import shutil

REPO = os.getcwd()
SRC = os.path.join(REPO, "wchat.sh")
BAK = os.path.join(REPO, ".scratch/wchat.pre_tools.bak")

NAMES = ["tool_read_file", "tool_write_file", "tool_edit_file", "tool_list_dir", "tool_grep"]


def stub(name):
    return ('%s() {\n'
            '    content="错误:工具 %s 的 bash 版已收口删除; Python 组件不可用, 本次不执行"\n'
            '    printf \' ⛔ %%s 组件不可用\\n\' %s >&2\n'
            '    return 0\n'
            '}' % (name, name, name))


def main():
    lines = open(SRC, encoding="utf-8").read().split("\n")
    shutil.copyfile(SRC, BAK)
    total = 0
    for name in NAMES:
        start = next((i for i, ln in enumerate(lines) if ln.startswith(name + "() {")), None)
        if start is None:
            print("跳过 %s(未找到)" % name)
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), None)
        if end is None:
            print("跳过 %s(无收尾)" % name)
            continue
        lines = lines[:start] + stub(name).split("\n") + lines[end + 1:]
        total += 1
        print("替换 %s(原 %d 行)" % (name, end - start + 1))
    open(SRC, "w", encoding="utf-8").write("\n".join(lines))
    print("共替换 %d 个函数；备份 %s" % (total, BAK))


if __name__ == "__main__":
    main()


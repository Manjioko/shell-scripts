#!/usr/bin/env python3
"""`-simplest` 极简模式的接线件(隐藏工具/思考展示,只留状态行 + 正文流式)。

设计要点(2026-11 用户需求):
  · **调用逻辑完全不变**,只改"展示"。不加 `-simplest` 时本模块所有函数都是 no-op,
    默认档(仍是 `-tf=off`)行为一字不改。
  · 状态机按**整轮**(一个用户输入 = 一轮):轮开始显示「正在处理中,请稍后...」,
    轮结束把同一条改成「处理完成」。工具轮循环**不**单独出状态(那是 -tf 的展示域,
    simplest 直接整体隐藏)。
  · 隐藏的是:思考文本、工具横幅、工具结果行、shell 输出箱、auto 放行提示等"背景信息";
    **保留**:模型正文(照常流式)、权限/审批 y·n、报错。

各渲染路径的接入点:
  · TUI(tui.py):reason 回调 no-op、本轮回调期把 sys.stdout 换成空 sink、
    状态行在内容区挂一个可 update 的 Static(begin→done)。
  · 旧交互(cli.py repl 段 / StreamBox):_emit_reason 跳过、工具输出走 quiet_stdout。
  · plain/管道:同上(无 TTY 时终端状态行无意义,由调用方按 isatty 决定是否打)。
"""
import os
import sys

WORKING_BASE = "正在处理中,请稍后"
WORKING = WORKING_BASE + "..."          # 静态兜底(非动画场景)
DONE = "处理完成"


def working_text(frame):
    """状态行动画帧:结尾点数 1→2→3→1 循环(frame 取模 3)。

    frame=0→'正在处理中,请稍后.'  1→'..'  2→'...'  3→'.'(回到 1 点)。
    用户在等待时看到点在不断跳动,不至于"太安静"。
    """
    return WORKING_BASE + "." * (int(frame) % 3 + 1)


def enabled():
    """是否极简档。单一真源在 render.tf_mode():`-tf=0`(或旧 `-simplest`/`WCHAT_SIMPLEST=1`)。"""
    try:
        from wchat_py.render import simplest_on
        return simplest_on()
    except Exception:
        return os.environ.get("WCHAT_SIMPLEST") == "1"


class _Null:
    """吞掉 stdout 的空 sink(isatty=False,写/刷都丢弃)。用于隐藏工具横幅/结果/输出箱。"""

    def write(self, s):
        return len(s) if s else 0

    def flush(self):
        pass

    def isatty(self):
        return False


class quiet_stdout:
    """`with quiet_stdout():` 期间 sys.stdout 换成空 sink(仅在 simplest 开启时生效)。

    未开启时是纯 no-op 上下文,不动 sys.stdout。这样调用方可以无条件 `with`,不写分支。
    """

    def __enter__(self):
        self._old = None
        if enabled():
            self._old = sys.stdout
            sys.stdout = _Null()
        return self

    def __exit__(self, *a):
        if self._old is not None:
            sys.stdout = self._old
        return False


def run_tool(run_tool_fn, name, args):
    """执行工具:simplest 下吞掉其 stdout(横幅/结果/输出箱),否则原样执行。返回工具输出。

    注意:只吞 **stdout**。工具的返回值(进上下文)不受影响;权限审批/报错走的是
    确认框与 stderr,不在此吞没范围内。
    """
    if not enabled():
        return run_tool_fn(name, args)
    with quiet_stdout():
        return run_tool_fn(name, args)
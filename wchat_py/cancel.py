"""流式取消通道(TUI 下 Ctrl+C 打断当前轮)。

TUI 把一轮 turn 放 worker 线程跑,Ctrl+C 是 Textual 的按键(不是 SIGINT,且 worker 里也装不了
signal handler)。所以用**线程安全的 Event** 当取消通道:主线程 Ctrl+C → set;worker 里
`_call_llm` 起一个守护 watcher 轮询,一旦置位就 kill 掉读流的子进程(读循环随即 EOF),并置
`_LLM_ABORT` → 抛 Aborted(带已流出的半截正文)→ orchestrate 保留已完成部分。

独立成 module 的原因:cli.py 有行数上限(分层结构闸门),且本模块**不 import cli**(纯 stdlib),
下层依赖方向单一。非 TUI 不走这条(仍用 SIGINT),零影响。
"""
import threading as _threading
import time as _time

_EVENT = _threading.Event()


def request():
    """请求取消(主线程 Ctrl+C 调用)。"""
    _EVENT.set()


def clear():
    """清除取消标志(每轮开始/结束时调用,避免误取消下一轮)。"""
    _EVENT.clear()


def cancelled():
    return _EVENT.is_set()


def spawn_watcher(p, set_abort):
    """起一个守护线程:轮询取消标志,一旦置位就 `p.kill()` 并 `set_abort()`(把 _LLM_ABORT 置真),
    使阻塞在 `p.stdout` 上的读循环因 EOF 立刻返回 → _call_llm 抛 Aborted。`p.poll() is not None`
    (进程已自行结束)时自行退出。抽成模块函数便于用假进程单测(不依赖真子进程/网络)。"""
    def _watch():
        while True:
            try:
                if p.poll() is not None:
                    return
            except Exception:
                return
            if _EVENT.is_set():
                try:
                    set_abort()
                except Exception:
                    pass
                try:
                    p.kill()
                except Exception:
                    pass
                return
            _time.sleep(0.05)
    try:
        t = _threading.Thread(target=_watch, daemon=True)
        t.start()
        return t
    except Exception:
        return None


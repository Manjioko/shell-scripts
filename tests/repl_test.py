#!/usr/bin/env python3
"""repl.run_repl 自检：读行→classify→分发（不联网，用假 stream/handler）。末行 RESULT ...。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.repl import run_repl  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


# 1) text 走 on_text，元命令走 on_meta，空行忽略，-q 退出
texts, metas = [], []
stream = io.StringIO("hi there\n\n-u\nfoo\n-q\nnever\n")
rc = run_repl(stream, lambda t: texts.append(t), lambda a, g: metas.append(a) or True)
ck("rc=0", rc == 0)
ck("text 分发顺序", texts == ["hi there", "foo"], repr(texts))
ck("元命令分发", metas == ["usage"], repr(metas))
ck("-q 后不再处理", "never" not in texts)

# 2) on_meta 返回 False 立即停止
texts2 = []
stream2 = io.StringIO("a\n-l\nb\n")
rc2 = run_repl(stream2, lambda t: texts2.append(t),
               lambda a, g: False)
ck("meta 返回False即停", texts2 == ["a"], repr(texts2))

# 3) 无 handler 时走 _default_meta 真实现:-skill 已落地(空对话给提示),占位兜底已无活路
out = io.StringIO()
old = sys.stdout
sys.stdout = out
try:
    run_repl(io.StringIO("-skill\n"), lambda t: None)
finally:
    sys.stdout = old
ck("-skill 默认handler走真实现(空对话提示)", "当前对话为空" in out.getvalue(), repr(out.getvalue()))

# 4) -h 默认 handler 调 cli.help_user（真打印，非占位）
out2 = io.StringIO()
old = sys.stdout
sys.stdout = out2
try:
    run_repl(io.StringIO("-h\n"), lambda t: None)
finally:
    sys.stdout = old
_hv = out2.getvalue()
ck("-h 打印 help(非占位)", len(_hv) > 50 and "尚未在 Python 主循环实现" not in _hv, repr(_hv[:60]))

# 5) -u 默认 handler 渲染 usage(含"消息条数")
out3 = io.StringIO()
old = sys.stdout
sys.stdout = out3
try:
    run_repl(io.StringIO("-u\n"), lambda t: None)
finally:
    sys.stdout = old
_uv = out3.getvalue()
ck("-u 渲染 usage", "消息条数" in _uv and "单价自标定" in _uv, repr(_uv[:40]))


def _cap(line):
    b = io.StringIO()
    o = sys.stdout
    sys.stdout = b
    try:
        run_repl(io.StringIO(line + "\n"), lambda t: None)
    finally:
        sys.stdout = o
    return b.getvalue()


ck("-cl 清屏转义", "\033[H\033[2J" in _cap("-cl"), repr(_cap("-cl")))
ck("-n 新会话提示", "已开始新会话" in _cap("-n"), repr(_cap("-n")))

# session 跨轮：run_repl + session_text_handler + 假 llm
from wchat_py.session import Session as _S, messages_list as _ml2  # noqa: E402
from wchat_py.repl import session_text_handler  # noqa: E402

_sess = _S()
_buf = io.StringIO()
_h = session_text_handler(_sess, lambda m: ("echo:" + m[-1]["content"], [], None), lambda n, a: "OUT", out=_buf)
run_repl(io.StringIO("p\nq\n"), _h)
ck("session 两轮 msg_count=4", _sess.msg_count == 4, str(_sess.msg_count))
ck("session 输出两行", _buf.getvalue() == "echo:p\necho:q\n", repr(_buf.getvalue()))
ck("session 上下文累积", [m["role"] for m in _ml2(_sess.messages_json)] == ["user", "assistant", "user", "assistant"])

# run_repl_loop 注入式 reader
from wchat_py.repl import run_repl_loop  # noqa: E402
_seq = iter(["x", "-l", "-q", "y"])
_tc, _mc = [], []
run_repl_loop(lambda: next(_seq, None), lambda t: _tc.append(t), lambda a, g: _mc.append(a) or True)
ck("loop reader: text 只到 -q 前", _tc == ["x"], str(_tc))
ck("loop reader: meta 捕获 -l", _mc == ["list_history"], str(_mc))

# write_final=False: 不整段输出, 但 session 更新
_b2 = io.StringIO(); _s2 = _S()
_h2 = session_text_handler(_s2, lambda m: ("T", [], None), lambda n, a: "", out=_b2, write_final=False)
run_repl(io.StringIO("z\n"), _h2)
ck("write_final=False 不输出", _b2.getvalue() == "", repr(_b2.getvalue()))
ck("write_final=False 仍更新 session", _s2.msg_count == 2)

# on_quit: -q 时调用一次
_q = iter(["x", "-q", "y"])
_qflag = []
run_repl_loop(lambda: next(_q, None), lambda t: None, None, lambda: _qflag.append(1))
ck("on_quit 在 -q 调用一次", _qflag == [1], str(_qflag))

# 输入助手起不来(rc=2,如换解释器后缺 prompt_toolkit)绝不能被当成"空行提交"无限重启。
# 旧实现 nxt() 对非 0/1/4 的退出码返回 ""，run_repl_loop 见空串即 continue ->
# 输入助手被无限次重启:屏幕无字、Ctrl+C 也打断不了(实测卡死事故)。这里用有界桩:
# 若回归,第 51 次调用直接抛错把死循环打断,测试才不会自己挂住。
from wchat_py import repl as _repl  # noqa: E402

class _R2:
    returncode = 2
    stdout = ""
    stderr = "ModuleNotFoundError: No module named 'prompt_toolkit'"

_calls = {"n": 0}


def _fake_run(*a, **k):
    _calls["n"] += 1
    if _calls["n"] > 50:
        raise RuntimeError("输入助手被无限重启(rc=2 被当成空行)")
    return _R2()


_orig_run2 = _repl.subprocess.run
_orig_stdin2 = sys.stdin
_errbuf = io.StringIO()
_orig_err2 = sys.stderr
_repl.subprocess.run = _fake_run
sys.stdin = io.StringIO("")          # input() 立即 EOF,让兜底路径正常结束
sys.stderr = _errbuf
try:
    _repl.run_interactive(lambda t: None)
    _hung = False
except RuntimeError:
    _hung = True
finally:
    _repl.subprocess.run = _orig_run2
    sys.stdin = _orig_stdin2
    sys.stderr = _orig_err2
ck("nxt rc=2 不无限重启(有界,只调用 1 次)", (not _hung) and _calls["n"] == 1,
   "调用 %d 次%s" % (_calls["n"], " · 死循环" if _hung else ""))
ck("nxt rc=2 说明原因并退回基础输入", "输入框不可用" in _errbuf.getvalue(), repr(_errbuf.getvalue()))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)
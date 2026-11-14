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

# 输入助手"非受控退出"绝不能被当成"空行提交"无限重启。旧实现只挡了 rc=2;
# 3 / -9 / 130 / 137 等(被信号杀:挂起/解挂、终端 detach 的 SIGHUP、休眠…)都被当空串 ->
# run_repl_loop 见空串即 continue -> 输入助手被无限次重启,每重启一次重绘一个空框
# (屏幕上叠出一堆 `❯ 问点什么`,实测)。下面用有界桩覆盖这些退出码:
# 若回归,第 51 次调用直接抛错把死循环打断,测试才不会自己挂住。
from wchat_py import repl as _repl  # noqa: E402


class _Rc:
    def __init__(self, rc, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _drive(rc, out=""):
    """把 nxt() 的输入盒子进程桩成固定退出码,数它被拉起几次(有界)。返回 (calls, stderr)。"""
    calls = {"n": 0}

    def _fake_run(*a, **k):
        calls["n"] += 1
        if calls["n"] > 50:
            raise RuntimeError("无限重启")
        return _Rc(rc, out, "boom")
    o_run, o_in, o_e = _repl.subprocess.run, sys.stdin, sys.stderr
    errb = io.StringIO()
    _repl.subprocess.run = _fake_run
    sys.stdin = io.StringIO("")          # input() 立即 EOF,让兜底路径正常结束
    sys.stderr = errb
    hung = False
    try:
        _repl.run_interactive(lambda t: None)
    except RuntimeError:
        hung = True
    finally:
        _repl.subprocess.run, sys.stdin, sys.stderr = o_run, o_in, o_e
    return calls["n"], errb.getvalue(), hung


for _rc in (2, 3, -1, -9, -13, 130, 137):
    _n, _err, _hung = _drive(_rc)
    ck("nxt rc=%-4s 不无限重启(只拉起 1 次)" % _rc, (not _hung) and _n == 1,
       "拉起 %d 次%s" % (_n, " · 死循环" if _hung else ""))
    ck("nxt rc=%-4s 说明原因并退回基础输入" % _rc, "退回基础输入" in _err, repr(_err))
# rc=0 却没有内容(异常):也不该当空行重开
_n0, _err0, _h0 = _drive(0, "")
ck("nxt rc=0 空内容不无限重启", (not _h0) and _n0 == 1, "拉起 %d 次" % _n0)
# rc=1(EOF)正常退出会话:只拉起 1 次,不报错
_eserr = io.StringIO()
_o_e = sys.stderr
sys.stderr = _eserr
_rc1_calls = {"n": 0}


def _run_eof(*a, **k):
    _rc1_calls["n"] += 1
    return _Rc(1)


_o_run = _repl.subprocess.run
_repl.subprocess.run = _run_eof
try:
    _r1 = _repl.subprocess.run  # noqa
    _repl.run_interactive(lambda t: None)
finally:
    _repl.subprocess.run = _o_run
    sys.stderr = _o_e
ck("nxt rc=1(EOF)只拉起 1 次、不报错", _rc1_calls["n"] == 1 and "退回基础输入" not in _eserr.getvalue(),
   "拉起 %d 次" % _rc1_calls["n"])

# Ctrl-F(rc=4)选中图片后,占位行必须回填进输入框(--default),不能被当成"选择取消"。
# 回归:WSL 下 powershell.exe 返回的是 Windows 盘符路径(C:\...)。旧实现直接把这串
# 返回给调用方,`os.path.isfile()` 在 WSL 里认不出 -> 当成取消 -> 占位行丢失 ->
# 用户看到的就是"弹窗能选图,选完输入框没有任何回显"。修法是 wslpath 转成 /mnt/...
import base64 as _b64  # noqa: E402
import tempfile as _tf  # noqa: E402

_png = _b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
_fd, _img = _tf.mkstemp(suffix=".png")
os.write(_fd, _png)
os.close(_fd)
try:
    _seen = []
    _state = {"n": 0}

    def _fake_pick_run(*a, **k):
        _state["n"] += 1
        _seen.append(list(a[0]) if a and isinstance(a[0], (list, tuple)) else [])
        if _state["n"] == 1:
            return _Rc(4, "")        # Ctrl-F,空缓冲
        if _state["n"] == 2:
            return _Rc(0, "x")       # 回填占位后再正常提交
        return _Rc(1)                # 之后 EOF 收尾

    _o_run_p = _repl.subprocess.run
    _o_pick = _repl._pick_file
    _repl.subprocess.run = _fake_pick_run
    _repl._pick_file = lambda: _img
    try:
        _repl.run_interactive(lambda t: None)
    finally:
        _repl.subprocess.run = _o_run_p
        _repl._pick_file = _o_pick
    _want = _repl._file_placeholder(_img)
    _second = _seen[1] if len(_seen) >= 2 else []
    ck("Ctrl-F 选中图片回填进输入框(--default 含占位行)", _want in _second, repr(_second))
finally:
    os.unlink(_img)

# Windows 盘符路径必须转成 WSL 可访问的 Linux 路径(否则 isfile 失败=白选)。
_w2l = _repl._win_to_wsl_path
ck("_win_to_wsl_path 盘符转换", _w2l(r"C:\Users\wzk\Pictures\a.png") == "/mnt/c/Users/wzk/Pictures/a.png",
   repr(_w2l(r"C:\Users\wzk\Pictures\a.png")))
ck("_win_to_wsl_path 小写盘符/中文/空格", _w2l(r"d:\data\图 1.jpg") == "/mnt/d/data/图 1.jpg",
   repr(_w2l(r"d:\data\图 1.jpg")))
ck("_win_to_wsl_path 空串", _w2l("") == "")
ck("_win_to_wsl_path 非盘符原样", _w2l("/tmp/x.png") == "/tmp/x.png")

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)
#!/usr/bin/env python3
"""repl 层自检(TUI 共用部分:默认 handler / session_text_handler / placeholders)。

仍被测的是**TUI 也在用**的部分:
  · `_default_meta` 真实现(help/usage/clear/new_session/skill_save/think_full);
  · `session_text_handler` 跨轮累积 + write_final + 输出;
  · `parse_placeholders`(图片占位 → input_images);
  · `_win_to_wsl_path` / `_file_placeholder` 纯转换。
不联网。末行 RESULT ...。
"""
import base64
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wchat_py.repl import (_default_meta, session_text_handler, parse_placeholders,  # noqa: E402
                           _win_to_wsl_path, _file_placeholder, _PLACE_IMG)
from wchat_py.session import Session, messages_list  # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def _cap(fn):
    b = io.StringIO()
    o = sys.stdout
    sys.stdout = b
    try:
        fn()
    finally:
        sys.stdout = o
    return b.getvalue()


# 1) _default_meta 真实现(元命令处理,与 TUI 的 on_meta → _default_meta 同路)
ck("-h 打印 help(真实现,非占位)", len(_cap(lambda: _default_meta("help", ""))) > 50)
ck("clear 清屏转义", "\033[H\033[2J" in _cap(lambda: _default_meta("clear", "")))
ck("usage 渲染(含 消息条数)", "消息条数" in _cap(lambda: _default_meta("usage", "", session=Session())))
_ck = _cap(lambda: _default_meta("new_session", "", session=Session()))
ck("new_session 提示", "已开始新会话" in _ck, repr(_ck[:40]))
ck("skill_save 空对话提示", "当前对话为空" in _cap(lambda: _default_meta("skill_save", "", session=Session())))
ck("think_full on 提示含'整段'", "整段" in _cap(lambda: _default_meta("think_full", "on")))

# 2) session_text_handler:跨轮累积 + 输出(直接调用 handler)
_sess = Session()
_buf = io.StringIO()
_h = session_text_handler(_sess, lambda m: ("echo:" + m[-1]["content"], [], None),
                          lambda n, a: "OUT", out=_buf)
_h("p")
_h("q")
ck("session 两轮 msg_count=4", _sess.msg_count == 4, str(_sess.msg_count))
ck("session 每轮输出一行", _buf.getvalue() == "echo:p\necho:q\n", repr(_buf.getvalue()))
ck("session 上下文累积(user/assistant 交替)",
   [m["role"] for m in messages_list(_sess.messages_json)] == ["user", "assistant", "user", "assistant"])

# 3) write_final=False:不整段输出,但 session 仍更新
_b2 = io.StringIO()
_s2 = Session()
_h2 = session_text_handler(_s2, lambda m: ("T", [], None), lambda n, a: "", out=_b2, write_final=False)
_h2("z")
ck("write_final=False 不输出", _b2.getvalue() == "", repr(_b2.getvalue()))
ck("write_final=False 仍更新 session", _s2.msg_count == 2, str(_s2.msg_count))

# 4) parse_placeholders:图片占位行 → 图片路径列表(供 TUI/session 读进 input_images)
_png = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
_fd, _img = tempfile.mkstemp(suffix=".png")
os.write(_fd, _png)
os.close(_fd)
try:
    _txt, _imgs = parse_placeholders("看图\n" + _PLACE_IMG + _img + "\u200b")
    ck("parse_placeholders 抽出图片占位路径", _imgs == [_img], repr(_imgs))
    ck("parse_placeholders 普通文本行保留", "看图" in _txt, repr(_txt))
finally:
    os.unlink(_img)

# 5) _win_to_wsl_path
ck("_win_to_wsl_path 盘符转换", _win_to_wsl_path(r"C:\Users\a\a.png") == "/mnt/c/Users/a/a.png")
ck("_win_to_wsl_path 空串", _win_to_wsl_path("") == "")
ck("_win_to_wsl_path 非盘符原样", _win_to_wsl_path("/tmp/x.png") == "/tmp/x.png")

# 6) _file_placeholder:图片/文本/跳过 三种 tag
ck("_file_placeholder 图片 tag", _file_placeholder("/x.png").startswith(_PLACE_IMG), repr(_file_placeholder("/x.png")))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)
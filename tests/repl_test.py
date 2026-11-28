#!/usr/bin/env python3
"""repl 层自检(TUI 共用部分:默认 handler / session_text_handler / placeholders)。

仍被测的是**TUI 也在用**的部分:
  · `_default_meta` 真实现(help/usage/clear/new_session/skill_save/think_full);
  · `session_text_handler` 跨轮累积 + write_final + 输出;
  · `parse_placeholders`(图片占位 → input_images);
  · `_win_to_wsl_path` / `_to_win_path` / `_ps_quote` / `_file_placeholder` 纯转换;
  · WSL 选择器与"打开文件管理器"的**分支优先级**(Windows 原生优先、zenity 只作回退)。
不联网、不真弹窗(对话框与 spawn 全部打桩)。末行 RESULT ...。
"""
import base64
import io
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wchat_py.repl as _repl  # noqa: E402
from wchat_py.repl import (_default_meta, session_text_handler, parse_placeholders,  # noqa: E402
                           _win_to_wsl_path, _to_win_path, _ps_quote,
                           _file_placeholder, _PLACE_IMG)
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


class _Restore(object):
    """临时改掉模块属性,离开 with 时还原(每个用例互不影响)。"""

    def __init__(self, mod, **attrs):
        self.mod, self.attrs, self.old = mod, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.old[k] = getattr(self.mod, k, None)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(self.mod, k, v)
        return False


class _Px(object):
    """假的 subprocess / shutil 模块:被测代码只用 run/Popen/which 这几样。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.DEVNULL = subprocess.DEVNULL
        self.SubprocessError = subprocess.SubprocessError


class _Shim(object):
    """假的 subprocess.run 返回值(只带被测代码读的字段)。"""

    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


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

# 5b) _to_win_path:WSL 路径 → Windows 路径(explorer.exe / 对话框定位目录用)
ck("_to_win_path /mnt/c 盘符", _to_win_path("/mnt/c/Users/a") == r"C:\Users\a")
ck("_to_win_path 小写盘符/中文/空格", _to_win_path("/mnt/d/data/图 1.jpg") == r"D:\data\图 1.jpg")
ck("_to_win_path 空串", _to_win_path("") == "")

# 5c) _ps_quote:PowerShell 单引号字面量(路径里的 ' 要写成 '')
ck("_ps_quote 转义单引号", _ps_quote("C:\\a'b") == "'C:\\a''b'", repr(_ps_quote("C:\\a'b")))

# 6) _file_placeholder:图片/文本/跳过 三种 tag
ck("_file_placeholder 图片 tag", _file_placeholder("/x.png").startswith(_PLACE_IMG), repr(_file_placeholder("/x.png")))


# 7) WSL 选择器的**分支优先级**(用户实报:WSL2 里弹出的是 Linux 的 GTK 框)
#    旧实现把 zenity 排在 Windows 原生对话框前面,装了 zenity 的机器永远命中前者。
#    这里把"对话框返回什么"和"zenity 是否被调用"都打桩,专测顺序,不真弹窗。
_fd, _pickfile = tempfile.mkstemp(suffix=".png")
os.write(_fd, b"\x89PNG\r\n")
os.close(_fd)
try:
    _calls = []
    _fake_zenity = lambda cmd, **kw: (_calls.append(list(cmd)),
                                      _Shim(stdout=_pickfile + "\n", returncode=0))[1]

    with _Restore(_repl, _is_wsl_env=lambda: True,
                  _wsl_pick_files=lambda multiselect=True, default_dir=None: [_pickfile],
                  subprocess=_Px(run=_fake_zenity),
                  shutil=_Px(which=lambda n: "/usr/bin/zenity")):
        _got = _repl._pick_files()
        ck("WSL 下 _pick_files 优先 Windows 原生多选框", _got == [_pickfile], repr(_got))
        ck("WSL 下不再走 zenity(顺序回归)", not any(c[0] == "zenity" for c in _calls), repr(_calls))

    # powershell.exe 不可用/框起不来 → None → 必须回退 zenity,不能当成"用户取消"
    _calls.clear()
    with _Restore(_repl, _is_wsl_env=lambda: True,
                  _wsl_pick_files=lambda multiselect=True, default_dir=None: None,
                  subprocess=_Px(run=_fake_zenity),
                  shutil=_Px(which=lambda n: "/usr/bin/zenity")):
        _got = _repl._pick_files()
        ck("WSL 原生框不可用时回退 zenity", _got == [_pickfile], repr(_got))
        ck("回退路径确实调了 zenity", any(c[0] == "zenity" for c in _calls), repr(_calls))

    # 用户取消([])不是"不可用"(None):不许回退,否则会二次弹窗
    _calls.clear()
    with _Restore(_repl, _is_wsl_env=lambda: True,
                  _wsl_pick_files=lambda multiselect=True, default_dir=None: [],
                  subprocess=_Px(run=_fake_zenity),
                  shutil=_Px(which=lambda n: "/usr/bin/zenity")):
        ck("WSL 用户取消不触发 zenity 二次弹窗", _repl._pick_files() == [] and not _calls, repr(_calls))

    # 选文件夹同一条顺序:WSL 先 Windows 原生,再 zenity/kdialog
    _calls.clear()
    with _Restore(_repl, _is_wsl_env=lambda: True,
                  _wsl_pick_dir=lambda default_dir=None: "/tmp/wsl-dir",
                  subprocess=_Px(run=_fake_zenity),
                  shutil=_Px(which=lambda n: "/usr/bin/zenity")):
        ck("WSL 下 _pick_dir 优先 Windows 原生选文件夹框",
           _repl._pick_dir("/home/x") == "/tmp/wsl-dir" and not _calls, repr(_calls))
finally:
    os.unlink(_pickfile)


# 8) 打开文件管理器:WSL2 必须弹 **Windows 资源管理器**,不是 xdg-open / WSLg 文件管理器
_spawned = []


def _fake_popen(cmd, **kw):
    _spawned.append(list(cmd))


with _Restore(_repl, _is_wsl_env=lambda: True,
              subprocess=_Px(Popen=_fake_popen),
              shutil=_Px(which=lambda n: r"C:\Windows\explorer.exe" if n == "explorer.exe" else None)):
    _repl.open_in_file_manager("/mnt/c/Users/wzk")
_calls = _spawned[-1] if _spawned else []
ck("WSL 下打开目录用 explorer.exe", bool(_calls) and _calls[0] == "explorer.exe", repr(_calls))
ck("WSL 下 /mnt/c 路径转成 C:\\ 交给 explorer",
   bool(_calls) and _calls[-1] == r"C:\Users\wzk", repr(_calls))
ck("WSL 下不再调用 xdg-open", all(c[0] != "xdg-open" for c in _spawned), repr(_spawned))

# WSL 内部路径(/home/...)要靠 wslpath -w 转成 \\wsl.localhost\... UNC,explorer 才认
_spawned[:] = []
_unc = r"\\wsl.localhost\Ubuntu-24.04\home\wzk\shell-scripts"
with _Restore(_repl, _is_wsl_env=lambda: True,
              subprocess=_Px(Popen=_fake_popen,
                             run=lambda cmd, **kw: _Shim(stdout=_unc + "\n")),
              shutil=_Px(which=lambda n: ("/mnt/c/Windows/explorer.exe" if n == "explorer.exe"
                                          else "/usr/bin/wslpath"))):
    _repl.open_in_file_manager("/home/wzk/shell-scripts")
_calls = _spawned[-1] if _spawned else []
ck("WSL 内部路径经 wslpath 转成 UNC 交给 explorer", bool(_calls) and _calls[-1] == _unc, repr(_calls))

_spawned[:] = []
with _Restore(_repl, _is_wsl_env=lambda: False,
              subprocess=_Px(Popen=_fake_popen),
              shutil=_Px(which=lambda n: None)):
    _repl.open_in_file_manager("/tmp")
ck("非 WSL 的 Linux 仍走 xdg-open", bool(_spawned) and _spawned[-1][0] == "xdg-open", repr(_spawned))

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)
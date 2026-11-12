#!/usr/bin/env python3
r"""wchat 输入助手:prompt_toolkit 多行输入框(替代 read -e)。

渲染输出到 /dev/tty(避免渲染序列混进结果),结果文本经 stdout 交回 bash。
输入语义:
  回车 = 发送整段(缓冲区全是空白时不动,不发空消息);
  行尾反斜杠 + 回车 = 续行(吃掉那个反斜杠,不发送)—— 与旧 read -e 流同义;
  命令(-x)不由本助手识别:单行文本交回 bash 后照旧按命令解析,多行一律按正文
  (判据在 wchat 侧 `[[ "$ptk_text" != *$'\n'* ]]`,本助手只负责"送出去");
  Ctrl+F 选择文件(退出码 4,携带缓冲区文本交 bash 走旧文件选择逻辑);
  Ctrl+G / Ctrl+V 粘贴剪贴板图片(截图直接附加,与 Ctrl+F 同管线统一转述);
  粘贴时若剪贴板装着图片(常见:截图后 Cmd+V/Ctrl+V),空粘贴自动嗅探并附加;
  Ctrl+C 清空当前行,Ctrl+R 清空整段;方向键/历史/括号粘贴/中文宽度原生支持。
退出码:0=提交(文本在 stdout),1=EOF/中断(无文本),2=环境异常(无 tty/缺库),
4=Ctrl+F 选择文件(缓冲区文本在 stdout)。
"""
import os
import re
import sys
import shutil
import argparse
import subprocess
import tempfile


def _term_cols_now():
    """当前终端宽度。优先问 prompt_toolkit 的 output(CPR 后是真值),
    拿不到时退回 COLUMNS/shutil —— 取窄一点也不要紧,规则线宁短勿折。"""
    try:
        from prompt_toolkit.application import get_app
        return int(get_app().output.get_size().columns) or 80
    except Exception:
        return shutil.get_terminal_size((80, 24)).columns or 80


def _rule(columns):
    """给定列数生成一条横线。留 1 列:在最后一列写字会触发自动折行。"""
    return "─" * max(1, int(columns) - 1)


def _rule_text(fd=None):
    """**框内**横线(输入助手活着的时候)不在这里取宽度,见 `_WidthControl`。

    这个函数只剩一个用途:输入框退出后由 `_close_box()` 补底边 —— 那时没有
    prompt_toolkit 的 app 可问,必须直接量 /dev/tty(prompt_toolkit 的
    DummyApplication 会报 80 列,在 70 列的终端上画出来就会折行)。
    """
    cols = None
    if fd is not None:
        try:
            cols = os.get_terminal_size(fd).columns
        except OSError:
            cols = None
    if not cols:
        cols = _term_cols_now()
    return _rule(cols)


def _width_control(make_text):
    """按**本帧渲染宽度**生成文本的控件(FormattedTextControl 子类)。

    为什么必须这样,而不是在回调里问 `get_app().output.get_size()`:
    后者是"活的"终端尺寸,拖拽改变窗口大小时它会在同一帧内就变,而 prompt_toolkit
    这一帧是按它自己缓存的 size 渲染的。两者一旦不一致,我们画出来的横线就比
    渲染宽度宽或窄 —— 宽出去的那截**不会被下一帧的 `ESC[K` 擦掉**(终端只擦到当前
    宽度),叠加回流就是用户看到的"输入框上部不规则残影"。
    实测证据:一次 resize 里上下两条横线宽度不同(上面 ~200 列、下面 ~170 列),
    只可能来自"同一帧内两次活查询拿到不同宽度"。

    `UIControl.create_content(width, height)` 收到的 `width` 就是这一帧真正渲染的
    列数 —— 唯一可信的来源。
    """
    from prompt_toolkit.layout.controls import FormattedTextControl

    class _C(FormattedTextControl):
        def __init__(self):
            super().__init__(text="")

        def create_content(self, width, height):
            self.text = make_text(width)
            return super().create_content(width, height)

    return _C()


# 活跃输入时的快捷键提示(只在输入框存在期间显示,提交后不重复打进历史)。
INPUT_HINT = "? 回车发送 · 行尾 \\ 续行 · Ctrl+F 选文件"


def _erase_stale_box(tty, height):
    """Ctrl+F 退出时擦掉残留在屏上的输入框。prompt_toolkit 退出会把
    光标停到框下一行、框本体留在屏上(render_as_done 的收尾帧);bash 侧
    随后弹文件对话框、不再打印任何输出,取消后立即重开会把新框画到旧框
    下一行——视觉上框"下滑一行",旧行是残影,退格永远删不到(真机复现)。
    这里按退出前记下的框高上移回框首、向下擦除,重开时新框恰好落在原位。
    框高缺失(理论不可达)时静默跳过,仅回到旧行为,不影响功能。"""
    if not height or height <= 0:
        return
    try:
        tty.write(f"\x1b[{height}A")   # 上移框高行,回框首(退出时光标在列 0)
        tty.write("\x1b[J")            # 自框首向下擦除,残留框一并清掉
        tty.flush()
    except OSError:
        pass


# ---------- 终端应答过滤(输入侧) ----------
# 现象:输入框里冒出 `]11;rgb:2828/2c2c/3434`,还会随回车一起提交给模型。
# 成因:子进程(gh / vim / mdcat / codex / opencode / tmux —— 本机 PATH 上 6 个可执行文件
# 内嵌 `\x1b]11;?` 这条查询)问终端"你背景色是什么",终端**必定回一段应答**;这些程序常常
# 发完查询就退出,应答留在 pty 输入队列里等**下一个读者**。它可以在很久之后才被读到
# (甚至不是同一个进程留下的),所以"谁查询"根本追不完 —— 必须在读者侧堵。
# wchat 的 bash 确认/编号读取早已过同一套判据(ask_read_line → tty_strip_responses),
# 唯独输入框走 prompt_toolkit:PTK 认不出 `ESC ]` 这类序列,把**正文当普通按键**逐个塞进
# 缓冲区 → 屏幕上就是用户看到的乱码,提交后污染发给模型的消息。
#
# 判据与 bash 版严格一致:**只丢"整段可识别的应答",判不动的一律放行**。少丢一条只是屏幕
# 上多串噪声,误吞一个按键是用户的输入被改 —— 后者严重得多。这里刻意不做 bash 那套
# "半截滞留等下片"(tty_hold):要 hold 就得把没判完的字节扣在手里,而它可能永远等不到
# 下一片(用户只按了一个 ESC / Alt+]),那是拿真按键换噪声。PosixStdinReader 单次读 1024
# 字节,25 字节的应答必然整段到达,分片在本路上不是实际风险。
# bracketed paste 区间内一律不过滤:粘贴正文里出现 ESC]… 是**数据**,剥它就是篡改用户输入。
_ESC = "\x1b"
_PASTE_START = _ESC + "[200~"
_PASTE_END = _ESC + "[201~"
# 四类无条件应答:OSC/DCS(BEL 或 ST 终止)、DA1/DA2、kitty 标志应答、DECRPM。
# 注意每条都要求 ESC 紧跟特征字节,方向键(ESC[A / ESC[1;5A)、F 键(ESC[15~ / ESCOP)、
# Alt+字符、kitty 真按键(ESC[13u,不带 ?)都不在其中。
_RESP_RE = re.compile(
    _ESC + r"[\]PX^_][^\x1b\x07]*(?:" + _ESC + r"\\|\x07)"   # OSC / DCS 应答
    + "|" + _ESC + r"\[[?>][0-9;]*c"                        # DA1 / DA2 属性应答
    + "|" + _ESC + r"\[\?[0-9;]*u"                          # kitty 键盘标志应答
    + "|" + _ESC + r"\[\?[0-9;]*\$y"                        # DECRPM 模式应答
)
# DSR 光标报告(ESC[行;列R)**不能无条件丢**:prompt_toolkit 自己就发 CPR 查询(3.0.52 见
# output/vt100.py:712 的 `\x1b[6n`),它的应答正是这个形态,是算光标位置要用的;一律丢掉
# 会让输入框打 "terminal doesn't support cursor position requests" 并重排错行(实测,
# 照抄 bash 侧判据就会踩这里)。但 vim 之类程序留下的**陈旧** DSR 又确实会变成乱码。
# 所以按配额放行:本进程向终端发了几次 CPR,就放几个 DSR 应答,多出来的判成残留。
# 助手是"一次输入一个进程",配额天然按框重置;万一查询超时没等到应答,配额留着,最坏
# 只是放行一条本可丢的 DSR(噪声)——方向仍是"宁漏一条应答,不误吞按键"。
_RESP_DSR_RE = re.compile(_ESC + r"\[\d{1,6};\d{1,6}R")
_CPR_QUERY = _ESC + "[6n"
_cpr_quota = [0]        # 已发出、还没等到应答的 CPR 次数
_in_paste = [False]     # 跨块保持:粘贴标记与正文可能不在同一次 read 里


def _drop_responses(s: str) -> str:
    """按判据丢掉整段应答(不跨块,粘贴中不调用)。DSR 按 CPR 配额放行。"""
    if not s or _ESC not in s:
        return s
    s = _RESP_RE.sub("", s)
    if not _RESP_DSR_RE.search(s):
        return s
    parts = []
    pos = 0
    for m in _RESP_DSR_RE.finditer(s):
        parts.append(s[pos:m.start()])
        if _cpr_quota[0] > 0:                   # 是本框问出来的 → 原样交给 PTK 解析
            _cpr_quota[0] -= 1
            parts.append(m.group())
        pos = m.end()                           # 配额用完还来 → 判成残留,丢
    parts.append(s[pos:])
    return "".join(parts)


def _counting_output(out):
    """包一层 write_raw,数出本进程向终端发了几个 CPR 查询(给 _cpr_quota 记账)。
    拿不到 write_raw(版本漂移)就原样返回 —— 记账失效最多退化成"DSR 一律判残留",
    不能因为它把渲染弄崩。"""
    orig = getattr(out, "write_raw", None)
    if orig is None:
        return out

    def _write_raw(data):
        if _CPR_QUERY in data:
            _cpr_quota[0] += data.count(_CPR_QUERY)
        return orig(data)

    out.write_raw = _write_raw
    return out


def _strip_terminal_responses(s: str) -> str:
    """丢掉整段可识别的终端应答,其余原样返回。粘贴区间内一律不碰。"""
    if not s:
        return s
    if _in_paste[0]:                                   # 上次分块时粘贴还没结束
        e = s.find(_PASTE_END)
        if e < 0:
            return s                                   # 整块仍是粘贴正文
        _in_paste[0] = False
        return s[:e + len(_PASTE_END)] + _strip_terminal_responses(
            s[e + len(_PASTE_END):])
    b = s.find(_PASTE_START)
    if b < 0:
        return _drop_responses(s)
    e = s.find(_PASTE_END, b + len(_PASTE_START))
    if e < 0:                                          # 粘贴没结束:状态存住,后段透传
        _in_paste[0] = True
        return _drop_responses(s[:b]) + s[b:]
    return (_drop_responses(s[:b])
            + s[b:e + len(_PASTE_END)]
            + _strip_terminal_responses(s[e + len(_PASTE_END):]))


def _guarded_input():
    """造一个"输入先到过滤器再到 PTK 解析器"的 Vt100Input;任何不兼容都退回 None
    (None = 让 PromptSession 自己 create_input(),即改造前的行为,绝不因加固而崩)。"""
    if os.name != "posix" or not sys.stdin.isatty():
        return None
    try:
        from prompt_toolkit.input.vt100 import Vt100Input
        inp = Vt100Input(sys.stdin)
        parser = getattr(inp, "vt100_parser", None)
        orig = getattr(parser, "feed", None)
        if orig is None:
            return None                    # 属性名变了(版本漂移):不加固,不冒险
    except Exception:
        return None
    def _feed(data):
        data = _strip_terminal_responses(data)
        if data:
            orig(data)
    parser.feed = _feed                    # 只在实例上包,不动 prompt_toolkit 类本身
    return inp


# ---------- 剪贴板图片嗅探(截图 → 直接粘贴) ----------
# 终端粘贴协议只能传文本,图片字节永远不会从 Cmd+V/Ctrl+V 通道进来。可行的是
# 在粘贴/热键发生时主动问系统「剪贴板里现在装的是不是图片」:各平台都有
# 零额外依赖的探测+提取手段。格式集合与 wchat 的 IMG_EXTS 对齐
# (png/jpg/jpeg/gif/webp/bmp),提取结果按扩展名落盘,交给 bash 的
# parse_placeholders 与 Ctrl+F 走同一条转述管线。

def _run(args, timeout=3, **kwargs):
    """subprocess.run 容错包装:工具缺失/超时/异常一律返回 None,
    由调用方当作「剪贴板无图」降级。"""
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _is_wsl():
    """与 wchat.sh 的 detect_os 同判据:WSL_DISTRO_NAME 或 /proc/version
    含 microsoft。WSL 下剪贴板图片在 Windows 侧,必须走 interop。"""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _grab_macos():
    """macOS:osascript 读剪贴板。「clipboard info」看格式类,再按类取
    「the clipboard as «class X»」——osascript 把二进制输出成十六进制
    文本(«data ...»),剥壳解码即可,零额外依赖。TIFF 不在 wchat 图片
    白名单里,用系统自带 sips 转 png。"""
    info = _run(["osascript", "-e", "clipboard info"])
    if not info or info.returncode != 0:
        return None, None
    text = info.stdout.decode("utf-8", "replace")
    for cls, ext in (("«class PNGf»", "png"), ("«class GIFf»", "gif"),
                     ("«class TIFF»", "png")):
        if cls not in text:
            continue
        r = _run(["osascript", "-e", f"the clipboard as {cls}"])
        if not r or r.returncode != 0:
            return None, None
        out = r.stdout.decode("utf-8", "replace")
        # osascript 输出形如 «data PNGf89504E47...»:«data » 后先是格式
        # 类名(PNGf/GIFf/TIFF),再往后才是十六进制数据。不能对整串做
        # re.sub 剥壳,否则 "data" 与类名里的十六进制字母(d/a/f 等)会
        # 被当成图片字节,写坏文件。按类名精确截取它后面的十六进制段。
        token = cls.replace("«class ", "").replace("»", "")
        m = re.search(r"«data\s+" + re.escape(token) + r"([0-9A-Fa-f]+)»", out)
        if not m:
            return None, None
        try:
            data = bytes.fromhex(m.group(1))
        except ValueError:
            return None, None
        if cls == "«class TIFF»":
            data = _tiff_to_png(data)
        return data, ext
    return None, None


def _tiff_to_png(data):
    """sips 把 TIFF 字节转 PNG(macOS 自带)。临时文件即用即删。"""
    if not shutil.which("sips"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".tiff", delete=False) as f:
        f.write(data)
        tiff_path = f.name
    png_path = tiff_path + ".png"
    try:
        r = _run(["sips", "-s", "format", "png", tiff_path, "--out", png_path],
                 timeout=15)
        if not r or r.returncode != 0 or not os.path.exists(png_path):
            return None
        with open(png_path, "rb") as f:
            return f.read()
    finally:
        for p in (tiff_path, png_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _grab_wsl():
    """WSL:经 interop 用 PowerShell 读 Windows 侧剪贴板(截图只存在于
    Windows 侧,Linux 剪贴板通常是空的)。Clipboard.GetImage() 无图返回
    null、不写文件;有图经 MemoryStream 序列化成 png 再写回 WSL 侧路径。
    两个实测坑:1) WSL interop 默认不传自定义环境变量,必须经 WSLENV
    显式声明,/l 标志顺带把路径自动转成 Windows 形式(\\wsl.localhost\…),
    免去 wslpath 手动转换;2) GDI+ 的 Image.Save() 拒绝 wsl 桥接路径,
    先存 MemoryStream、再 [IO.File]::WriteAllBytes 直写才成功。
    -STA 必须带:剪贴板 API 要求单线程套间。"""
    if not shutil.which("powershell.exe"):
        return None, None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        # 用户 shell 可能已定义 WSLENV,追加而非覆盖
        wsl_env = os.environ.get("WSLENV", "")
        env = dict(os.environ, WCLIP_PATH=tmp_path,
                   WSLENV="WCLIP_PATH/l" if not wsl_env else wsl_env + ":WCLIP_PATH/l")
        r = _run(["powershell.exe", "-NoProfile", "-STA", "-Command",
                  "Add-Type -AssemblyName System.Windows.Forms;"
                  "$i=[System.Windows.Forms.Clipboard]::GetImage();"
                  "if($i){$ms=New-Object System.IO.MemoryStream;"
                  "$i.Save($ms,[System.Drawing.Imaging.ImageFormat]::Png);"
                  "[System.IO.File]::WriteAllBytes($env:WCLIP_PATH,$ms.ToArray())}"],
                 timeout=15, env=env)
        if not r or r.returncode != 0 or os.path.getsize(tmp_path) == 0:
            return None, None
        with open(tmp_path, "rb") as f:
            return f.read(), "png"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _grab_x11():
    """X11:xclip 列 TARGETS,命中 image/* 再按该 target 取二进制。"""
    if not shutil.which("xclip") or not os.environ.get("DISPLAY"):
        return None, None
    targets = _run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"])
    if not targets or targets.returncode != 0:
        return None, None
    m = re.search(rb"image/(png|jpeg|gif|webp|bmp)", targets.stdout)
    if not m:
        return None, None
    fmt = m.group(1).decode()
    r = _run(["xclip", "-selection", "clipboard", "-t", f"image/{fmt}", "-o"])
    if not r or r.returncode != 0 or not r.stdout:
        return None, None
    return r.stdout, "jpg" if fmt == "jpeg" else fmt


def _grab_wayland():
    """Wayland:wl-paste 列类型,命中 image/* 按类型取二进制(格式同上)。"""
    if not shutil.which("wl-paste"):
        return None, None
    types = _run(["wl-paste", "--list-types"])
    if not types or types.returncode != 0:
        return None, None
    m = re.search(rb"image/(png|jpeg|gif|webp|bmp)", types.stdout)
    if not m:
        return None, None
    fmt = m.group(1).decode()
    r = _run(["wl-paste", "--type", f"image/{fmt}"])
    if not r or r.returncode != 0 or not r.stdout:
        return None, None
    return r.stdout, "jpg" if fmt == "jpeg" else fmt


def _clip_image_file(clip_dir):
    """把剪贴板图片落盘到会话级临时目录,返回路径;无图/失败返回 None。
    文件生命周期按业界惯例由调用方在消费(转述)后统一清理:bash 侧每轮
    输入开始前清空上一轮的 clip 目录,退出时 EXIT trap 兜底。"""
    if not clip_dir:
        return None
    if sys.platform == "darwin":
        data, ext = _grab_macos()
    elif _is_wsl():
        data, ext = _grab_wsl()
    elif os.environ.get("WAYLAND_DISPLAY"):
        data, ext = _grab_wayland()
    else:
        data, ext = _grab_x11()
    if not data or not ext:
        return None
    try:
        os.makedirs(clip_dir, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="clip_", suffix="." + ext, dir=clip_dir)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path
    except OSError:
        return None


def _insert_clip_image(event, clip_dir):
    """剪贴板图片 → 文件占位行。格式与 wchat 的 read_file 产物完全一致
    (📋 [图片] <路径> + 零宽标记):提交时 bash 的 parse_placeholders 按
    行前缀解析进 input_images 统一转述,零宽标记让整行原子删除 = 取消附加
    (占位删除机制免费复用)。占位行必须独占一行:parse 只认行首前缀,
    光标所在行已有内容时先补一个换行,否则占位与文字挤在同一行,提交时
    整行按字面文本进消息、图片丢失(AI 转而用 read_image 猜路径,真机
    复现)。行尾补换行,光标落空行,便于继续输入;发送仍是回车(不再依赖旧流
    的"空行提交"约定)。
    返回 True=已附加;False=剪贴板无图。"""
    path = _clip_image_file(clip_dir)
    if not path:
        return False
    b = event.current_buffer
    line_before = b.document.text_before_cursor.split("\n")[-1]
    if line_before:
        b.insert_text("\n")
    b.insert_text("📋 [图片] " + path + "​\n")
    return True


def _squeeze_box(session):
    """把输入窗的高度从"撑满剩余屏幕"改成"按内容高度",让底部横线紧贴输入行。

    为什么必须动:prompt_toolkit 给输入窗口的默认高度是裸 `Dimension()`,意思是
    **可延伸**(剩余空间全归它);而 `bottom_toolbar` 挂在 HSplit 末尾,于是底部工具条
    被推到**屏幕最后一行** —— 实测 24 行终端里输入行在第 2 行、横线掉到第 24 行,
    中间 21 行空白,看着像"框裂了"。它只在 `renderer_height_is_known` 之后才画,
    所以现象是"每次刷新位置都会跳",更难辨认。

    改法只碰一个属性:`dont_extend_height`(preferred height 由 BufferControl 按
    行数给出,多行输入照常长高,行为不变)。不改 prompt_toolkit 的类、不重写 Layout ——
    我们只调实例,与 `_guarded_input` 包实例而不动类的口径一致。
    """
    try:
        from prompt_toolkit.filters import to_filter
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.layout.controls import BufferControl
        for c in session.layout.walk():
            if isinstance(c, Window) and isinstance(c.content, BufferControl):
                if c.content.buffer is session.default_buffer:
                    # 属性存的是**Filter**(渲染时当函数调用),写裸 True 会在下一帧
                    # 抛 TypeError:'bool' object is not callable —— 表现为输入框直接消失。
                    c.dont_extend_height = to_filter(True)
    except Exception:
        pass      # 只是观感:拿不到内部布局就退回默认(仍是可用的输入框),不阻断输入


def _install_rules(session):
    """把上下两条横线换成"按本帧渲染宽度填"的控件。

    要解决两件事:
      1. **宽度来源**(resize 残影的根因):绝不能用 `get_app().output.get_size()`
         这种活查询 —— 拖拽改变窗口大小时它会在同一帧内变,而我们这一帧是
         prompt_toolkit 按自己缓存的 size 渲染的。宽出去那截擦不掉,回流之后
         就成了"输入框上部不规则残影"。`create_content(width, ...)` 的 `width`
         才是这一帧真正的列数。
      2. 上下两条线必须**同宽**:它们现在来自同一种控件、同一个 width,天然一致。

    定位方式**按结构**,不按"第一个 FormattedTextControl":
    那个窗口是 HSplit 里紧挨着 buffer 窗口的前一个孩子(`_create_layout` 里
    `ConditionalContainer(Window(FormattedTextControl(get_prompt_text_1)), ...)`)。
    按类型撞运气会先撞到 `Frame` 的 label 窗口(它也是 FormattedTextControl),
    结果横线被塞进一个不渲染的窗口 —— 表现是"顶部横线凭空消失",极难归因。

    只替换控件实例,不动 prompt_toolkit 的类、不重写 Layout。任何一步拿不到就
    整体放弃、退回旧行为 —— 框难看总比输入框坏掉好。
    """
    try:
        from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
        from prompt_toolkit.layout.controls import BufferControl

        rule_ctl = _width_control(lambda w: [("class:border", _rule(w))])
        bar_ctl = _width_control(
            lambda w: [("class:border", _rule(w)), ("class:hint", "\n" + INPUT_HINT)])

        top_done = bar_done = False
        for c in session.layout.walk():
            if not isinstance(c, Window):
                continue
            if getattr(c, "style", "") == "class:bottom-toolbar":
                c.content = bar_ctl
                bar_done = True

        # 输入行上方那一行:HSplit 里紧挨着"buffer 窗口"的前一个孩子。
        # 注意 buffer 窗口外面还套着一层 ConditionalContainer(_create_layout 里是
        # `ConditionalContainer(default_buffer_window, Condition(...))`),
        # 直接判 `isinstance(k, Window)` 会一个都匹配不上 —— 那样整套替换静默失效,
        # 表现就是"顶部横线凭空消失"。
        def _win_of(container):
            c2 = container
            while isinstance(c2, ConditionalContainer):
                c2 = c2.content
            return c2 if isinstance(c2, Window) else None

        for c in session.layout.walk():
            if not isinstance(c, HSplit):
                continue
            kids = list(c.children)
            for i, k in enumerate(kids):
                w = _win_of(k)
                if (w is not None and isinstance(w.content, BufferControl)
                        and w.content.buffer is session.default_buffer):
                    prev = _win_of(kids[i - 1]) if i else None
                    if prev is not None:
                        prev.content = rule_ctl
                        top_done = True
                    break
            if top_done:
                break
        return top_done and bar_done
    except Exception:
        return False


def _reflow_extra(line_lens, cursor_y, new_width):
    """估算"窗口变窄后,光标**上方**那几行多折出来了几行"。

    这是 resize 残影的第二个根因,与宽度来源无关:
    终端(会 reflow 的那些)把**旧**的长横线按新宽度重新折行,于是箱子在屏幕上
    实际占的行数变多;而 prompt_toolkit 自己记的 `_cursor_pos.y` 是**它上一帧画的
    逻辑行数**,没跟着变。它擦屏时只上移 `y` 行,落点就在箱子中间 ——
    上面剩下的那截旧横线擦不到,用户看到的就是"上方多出一条横杠"。

    修法:擦之前先多上移 `extra` 行,让"回箱顶"真正回到箱顶。extra 只算
    **光标上方**的行(下方那些行 reflow 后仍在光标下面,erase_down 会一并擦掉),
    所以多擦的就是箱子自己那几行,不会吃掉箱子之上的历史正文。

    `line_lens` 是上一帧每行的**显示宽度**(末尾空行传 0)。极窄宽度下也不至于
    算出个天文数字:返回值由调用方再夹上限。
    """
    try:
        w = max(1, int(new_width))
        cy = max(0, int(cursor_y))
        lens = list(line_lens)
        above = 0
        for i in range(cy):                         # 只算光标**上方**的行
            n = int(lens[i] or 0) if i < len(lens) else 0
            above += (-(-n // w)) if n > 0 else 1
        # extra = 物理行数(above) - 逻辑行数(cy) = erase 之前需**额外**上移的行数。
        # **不截断**:大跨度收窄要多移好几行,截到常数会留下多条残影(旧实现的坑)。
        return max(0, above - cy)
    except Exception:
        return 0


def _anchor_to_bottom(renderer, cols):
    """盒**贴屏幕底部**时,把物理光标用**绝对行**定位到盒顶、清干净,并把 renderer 置成
    "从这里全量重画" —— 免疫"切 tab / 挂起回来时终端把光标挪了位置"造成的相对擦屏错位
    (那会让旧盒整块残留、叠成多个框,用户实报)。

    只在盒确实贴底(`_min_available_height` 很小,光标下方空行不超过盒高)时动手;否则返回
    False,交回原来的相对逻辑。盒不在底部(内容少、下方一片空)时绝不能贴底锚定,否则会把盒
    搬到屏幕底部、还擦错位置。返回 True 表示"已定位+擦除并重置渲染状态,可直接重画"。
    """
    last = getattr(renderer, "_last_screen", None)
    if last is None or not cols or cols < 2:
        return False
    minav = getattr(renderer, "_min_available_height", 0) or 0
    out = renderer.output
    try:
        rows = out.get_size().rows
    except Exception:
        return False
    cy = getattr(getattr(renderer, "_cursor_pos", None), "y", 0) or 0
    # 上一帧各行在新宽度下的物理折行数;above=光标上方物理行数,H=盒**物理**总高
    wrapped = []
    for y in range(last.height):
        row = last.data_buffer[y]
        w = max((x for x, cell in row.items() if cell.char != " "), default=-1) + 1
        wrapped.append(max(1, -(-w // cols)) if w > 0 else 1)
    H = sum(wrapped)
    above = sum(wrapped[:max(0, cy)]) if cy <= len(wrapped) else H
    # 光标绝对行:_min_available_height 含光标行,row = rows - minav + 1
    if not minav:
        return False
    cursor_row = rows - minav + 1
    box_bottom = cursor_row + (H - above - 1)   # 盒底绝对行
    # 只有盒底贴到屏幕底部(允许 1 行误差)才锚定;否则交回相对逻辑(别把半空屏的盒搬到底部)
    if box_bottom < rows - 1:
        return False
    top = cursor_row - above                   # 盒顶绝对行
    if top < 1:
        return False
    out.write_raw("\r\x1b[%d;1H" % top)   # 绝对定位到盒顶(第 top 行,第 1 列)
    out.erase_down()                       # 从盒顶清到屏幕底 —— 不管光标原来在哪都清得干净
    out.flush()
    from prompt_toolkit.data_structures import Point
    renderer._cursor_pos = Point(0, 0)     # 重画从"盒顶"开始
    renderer._last_screen = None           # 强制全量重画(不留上一帧做 diff)
    renderer._min_available_height = rows - top + 1
    return True


def _resize_guard(session, tty=None):
    """resize 时把光标先退到**箱子真正的物理顶部**,再让 prompt_toolkit 擦屏。

    ⚠ 旧实现是包 `renderer.erase()`、在里面判 `output.get_size().列 < last_size.列` 决定
    补多少 —— 实测**完全无效**(加了和没加一模一样):`_on_resize` 调 erase 的时刻
    `output.get_size()` 往往还是旧值(等于 last_size),"变小"判据不成立、extra 恒 0;而且
    只处理变窄、还把补偿截到 4 行。回归测试"假绿"是因为测试屏 resize 时**不 reflow**。

    正确做法:包 `app._on_resize`,在原始 erase 之前,按"上一帧每行宽度 / 新宽度"折行算出
    需**额外**上移的行数(`_reflow_extra`),先 `cursor_up(extra)`。新宽度**直读 ioctl**
    (`os.get_terminal_size(tty.fileno())`),不信 resize 回调里可能过期的 `output.get_size()`。
    变宽时 extra=0,不多吃历史;拿不到尺寸/上一帧就退回原行为(残影难看总比错位/吃历史好)。
    """
    try:
        app = session.app
        renderer = app.renderer
        orig = app._on_resize

        def _new_cols():
            f = None
            try:
                f = tty.fileno() if tty is not None else None
            except Exception:
                f = None
            if f is not None:
                try:
                    return os.get_terminal_size(f).columns
                except OSError:
                    pass
            try:
                return renderer.output.get_size().columns
            except Exception:
                return 0

        def on_resize():
            # 优先:盒贴屏幕底部时,用**绝对行**从屏幕底部锚定并清理,免疫光标错位(切 tab)。
            # 成功则跳过下面的相对补偿(已把 renderer 置成从盒顶全量重画)。
            try:
                if _anchor_to_bottom(renderer, _new_cols()):
                    return orig()
            except Exception:
                pass
            try:
                last = getattr(renderer, "_last_screen", None)
                cy = getattr(getattr(renderer, "_cursor_pos", None), "y", 0) or 0
                if last is not None and cy > 0:
                    w = _new_cols()
                    if w and w > 1:
                        lens = []
                        for y in range(last.height):
                            row = last.data_buffer[y]
                            lens.append(max((x for x, cell in row.items()
                                             if cell.char != " "), default=-1) + 1)
                        extra = _reflow_extra(lens, cy, w)
                        if extra > 0:
                            # erase 自己还会按 _cursor_pos.y 上移;这里补"折行多出来的"那段。
                            renderer.output.cursor_up(extra)
                            renderer.output.flush()
            except Exception:
                pass
            return orig()

        app._on_resize = on_resize
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="", help="历史文件路径(空=不持久)")
    # --commands/--no-commands 保留为**调用契约**:回车恒发送之后,本助手不再需要
    # "识别单行命令立即返回"那条快速路(单行文本交回 bash,handle_cmd_line 照样解析,
    # 结果逐字相同)。参数留着是为了不动 wchat 侧的调用点与闸门启动参数;助手侧不消费。
    ap.add_argument("--commands", default="", help="[保留]空格分隔的单行命令集(助手不再消费)")
    ap.add_argument("--default", default="", help="初始缓冲区文本(文件选择取消后带回)")
    ap.add_argument("--no-commands", action="store_true",
                    help="[保留]续行/补输模式(助手不再消费)")
    ap.add_argument("--clip-dir", default="",
                    help="剪贴板图片落盘的会话级临时目录(空=禁用图片嗅探)")
    args = ap.parse_args()

    if not sys.stdin.isatty():
        return 2

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.styles import Style
        from prompt_toolkit.output import create_output
        from prompt_toolkit.history import FileHistory, InMemoryHistory
    except ImportError:
        return 2

    # 命令清单不再由助手消费(--commands 保留为调用契约,见 main 开头的说明):
    # 单行命令现在也走"回车=发送"同一条路,回 bash 后由 handle_cmd_line 精确分发。
    try:
        history = FileHistory(args.history) if args.history else InMemoryHistory()
    except OSError:
        history = InMemoryHistory()

    kb = KeyBindings()
    stored_pastes = {}   # 占位令牌 → 粘贴全文(提交前换回)
    paste_seq = [0]
    fpick_height = [None]   # Ctrl+F 退出时已渲染框的行数(用于擦残留框)

    @kb.add(Keys.BracketedPaste)
    def _(event):
        """多行粘贴只插入一行占位:缓冲区保持短小,上方已输入的行不被
        顶出屏幕;内容存入侧表,提交时换回。占位后补一个换行让光标落在
        空行,便于继续输入(发送一律靠回车)。单行粘贴原样插入。
        粘贴结尾的 \n 是终止符非内容,按旧流归一化剥掉一个。占位尾部拼
        上序号个零宽空格(U+200B,不可见)作唯一标记,提交时按整个占位串
        换回,多次同规模粘贴互不混淆。
        空粘贴(图片剪贴板下 Ctrl+V 的常见形态)嗅探剪贴板图片:命中则
        按文件占位行附加(与 Ctrl+F 同管线);无图静默——空文本粘贴本来
        就不该插入任何内容。"""
        data = event.data.replace("\r\n", "\n").replace("\r", "\n")
        if data == "":
            _insert_clip_image(event, args.clip_dir)
            return
        if "\n" not in data:
            event.current_buffer.insert_text(data)
            return
        if data.endswith("\n"):
            data = data[:-1]
        n_lines = data.count("\n") + 1
        paste_seq[0] += 1
        placeholder = f"📋 [已粘贴 {n_lines} 行文本内容]" + "​" * paste_seq[0]
        stored_pastes[placeholder] = data
        event.current_buffer.insert_text(placeholder + "\n")

    @kb.add("enter")
    def _(event):
        r"""回车 = 发送;行尾反斜杠 + 回车 = 续行(吃掉反斜杠,不发送)。

        为什么要拆开这两个职责(用户实报):旧规则是"内容+回车=换行继续,空行回车
        =发送",一个键两种含义 —— 想发一条单行消息必须按两遍回车,而"想发送时的
        第一遍"和"想换行"在屏幕上完全看不出区别。现在回车恒发送,续行改成显式的
        行尾反斜杠,与 bash/readline 的习惯一致,而且**看得见**(反斜杠就在屏上)。

        这个语义不是新发明:输入框换成 prompt_toolkit 之前,read -e 流里就是这么
        做的(fdc1937^:wchat.sh 的 `if [[ "$line" == *\\ ]]; then line="${line%?}"
        ... continue; fi`),换成 PTK 时丢了 —— 帮助里"行尾 \ 也可续行"那句话因此
        变成过期说明(实测:产品版交回的是 `foo\\n`,反斜杠留在正文里)。
        """
        b = event.current_buffer
        doc = b.document
        line_before = doc.text_before_cursor.split("\n")[-1]
        if line_before.endswith("\\"):
            # 只删最后一个字符(即那个反斜杠),再插入换行 —— 与旧版 ${line%?} 同义。
            # 按**个数**删而不是按内容替换:不同实现对反斜杠的转义/正则语义有差异,
            # 按内容删容易删多(旧版注释里记的就是这个坑,故用 %? 而非 %\\)。
            b.delete_before_cursor(count=1)
            b.insert_text("\n")
            return
        if not b.text.strip():
            return                      # 空缓冲区回车=不动,避免发出空消息
        event.app.exit(result=b.text)

    def placeholder_ranges(text):
        """粘贴占位行(含零宽标记的行)的 [start, end) 区间列表。每次删除
        按当前文本现算,不缓存,天然与编辑状态同步。"""
        ranges = []
        pos = 0
        while pos < len(text):
            nl = text.find("\n", pos)
            line_end = len(text) if nl < 0 else nl
            if "​" in text[pos:line_end]:
                ranges.append((pos, line_end))
            if nl < 0:
                break
            pos = nl + 1
        return ranges

    def atomic_delete(b, start, end):
        """删除 [start, end);若与任一占位区间相交,扩为整段占位删除——
        占位符对一切删除操作呈现为一个原子单元。侧表条目从不弹出:swap
        只替换文本中真实存在的占位串,删除后不复活、Ctrl+_ 撤销恢复占位
        文本后内容也一并恢复。"""
        if start >= end:
            return
        for ps, pe in placeholder_ranges(b.text):
            if start < pe and end > ps:
                start, end = ps, pe
                break
        b.text = b.text[:start] + b.text[end:]
        b.cursor_position = min(start, len(b.text))

    @kb.add("c-c")
    def _(event):
        """Ctrl+C:清空当前行(占位感知,与 wchat 的 unix-line-discard 对齐)"""
        b = event.current_buffer
        doc = b.document
        before = doc.text_before_cursor.split("\n")[-1]
        after = doc.text_after_cursor.split("\n")[0]
        line_start = doc.cursor_position - len(before)
        line_end = doc.cursor_position + len(after)
        atomic_delete(b, line_start, line_end)

    @kb.add("c-h")
    def _(event):
        """退格:占位感知——碰到占位符任意字符(含零宽尾巴)整段删除"""
        b = event.current_buffer
        doc = b.document
        if doc.selection:
            start, end = doc.selection_range()
            atomic_delete(b, start, end)
            return
        prev = doc.char_before_cursor
        atomic_delete(b, doc.cursor_position - len(prev), doc.cursor_position)

    @kb.add("c-d")
    def _(event):
        """Ctrl+D:删光标处字符(占位感知);空缓冲区保持 EOF 语义"""
        b = event.current_buffer
        doc = b.document
        if doc.selection:
            start, end = doc.selection_range()
            atomic_delete(b, start, end)
            return
        if not b.text:
            event.app.exit(exception=EOFError())
            return
        nxt = doc.current_char
        atomic_delete(b, doc.cursor_position, doc.cursor_position + len(nxt))

    @kb.add("c-u")
    def _(event):
        """Ctrl+U:删到行首(占位感知)。粘贴后光标落在空行,删除范围为
        空、不触发重叠规则——空行且上一行是粘贴占位时连占位整段删除。"""
        b = event.current_buffer
        doc = b.document
        before = doc.text_before_cursor.split("\n")[-1]
        after = doc.text_after_cursor.split("\n")[0]
        line_start = doc.cursor_position - len(before)
        if not before.strip() and not after.strip():
            lines = b.text.split("\n")
            cur_idx = len(doc.text_before_cursor.split("\n")) - 1
            prev_idx = cur_idx - 1
            if prev_idx >= 0 and "​" in lines[prev_idx]:
                prev_start = sum(len(l) + 1 for l in lines[:prev_idx])
                atomic_delete(b, prev_start, line_start)
                return
        atomic_delete(b, line_start, doc.cursor_position)

    @kb.add("c-w")
    def _(event):
        """Ctrl+W:删前一词(占位感知)"""
        b = event.current_buffer
        doc = b.document
        word_start = doc.find_start_of_previous_word(WORD=True)
        atomic_delete(b, word_start, doc.cursor_position)

    @kb.add("c-k")
    def _(event):
        """Ctrl+K:删到行尾(占位感知)"""
        b = event.current_buffer
        doc = b.document
        after = doc.text_after_cursor.split("\n")[0]
        line_end = doc.cursor_position + len(after)
        atomic_delete(b, doc.cursor_position, line_end)

    @kb.add("c-r")
    def _(event):
        """Ctrl+R:清空输入框已输入的全部文本(留在会话内,不清屏——
        清屏是 -n/-cl 的职责)。文件占位行(如有)随之整行删除=取消选择"""
        event.current_buffer.reset()

    @kb.add("c-f")
    def _(event):
        """Ctrl+F:选择文件(替代旧 @ 手势)——退出码 4 携带当前缓冲区
        文本,由 bash 走旧文件选择逻辑(read_file)。退出前记下框的行数,
        prompt() 返回后据此擦掉残留在屏上的输入框。框高必须取布局的
        preferred height——prompt_toolkit 的 is_done 收尾帧正是用它当
        渲染高度、把光标停到框下一行;若取 screen.height(渲染窗口高,
        真机 CPR 应答后 = 光标下方全部行数),上移会被钳制到屏顶、向下
        擦除把整屏清空(真机复现的严重问题)"""
        try:
            size = event.app.output.get_size()
            dim = event.app.layout.container.preferred_height(
                size.columns, size.rows)
            fpick_height[0] = dim.preferred
        except Exception:
            fpick_height[0] = None
        event.app.exit(result=("file-pick", event.current_buffer.text))

    @kb.add("c-g")
    @kb.add("c-v")
    def _(event):
        """Ctrl+G / Ctrl+V:粘贴剪贴板图片。截图工具通常只把图片放进
        剪贴板,而各终端对图片剪贴板的 Cmd+V/Ctrl+V 行为不一(有的发
        空粘贴、有的干脆不发),热键是空粘贴嗅探的兜底入口;无图时响铃
        提示。"""
        if not _insert_clip_image(event, args.clip_dir):
            event.app.output.bell()

    try:
        # 显式 utf-8:`LC_ALL=C` 下 open() 默认 ASCII,新输入框的 `❯`/`─`
        # 会在收尾补横线时抛 UnicodeEncodeError(真终端与 tty 闸门都会踩)。
        tty = open("/dev/tty", "w", encoding="utf-8", errors="replace")
    except OSError:
        return 2

    def _continuation(width, line_number, is_soft_wrap):
        """续行提示符:硬换行的续行缩进两格(对齐 `❯ ` 后面的正文);
        软折行不加(与旧 read -e 的终端自然折行一致)。"""
        if is_soft_wrap:
            return []
        return [("class:prompt", "  ")]

    def _top():
        """输入行上方:先占一行给横线(`_split_multiline_prompt` 会把最后一个
        `\\n` 之前的内容画成"输入行上方的一行")。

        ⚠ 这一行**不在这里取宽度**:宽度由 `_install_rules()` 换上的
        `_width_control` 按本帧渲染宽度填 —— 详见那里的注释(残影根因)。
        """
        return [("class:border", "\n"), ("class:prompt", "❯ ")]

    def _bottom():
        """输入行下方:横线 + 快捷键提示(提示只在活跃输入时出现)。

        提交后 prompt_toolkit 会把 bottom_toolbar 收掉、把光标停在输入行下一行;
        main() 在返回前再补一条同样的横线,于是**历史里也留下完整的输入框**,
        但不会每轮重复那句快捷键提示。横线宽度同样由 `_width_control` 给。"""
        return [("class:border", ""), ("class:hint", INPUT_HINT)]

    # 渲染(提示符/输入行/重绘)全部经 output 写到 /dev/tty 上屏;
    # stdout 只承载结果文本,由 bash 的命令替换捕获
    # input= 显式给一个带应答过滤的 Vt100Input(见 _guarded_input);拿不到就传 None,
    # 由 PromptSession 自己 create_input(),行为等同改造前。
    session = PromptSession(
        history=history,
        key_bindings=kb,
        multiline=True,
        message=_top,
        bottom_toolbar=_bottom,
        # 占位文本必须**自己带样式类**:prompt_toolkit 没给 placeholder 任何默认样式类,
        # 裸字符串会用终端默认前景色 —— 深色主题下就是白的,比输入框本体还亮。
        placeholder=[("class:placeholder", "问点什么…(回车发送)")],
        prompt_continuation=_continuation,
        style=Style.from_dict({
            # 上下横线 / `❯` / 占位 / 提示 **一律 dim**(= SGR 2),与思考内容
            # (`render.GREY = "\033[2m"`)同一个灰。这是用户明确要求的"一屏只有助手正文
            # 是亮的";也是 2026-12 把边框从蓝灰 `#5f6b7a` 改回来的原因 —— 自定义色
            # 只能近似,永远和"思考内容那一档灰"对不上。
            "prompt": "dim",
            "border": "dim",
            "placeholder": "dim",
            "hint": "dim",
            # 默认主题给 bottom-toolbar 套了 reverse(整条反色)与前景色;这里必须去掉,
            # 否则我们画出来的横线会变成一条高亮色块,比输入框本身还抢眼。
            "bottom-toolbar": "noreverse bg:default",
            "bottom-toolbar.text": "noreverse bg:default",
        }),
        output=_counting_output(create_output(stdout=tty)),
        input=_guarded_input(),
    )

    _squeeze_box(session)
    _install_rules(session)
    _resize_guard(session, tty)

    def _close_box():
        """提交/退出后补上底部横线。prompt_toolkit 收尾帧会收掉 bottom_toolbar,
        光标停在输入行下一行;在这里写一条横线,历史里就留下一个完整的输入框。

        **必须自己包 `\\033[2m`**:这条线不是 prompt_toolkit 画的,样式表管不到它;
        裸写就是用终端默认前景色(深色主题下是白的),表现为"AI 回答完之后,框的下边
        突然比上边亮" —— 用户实报的这个 bug,真因就在这里。SGR 2 与样式表里的
        `dim` 是同一个灰(见 Vt100 的 `if dim: parts.append("2")`)。
        """
        try:
            tty.write("\r\033[2m" + _rule_text(tty.fileno()) + "\033[0m\n")
            tty.flush()
        except OSError:
            pass

    try:
        result = session.prompt(default=args.default)
    except (EOFError, KeyboardInterrupt):
        _close_box()
        return 1

    if isinstance(result, tuple) and result and result[0] == "file-pick":
        _erase_stale_box(tty, fpick_height[0])
        # 缓冲区里可能还有粘贴占位令牌:换回全文再交 bash(重开输入会话
        # 时经 --default 原样显示,否则占位符会以字面形式进消息)。
        # 注意:这里不剥零宽标记——文件占位行的标记要保留,重开输入框
        # 后仍支持整行原子删除(删除占位行=取消选择);提交路径统一剥
        text = result[1]
        for token, content in stored_pastes.items():
            text = text.replace(token, content)
        sys.stdout.write(text)
        return 4

    # 提交前把占位令牌换回粘贴全文,再剥离残留零宽标记(占位被部分
    # 删除时令牌不匹配、不换回,但零宽字符绝不允许进入消息)
    for token, content in stored_pastes.items():
        result = result.replace(token, content)
    result = result.replace("​", "")
    _close_box()
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
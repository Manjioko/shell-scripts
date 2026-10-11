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
        tty = open("/dev/tty", "w")
    except OSError:
        return 2

    def _continuation(width, line_number, is_soft_wrap):
        """续行提示符:硬换行的续行加灰色 │ 前缀(与首行一致);
        软折行不加(与旧 read -e 的终端自然折行一致)"""
        if is_soft_wrap:
            return []
        return [("class:prompt", "│ ")]

    # 渲染(提示符/输入行/重绘)全部经 output 写到 /dev/tty 上屏;
    # stdout 只承载结果文本,由 bash 的命令替换捕获
    # input= 显式给一个带应答过滤的 Vt100Input(见 _guarded_input);拿不到就传 None,
    # 由 PromptSession 自己 create_input(),行为等同改造前。
    session = PromptSession(
        history=history,
        key_bindings=kb,
        multiline=True,
        message=[("class:prompt", "│ ")],
        prompt_continuation=_continuation,
        style=Style.from_dict({"prompt": "#808080"}),
        output=_counting_output(create_output(stdout=tty)),
        input=_guarded_input(),
    )

    try:
        result = session.prompt(default=args.default)
    except (EOFError, KeyboardInterrupt):
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
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
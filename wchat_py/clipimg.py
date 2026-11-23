#!/usr/bin/env python3
"""从系统剪贴板抓取图片并落盘(供 TUI 的 Ctrl+V/Ctrl+G 粘贴图片用)。

调用方只认 `_clip_image_file(clip_dir)`;其余 `_grab_*` 是各平台实现。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile


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
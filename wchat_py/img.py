"""img —— 图片/视觉的纯逻辑(迁移自 wchat.sh 的 img_* 族)。

只放**纯函数**:错误措辞判定、分档梯子等。碰文件系统/请求体/引用表的(`prepare_image`、
`img_build_map`、`describe_images`)按各自波次再迁。

退出码(见 `_contract`):
  0  成功(谓词结果 "0"/"1" 写 stdout)
  1  未知子命令

用法:
  python3 -m wchat_py.img reject_wording    # stdin=错误文本 -> stdout "0"(算不收图)/"1"
  python3 -m wchat_py.img inline_sides <max_side> <shrink> <ladder>
"""

from __future__ import annotations

import re
import sys

from . import _contract


def load_data_url(path, max_side=2048):
    """读图片为 data URL,超过 max_side 先缩(Pillow 优先, 否则 macOS sips)。
    返回 (url, mime, orig_side, actual_side)。失败返回 (path, "", 0, 0)。"""
    import base64
    import mimetypes
    import os
    import subprocess
    mime = mimetypes.guess_type(path)[0] or "image/png"
    orig = actual = 0
    data = None
    try:
        from PIL import Image
        im = Image.open(path)
        orig = max(im.size)
        if max_side and orig > max_side:
            im.thumbnail((max_side, max_side))
            import io
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG")
            data = buf.getvalue()
            mime = "image/png"
            actual = max_side
        else:
            actual = orig
    except Exception:
        data = None
    if data is None:
        try:
            import shutil
            if max_side and shutil.which("sips"):
                g = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                                   capture_output=True, text=True)
                nums = [int(x.split(":")[1]) for x in g.stdout.splitlines()
                        if "pixel" in x and x.split(":")[1].strip().isdigit()]
                if nums:
                    orig = max(nums)
                    actual = orig
                if nums and max(nums) > max_side:
                    import tempfile
                    tmp = tempfile.mktemp(suffix=".png")
                    r = subprocess.run(["sips", "-Z", str(max_side), path, "--out", tmp],
                                       capture_output=True, text=True)
                    if r.returncode == 0 and os.path.isfile(tmp):
                        with open(tmp, "rb") as f:
                            data = f.read()
                        os.unlink(tmp)
                        mime = "image/png"
                        actual = max_side
        except Exception:
            data = None
    if data is None:
        try:
            with open(path, "rb") as f:
                data = f.read()
            actual = actual or orig
        except OSError:
            return (path, "", 0, 0)
    return ("data:%s;base64,%s" % (mime, base64.b64encode(data).decode()),
            mime, orig, actual)


def _not_then_support(lc: str) -> bool:
    """bash 的 `*"not"*"support"*`:存在 "not" 后面还有 "support"(顺序敏感)。"""
    return re.search(r"not.*support", lc, re.S) is not None


def reject_wording(t: str) -> bool:
    """这段报错算不算"模型不收图"?True=算。

    措辞来源是 2026-10-03 真实端点抓回的原文(见 bash 侧长注释与 .scratch/tokens.log):
      "Unexpected item type in content."(里面**没有** image/vision/not support 任何词)、
      "item must be dict and key[type]"。
    宽严取舍:**宁可漏判(继续试、多付一次失败)也不错判**(判错会把整个会话的内联能力
    永久关掉)。所以"格式/解码器/文件类型"这类排除先于命中。
    """
    if not t:
        return False
    lc = t.lower()
    group_img = ("image" in lc or "vision" in lc or "multimodal" in lc
                 or "图片" in t or "多模态" in t)
    group_no = ("not support" in lc or _not_then_support(lc) or "unsupport" in lc
                or "do not accept" in lc or "does not accept" in lc or "unsupported" in lc
                or "不支持" in t or "不接受" in t)
    group_excl = ("format" in lc or "decoder" in lc or "file type" in lc or "格式" in t)
    if group_img and group_no and not group_excl:
        return True
    # 二组:数组 content 被拒(纯文字档的典型回法),必须两个词同时在场
    if "unexpected item type" in lc and "content" in lc:
        return True
    if "item must be dict" in lc or "key[type]" in lc:
        return True
    # 三组:直接否认视觉能力的说法(不带 support/accept 字样)
    if ("not a vision model" in lc or "no vision capability" in lc or "no vision model" in lc
            or "lacks vision" in lc or "not support visual" in lc):
        return True
    return False


def cmd_reject_wording(argv) -> int:
    t = _contract.read_stdin_text()
    _contract.write_text("0" if reject_wording(t) else "1")
    return _contract.EXIT_OK


def _int_default(s, default=0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def inline_sides(max_side: str, shrink: str, ladder: str) -> str:
    """内联降档梯子:首档 IMG_INLINE_MAX_SIDE,随后把阶梯里**严格递减**的档并进来。

    与 bash 旧实现逐条对齐(tests/inline_image_test.sh 828-834 直接断言):
      · 首档永远在;`shrink != 1` 时只输出首档;
      · 阶梯逐个看:非纯数字跳过;必须 `0 < s < 上一档`(名单写乱也不会挂出更大的档);
      · 输出以空格连接。默认值由 bash 侧 `${VAR:-默认}` 解析后传入。
    """
    first = max_side if max_side != "" else "2048"
    out = [first]
    if _int_default(shrink if shrink != "" else "1", 1) != 1:
        return first
    first_num = _int_default(first, 0)
    for s in (ladder if ladder != "" else "1536 1024 768").split():
        if not re.match(r"^[0-9]+$", s):
            continue
        sn = int(s)
        if not (sn > 0 and sn < first_num):
            continue
        out.append(s)
        first_num = sn
    return " ".join(out)


def cmd_inline_sides(argv) -> int:
    a = list(argv) + ["", "", ""]
    _contract.write_text(inline_sides(a[0], a[1], a[2]))
    return _contract.EXIT_OK


_COMMANDS = {
    "reject_wording": cmd_reject_wording,
    "inline_sides": cmd_inline_sides,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "img: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
#!/usr/bin/env python3
"""wchat 输入助手:prompt_toolkit 多行输入框(替代 read -e)。

渲染输出到 /dev/tty(避免渲染序列混进结果),结果文本经 stdout 交回 bash。
语义与 wchat 的 read -e 输入一致:
  回车 = 换行继续;空行回车 = 提交整段;
  单行命令(-x,经 --commands 传入)回车立即返回,由 bash 执行;
  Ctrl+F 选择文件(退出码 4,携带缓冲区文本交 bash 走旧文件选择逻辑);
  Ctrl+C 清空当前行,Ctrl+R 清空整段;方向键/历史/括号粘贴/中文宽度原生支持。
退出码:0=提交(文本在 stdout),1=EOF/中断(无文本),2=环境异常(无 tty/缺库),
4=Ctrl+F 选择文件(缓冲区文本在 stdout)。
"""
import sys
import argparse


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="", help="历史文件路径(空=不持久)")
    ap.add_argument("--commands", default="", help="空格分隔的单行命令集")
    ap.add_argument("--default", default="", help="初始缓冲区文本(文件选择取消后带回)")
    ap.add_argument("--no-commands", action="store_true",
                    help="续行/补输模式:一切输入按正文")
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

    commands = [] if args.no_commands else args.commands.split()
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
        空行——一次回车即提交(与旧流"空行提交"一致)。单行粘贴原样插入。
        粘贴结尾的 \n 是终止符非内容,按旧流归一化剥掉一个。占位尾部拼
        上序号个零宽空格(U+200B,不可见)作唯一标记,提交时按整个占位串
        换回,多次同规模粘贴互不混淆。"""
        data = event.data.replace("\r\n", "\n").replace("\r", "\n")
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
        """回车:空行=提交整段;单行命令=立即返回;否则换行继续"""
        b = event.current_buffer
        doc = b.document
        line_before = doc.text_before_cursor.split("\n")[-1]
        line_after = doc.text_after_cursor.split("\n")[0]
        if not line_before.strip() and not line_after.strip():
            event.app.exit(result=b.text)
            return
        if "\n" not in b.text:
            t = b.text.strip()
            if any(t == c or t.startswith(c + " ") for c in commands):
                event.app.exit(result=b.text)
                return
        b.insert_text("\n")

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
    session = PromptSession(
        history=history,
        key_bindings=kb,
        multiline=True,
        message=[("class:prompt", "│ ")],
        prompt_continuation=_continuation,
        style=Style.from_dict({"prompt": "#808080"}),
        output=create_output(stdout=tty),
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

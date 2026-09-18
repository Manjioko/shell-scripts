#!/usr/bin/env bash
# 终端应答泄漏闸门:pty 队列里残留的终端应答(OSC 11 背景色等)不得变成输入框文本。
#
# 用法: bash tests/tty_osc_input_test.sh
#       bash tests/tty_osc_input_test.sh --source .scratch/input_helper.old.py   # 负对照
#       REAL=… HELPER=… 可分别指定被测 wchat.sh / input_helper.py
#
# 现象(用户实报,截图为准):`wchat -dp -l` 选完编号后,输入框里出现
#   ]11;rgb:2828/2c2c/3434]11;rgb:2828/2c2c/3434…
# 实测这串**会随回车提交**,即污染发给模型的消息,不只是难看。
#
# 为什么必须有这道闸门:发起查询的是**别人的程序**——本机 PATH 上 12 个可执行文件内嵌
# `\x1b]11;?`(vim/rvim/view/rview/vimdiff/ex、gh、codex、opencode、tmux、mdcat、ghostty)。
# 它们发完查询往往就退出,应答由终端写回 tty,**留在 pty 输入队列里跨进程存活**,被下一个
# 读者读到。所以"查是谁发的、让它别发"这条路封不住,只能在读者侧堵。wchat 的 bash 确认/
# 编号读取早已过 tty_strip_responses(ask_read_line),唯独输入框走 prompt_toolkit:
# PTK 认不出 `ESC ]`,把正文当普通按键逐个塞进缓冲区 → 就是用户看到的乱码。
#
# 判据(与 bash 版同源但**刻意不完全相同**,第 2 节钉住这条不对称):
#   丢:整段可识别应答(OSC/DCS、DA1/DA2、kitty 标志、DECRPM)
#   留:真按键(方向键/Ctrl 组合/F 键/SS3/Alt+字符/kitty 真按键)与**粘贴正文**
#   留:DSR 光标报告 `ESC[行;列R` —— prompt_toolkit 自己发 CPR(`\x1b[6n`)要用它算光标,
#        读者侧丢了输入框会重排错行(实测,见第 5 节那条 CPR 警告断言)
# 一律**不做"半截滞留等下片"**:扣着没判完的字节可能永远等不到下一片(用户只按一个 ESC),
# 那是拿真按键换噪声。
if (( ${BASH_VERSINFO[0]:-0} < 5 )); then
    for _b in "${WCHAT_BASH:-}" /opt/homebrew/bin/bash /usr/local/bin/bash "${HOME}/.linuxbrew/bin/bash"; do
        [[ -x "$_b" ]] || continue
        _v="$("$_b" -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null || true)"
        [[ "$_v" =~ ^[0-9]+$ ]] || _v=0
        (( _v >= 5 )) && exec "$_b" "$0" "$@"
    done
    echo "❌ 需要 bash 5+(与生产一致);或 WCHAT_BASH=/path/to/bash5 $0" >&2
    exit 1
fi
export LC_ALL=C
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REAL=${REAL:-$DIR/wchat.sh}
HELP=${HELP:-$DIR/input_helper.py}
if [[ "${1:-}" == "--source" && -n "${2:-}" ]]; then HELP="$2"; fi
[[ -f "$HELP" ]] || { echo "❌ 找不到被测输入助手:$HELP"; exit 1; }
PYBIN=${PYBIN:-python3}
RB="$BASH"
W=$(mktemp -d)
fail=0
ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; [[ -n "${2:-}" ]] && echo "    $2"; fail=$((fail + 1)); }
ck()  { [[ "$2" == "$3" ]] && ok "$1" || { bad "$1" "期望 [$3] 实际 [$2]"; }; }
has() { [[ "$2" == *"$3"* ]] && ok "$1" || { bad "$1" "缺少: $3"; }; }
hasnt(){ [[ "$2" != *"$3"* ]] && ok "$1" || { bad "$1" "不该出现: $3"; }; }

# ───────────── 先确认可导入(沙箱抽不出来就必须红,不能空过) ─────────────
IMP="$W/imp.py"
cat > "$IMP" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ih", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for n in ("_strip_terminal_responses", "_guarded_input"):
    if not hasattr(m, n):
        print("MISSING %s" % n); sys.exit(3)
print("IMPORT_OK")
PY
imp_out=$("$PYBIN" "$IMP" "$HELP" 2>&1)
if [[ "$imp_out" != *IMPORT_OK* ]]; then
    echo "❌ 沙箱漏抽:被测输入助手没有终端应答过滤($imp_out)"
    echo "   —— 这是负对照应有的红:说明这份实现里过滤根本不存在,后面全部断言无意义。"
    rm -rf "$W"; exit 1
fi

# ───────────── 1. 单元:四类整段应答必须丢 ─────────────
echo "1) 整段可识别应答 → 丢弃"
UNIT="$W/unit.py"
cat > "$UNIT" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ih", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
f = m._strip_terminal_responses
E = "\x1b"
# 每条 (说明, 输入, 期望输出); 期望输出为 "" 表示整段吃掉
CASES = [
    ("OSC11 背景色(ST 终止)",      E + "]11;rgb:2828/2c2c/3434" + E + "\\", ""),
    ("OSC11 背景色(BEL 终止)",      E + "]11;rgb:2828/2c2c/3434\a", ""),
    ("OSC11 ×4(用户截图形态)",      (E + "]11;rgb:2828/2c2c/3434" + E + "\\") * 4, ""),
    ("OSC10 前景色",                E + "]10;rgb:ffff/ffff/ffff" + E + "\\", ""),
    ("OSC4 调色板",                 E + "]4;11;rgb:2828/2c2c/3434" + E + "\\", ""),
    ("OSC11 夹在文字中间",          "q" + E + "]11;rgb:1/1/1" + E + "\\z", "qz"),
    ("DCS(XTVERSION)应答",          E + "P>| xterm-ghostty" + E + "\\", ""),
    ("DA1 属性应答",                E + "[?62;1;6c", ""),
    ("DA2 二级应答",                E + "[>1;10;0c", ""),
    ("kitty 键盘标志应答",          E + "[?0u", ""),
    ("DECRPM 模式应答",             E + "[?12;2$y", ""),
    ("无 ESC 的普通文本",           "hello 世界", "hello 世界"),
]
rc = 0
for label, src, want in CASES:
    got = f(src)
    print(("OK|%s" % label) if got == want else ("BAD|%s|期望 %r 实际 %r" % (label, want, got)))
    got != want and (rc := 1)
sys.exit(rc)
PY
"$PYBIN" "$UNIT" "$HELP" > "$W/unit.out" 2>&1
while IFS='|' read -r tag label detail; do
    case "$tag" in
        OK)  ok "$label" ;;
        BAD) bad "$label" "$detail" ;;
        *) [[ -z "$tag" ]] || bad "单元用例输出异常" "$tag $label $detail" ;;
    esac
done < "$W/unit.out"

# ───────────── 2. 单元:真按键与粘贴正文一个都不能吞 ─────────────
echo "2) 真按键 / DSR / 粘贴正文 → 原样放行"
cat > "$UNIT" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ih", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
f = m._strip_terminal_responses
E = "\x1b"
KEEP = [
    ("方向键 Up",                   E + "[A"),
    ("Ctrl+Up",                     E + "[1;5A"),
    ("F5(ESC[15~)",                 E + "[15~"),
    ("SS3 F1(ESCOP)",               E + "OP"),
    ("Alt+f(ESC+字符)",             E + "f"),
    ("kitty 真按键 ESC[13u",        E + "[13u"),
    ("kitty 带修饰按键 ESC[13;27u", E + "[13;27u"),
    ("F1 变体 ESC[11~",                  E + "[11~"),
    ("孤立 ESC(可能只是 Alt 前缀)",  E),
    ("ESC ] 未终止(Alt+] 形态)",     E + "]11;rgb:2"),
    ("粘贴标记内含应答形态",         E + "[200~A" + E + "]11;rgb:1/1/1" + E + "\\B" + E + "[201~"),
]
for label, src in KEEP:
    got = f(src)
    print(("OK|%s" % label) if got == src else ("BAD|%s|被改写了:期望 %r 实际 %r" % (label, src, got)))
# DSR 按 CPR 配额放行:配额=本框自己问过几次(有配额必须放行,PTK 要拿它算光标)
m._cpr_quota[0] = 1
if f(E + "[24;1R") == E + "[24;1R":
    print("OK|DSR 配额内放行(PTK 的 CPR 应答不会被误杀)")
else:
    print("BAD|DSR 配额内被吞|实得 %r" % (f(E + "[24;1R"),))
m._cpr_quota[0] = 0
if f("p" + E + "[24;1R" + "q") == "pq":
    print("OK|DSR 配额用完判成残留丢弃(vim 留下的陈旧报告不再变乱码)")
else:
    print("BAD|DSR 残留没被丢|实得 %r" % (f("p" + E + "[24;1R" + "q"),))
m._cpr_quota[0] = 1
got = f(E + "[3;1R" + E + "[9;9R")
if got == E + "[3;1R":
    print("OK|来两条 DSR 只放行一条(配额逐条扣减)")
else:
    print("BAD|DSR 配额扣减不对|实得 %r" % (got,))
m._cpr_quota[0] = 0
# 粘贴状态跨块:第一块以 200~ 结尾且正文未完 → 第二块整块按数据透传
m._in_paste[0] = False
a = f("x" + E + "[200~start")
b = f(E + "]11;rgb:1/1/1" + E + "\\tail" + E + "[201~end")
if a == "x" + E + "[200~start" and b == E + "]11;rgb:1/1/1" + E + "\\tail" + E + "[201~end":
    print("OK|粘贴跨块:正文里的应答形态不被剥")
else:
    print("BAD|粘贴跨块|a=%r b=%r" % (a, b))
if f("clean") == "clean" and m._in_paste[0] is False:
    print("OK|粘贴结束后状态复位")
else:
    print("BAD|粘贴结束后状态未复位|_in_paste=%r" % (m._in_paste[0],))
PY
"$PYBIN" "$UNIT" "$HELP" > "$W/unit2.out" 2>&1
while IFS='|' read -r tag label detail; do
    case "$tag" in
        OK)  ok "$label" ;;
        BAD) bad "$label" "$detail" ;;
        *) [[ -z "$tag" ]] || bad "单元用例输出异常" "$tag $label $detail" ;;
    esac
done < "$W/unit2.out"

# ───────────── 3. 接线:过滤器必须挂在真正的读入收口点上 ─────────────
echo "3) 接线与静态守卫"
has "PromptSession 显式传 input=" "$(sed -n '/session = PromptSession(/,/^    )/p' "$HELP")" "input=_guarded_input(),"
has "CPR 计数挂在 output.write_raw 上" "$(sed -n '/session = PromptSession(/,/^    )/p' "$HELP")" "output=_counting_output(create_output(stdout=tty)),"
has "_guarded_input 走 vt100_parser.feed 收口" "$(sed -n '/^def _guarded_input/,/^    return inp/p' "$HELP")" "vt100_parser"
has "_guarded_input 不兼容时退回 None(版本漂移不崩)" "$(sed -n '/^def _guarded_input/,/^    return inp/p' "$HELP")" "return None"
hasnt "过滤层不动 prompt_toolkit 类本身(只包实例)" "$HELP" "Vt100Input.feed ="
# 输入框**之前**不许有大面积 drain:tty_drain_responses 会把用户打字期预输入的按键扣进
# tty_hold 变量(那是给子命令 stdin 用的),开框前调它 = 吃掉流式回答期间的预输入。
if [[ -f "$REAL" ]]; then
    _launch=$(grep -n 'python3 "\$PTK_HELPER"' "$REAL" | head -1 | cut -d: -f1)
    if [[ -n "$_launch" ]]; then
        _before=$(sed -n "$((_launch - 6)),${_launch}p" "$REAL")
        hasnt "开框前不做 tty_drain_responses(会吞预输入)" "$_before" "tty_drain_responses"
    else
        bad "定位输入助手启动行" "wchat.sh 里找不到 python3 \"\$PTK_HELPER\""
    fi
    # 命令窗口退出时那一次 drain 必须还在(它清的是窗口期内已到达的应答)
    has "命令窗口收尾仍排一次队" "$(sed -n '/窗口模式运行命令/,/^}/p' "$REAL")" "tty_drain_responses"
fi

# ───────────── 4-5. 端到端:真起一次输入助手,把应答喂进 pty ─────────────
# 单元层能判字符串,判不了"这个 input= 到底生效没有"。这里用 pty 起真进程,
# 从 master 侧写一段"终端应答"(模拟前一个程序留下的残留),再写用户按键,看助手
# 交回 bash 的文本是什么 —— 与用户截图同一形态即判红。
DRIVER="$W/driver.py"
cat > "$DRIVER" <<'PY'
import os, pty, sys, time, fcntl, termios, select, signal
HELPER, mode = sys.argv[1], sys.argv[2]
RESP = b"\x1b]11;rgb:2828/2c2c/3434\x1b\\"      # Ghostty 对 OSC 11 的应答,25 字节
signal.alarm(40)                                 # 硬止损:绝不让闸门挂死
master, slave = pty.openpty()
out_r, out_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 1)
    os.dup2(slave, 0); os.dup2(out_w, 1); os.dup2(slave, 2)
    for fd in (master, slave, out_r, out_w):
        try: os.close(fd)
        except OSError: pass
    os.execve(sys.executable, [sys.executable, HELPER, "--no-commands",
                               "--history", "", "--clip-dir", ""],
              dict(os.environ, TERM="xterm-ghostty", LINES="24", COLUMNS="100",
                   PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"))
os.close(slave); os.close(out_w)
frames = bytearray(); result = bytearray(); alive = {master, out_r}; cpr = [0]

def pump(dur):
    end = time.time() + dur
    while time.time() < end and alive:
        try:
            r, _, _ = select.select(sorted(alive), [], [], 0.05)
        except (OSError, ValueError):
            break
        for fd in r:
            try: chunk = os.read(fd, 65536)
            except OSError: chunk = b""
            if not chunk:
                alive.discard(fd); continue
            (frames if fd == master else result).extend(chunk)
            # PTK 自己发 CPR 查询;不当假终端答它,它会打警告并把缓冲搞乱(实测)
            if fd == master:
                want = frames.count(b"\x1b[6n")
                while cpr[0] < want:
                    cpr[0] += 1
                    os.write(master, b"\x1b[2;1R")

# 残留的真实时序是"开框前就压在 pty 队列里"。leftover 类必须先写后起框:
# 若等框画出来再写,陈旧的 ESC[..R 会赶在 PTK 自己的 CPR 之后吃掉配额,
# 那就不再是在测残留,而是在测时序运气。
if mode == "leftover":
    os.write(master, RESP * 4)
elif mode == "leftover_dsr":
    os.write(master, RESP * 4 + b"\x1b[24;1R")
pump(1.0)
if mode == "typing":
    os.write(master, b"h"); pump(0.3); os.write(master, RESP); pump(0.4); os.write(master, b"i")
elif mode == "clean":
    os.write(master, b"hello")
else:                                   # leftover / leftover_dsr:上面已预写,这里只补按键
    os.write(master, b"hello")
pump(0.4)
os.write(master, b"\r\r"); pump(1.5)    # 空行回车 = 提交
try:
    os.kill(pid, signal.SIGTERM)
except ProcessLookupError:
    pass
for _ in range(20):
    p, st = os.waitpid(pid, os.WNOHANG)
    if p:
        break
    time.sleep(0.05)
else:
    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
# 结果文本本身可能含真换行,会把字段切开 → 转义后再交回 bash 比较。
# 残留只对"框自己的渲染帧"判:PTK 进 raw+noecho 之前,预写在队列里的字节会被行规程
# 回显到屏上(真终端同样如此),那不是过滤层能拦的东西,算成泄漏会永久误红。
raw_at = frames.find(b"\x1b[?2004h")           # PTK 启用 bracketed paste = 已进 raw
scr = frames[raw_at:] if raw_at >= 0 else frames[frames.find(b"\x1b[?25l"):]
leak = (b"rgb:2828" in scr or b"rgb:2828" in result or b"24;1R" in result)
print("RESULT\t%s\t%s" % (bytes(result).decode("utf-8", "replace").encode("unicode_escape").decode(),
                          "LEAK" if leak else "CLEAN"))
print("CPRWARN\t%s" % ("yes" if b"doesn't support cursor position" in frames else "no"))
print("CPRQ\t%d" % frames.count(b"\x1b[6n"))
PY
e2e_ok=1
if ! "$PYBIN" -c "import prompt_toolkit" 2>/dev/null; then
    echo "  ⚠ 跳过端到端 2 例:本机 python3 没有 prompt_toolkit(单元断言仍有效)"
    e2e_ok=0
fi
if (( e2e_ok )); then
    echo "4) 端到端:pty 队列里已有 4 条应答(前一个程序留下的) → 输入框干净"
    "$PYBIN" "$DRIVER" "$HELP" leftover > "$W/e1.out" 2>&1
    got=$(awk -F'\t' '/^RESULT/{print $2}' "$W/e1.out")
    leak=$(awk -F'\t' '/^RESULT/{print $3}' "$W/e1.out")
    ck "交回 bash 的文本恰好是 hello" "$got" 'hello\n'
    ck "屏上无应答残留" "$leak" "CLEAN"
    # 这条钉住"DSR 不过滤"的不对称:谁要是照抄 bash 判据把 ESC[..R 也丢了,PTK 拿不到
    # 自己 CPR 的应答,这里立刻变红 —— 而上面几条"应答丢弃"用例全绿,抓不到它。
    ck "PTK 的 CPR 仍能拿到应答(无降级警告)" "$(awk -F'\t' '/^CPRWARN/{print $2}' "$W/e1.out")" "no"
    has "PTK 确实发了 CPR(证明这条断言不是空跑)" "$(awk -F'\t' '/^CPRQ/{print $2}' "$W/e1.out")" "1"
    "$PYBIN" "$DRIVER" "$HELP" leftover_dsr > "$W/e4.out" 2>&1
    ck "掺入陈旧 DSR 后交回的文本仍是 hello" "$(awk -F'\t' '/^RESULT/{print $2}' "$W/e4.out")" 'hello\n'
    ck "掺入陈旧 DSR 后屏上无残留" "$(awk -F'\t' '/^RESULT/{print $3}' "$W/e4.out")" "CLEAN"
    ck "掺入陈旧 DSR 后 PTK 的 CPR 仍未被误杀" "$(awk -F'\t' '/^CPRWARN/{print $2}' "$W/e4.out")" "no"
    echo "5) 端到端:应答夹在真按键中间 → 丢应答留按键"
    "$PYBIN" "$DRIVER" "$HELP" typing > "$W/e2.out" 2>&1
    ck "交回的文本恰好是 hi" "$(awk -F'\t' '/^RESULT/{print $2}' "$W/e2.out")" 'hi\n'
    ck "屏上无应答残留" "$(awk -F'\t' '/^RESULT/{print $3}' "$W/e2.out")" "CLEAN"
    echo "6) 端到端:没有应答时正常输入不受影响"
    "$PYBIN" "$DRIVER" "$HELP" clean > "$W/e3.out" 2>&1
    ck "交回的文本恰好是 hello" "$(awk -F'\t' '/^RESULT/{print $2}' "$W/e3.out")" 'hello\n'
    ck "屏上无 CPR 降级警告" "$(awk -F'\t' '/^CPRWARN/{print $2}' "$W/e3.out")" "no"
fi

echo
(( fail > 0 )) && { echo "❌ 终端应答泄漏闸门:$fail 条红"; rm -rf "$W"; exit 1; }
echo "✅ 终端应答泄漏闸门全部通过"
rm -rf "$W"
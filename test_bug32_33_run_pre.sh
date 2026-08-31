#!/opt/homebrew/bin/bash
# 提取自 /tmp/ct_pre_fix.sh 的 kill_tree+run_with_window 测试桩
TOOL_OUTPUT_MAX_COL=2000; WIN=10; THROTTLE=50000; DISP_COL=300; PENDING_MAX=262144
OUT_HEAD_LINES=50; OUT_BUF_SIZE=300; BG_SILENT_TIMEOUT=3000000; INT_FORCE_TIMEOUT=2000000
PTY_STYLE=none; BASH=/opt/homebrew/bin/bash; window_output=""; input_aborted=0
handle_ctrl_c() { input_aborted=1; }
trap handle_ctrl_c INT
kill_tree() {
    local pid=$1 child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        kill_tree "$child"
    done
    kill "$pid" 2>/dev/null
}

# 窗口模式运行命令
run_with_window() {
    local cmd="$1"
    local -a buf=()
    local total=0 drawn=0
    local now last_paint=0 dirty=0
    local chunk rc line pending="" partial=""
    local _full="" _tail=""
    local cmd_pid="" interrupted=0 old_trap="" win_fd="" win_tmpdir=""
    local cmd_stat="" last_data=0 bg_leak=0 forced=0 win_pgid=""
    local in_fd="" key="" tty_save="" tty_cols="" tty_rows="" wrapped_cmd=""
    # 环形缓冲(见 OUT_BUF_SIZE 配置注释):每行追加 O(1),防 O(n²) 卡死
    local -a tail_buf=()
    local tail_idx=0 out_head="" tail_count=0 tstart=0 ti
    # 全量输出通过全局变量回传给调用方（窗口只显示末尾 WIN 行）
    window_output=""

    # 开启 job control:让 & 启动的子命令(尤其管道)进入独立进程组。
    # bash 5 对异步管道的实现中,管道成员不再是 $! 的后代(pgrep -P 父链断裂,
    # kill_tree 找不到孩子,只剩孤儿攥着 FIFO 写端,EOF 永远不来,Ctrl+C 杀不掉)。
    # 按进程组杀是业界标准做法(Claude Code 同款)。
    set -m

    shopt -s extglob
    # 藏光标防闪烁；关自动换行 → 超宽行（含中文宽字符）在右边缘直接裁剪，
    # 光标不会掉到下一行，"上移 drawn 行"的算术就永远是对的
    printf '\033[?25l\033[?7l'

    # 剥掉 \r 和 CSI 转义序列（子命令的进度条/颜色码会画烂窗口）
    clean_line() {
        line=${line//$'\r'/}
        # ANSI 序列剥离必须用 +([0-9;?]) 而非 *([0-9;?]):可空重复 + 后置
        # 锚定字符会让 bash 的 extglob 替换回溯爆炸(超长 ANSI 行实测卡死
        # 数十秒,CPU 100% 且 INT 杀不掉)。真实序列必带参数([1;32m 等),+ 合法。
        line=${line//$'\e'\[+([0-9;?])[[:alpha:]]/}
        # BSD script 在 stdin EOF 时向 pty 回显的 "^D" + 退格残留;
        # 先剥定向残留再剥散落的退格(窗口无法正确渲染 \b)
        line=${line//'^D'$'\b\b'/}
        line=${line//$'\b'/}
    }

    # drawn = 已画的完整行数；光标始终停在窗口底行（partial 行）上
    paint() {
        local i
        printf '\r'
        (( drawn > 0 )) && printf '\033[%dA' "$drawn"
        for i in "${buf[@]}"; do
            # \033[2K 整行擦除，清掉上一帧更长内容的残留。
            # 行尾显式 \r\n:不依赖终端 ONLCR 把 \n 翻译成回车+换行——
            # ONLCR 被关闭时 \n 只换行不回行首,下一行会从上一行文本结尾
            # 处续打,多行输出级联缩进、排版错乱(历史 bug)
            printf '\033[2K\033[2m│\033[0m %s\r\n' "$i"
        done
        # 底行：交互提示等无换行输出画在这里，光标停在其末尾方便用户应答
        printf '\033[2K'
        if [[ -n "$partial" ]]; then
            printf '\033[2m│\033[0m \033[33m%s\033[0m\033[?25h' "$partial"
        else
            printf '\033[?25l'
        fi
        drawn=${#buf[@]}
    }

    # 用 FIFO + 后台任务代替进程替换 <(...)：$! 能拿到子 shell PID，
    # 卡死时 Ctrl+C 可精准杀掉子命令。
    # 不用 coproc 的原因：coproc 一结束 bash 会 unset 其数组并关掉读端 fd，
    # read 会撞上 "invalid file descriptor"；FIFO 读端由我们自己控制，EOF 语义干净。
    win_tmpdir=$(mktemp -d)
    mkfifo "$win_tmpdir/fifo" || return 1
    # 输入 FIFO:子命令的 stdin 从这里读,由主 shell 代读 tty 后转发进来。
    # 后台进程组自己读 tty 会被 SIGTTIN 整树停住(npm create vite 实测
    # bash/npm/node 全部 T 状态挂死);主 shell 是前台进程组,读 tty 合法。
    # <> 打开:读写两端同时就位永不阻塞,且窗口存活期间写端常开,
    # 子命令的交互式提示会等待输入而非读到 EOF 直接返回。
    mkfifo "$win_tmpdir/in" || return 1
    exec {in_fd}<>"$win_tmpdir/in"
    # pty 的窗口尺寸默认 0×0(stdin 非 tty 时 script 无从继承):clack 等 UI 库
    # 会按宽度 0 折行,每字符一行。命令前注入 stty 把真实终端尺寸写进 pty slave
    tty_cols=$(tput cols 2>/dev/null); tty_rows=$(tput lines 2>/dev/null)
    wrapped_cmd="stty cols ${tty_cols:-120} rows ${tty_rows:-40} 2>/dev/null; $cmd"
    # 套 pty 启动(按启动时探测的语法家族分发):子命令把 stdout 当终端 →
    # 行缓冲,输出实时进 FIFO(实测:裸 FIFO 下 python 等程序憋到退出才吐)。
    case "$PTY_STYLE" in
        bsd)
            # stdin 经 cat 中转成匿名管道:macOS 的 FIFO 基于 socket 实现,
            # BSD script 对其 tcgetattr 会致命报错(管道只报 ENOTTY,被容忍)
            cat "$win_tmpdir/in" | script -q /dev/null "$BASH" -c "$wrapped_cmd" > "$win_tmpdir/fifo" 2>&1 & ;;
        util-linux)
            # util-linux script 用 $SHELL 跑命令,强制指到当前 bash;-e 透传退出码
            SHELL="$BASH" script -qec "$wrapped_cmd" /dev/null <"$win_tmpdir/in" > "$win_tmpdir/fifo" 2>&1 & ;;
        python)
            # 不用 pty.spawn:macOS 上子进程退出后它会挂在 master 读取,EOF 不来。
            # pty.fork + select 双向转发:master→stdout 及 stdin→master,
            # 读到 EOF/EIO(Linux 子进程退出报 EIO)都干净退出
            python3 -c '
import os, pty, select, sys
pid, fd = pty.fork()
if pid == 0:
    os.execvp(sys.argv[1], [sys.argv[1], "-c", sys.argv[2]])
stdin_open = True
while True:
    rfds = [fd] + ([0] if stdin_open else [])
    try:
        r, _, _ = select.select(rfds, [], [])
    except OSError:
        break
    if fd in r:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        os.write(1, data)
    if 0 in r:
        d = os.read(0, 65536)
        if d:
            os.write(fd, d)
        else:
            stdin_open = False
os.waitpid(pid, 0)' "$BASH" "$wrapped_cmd" <"$win_tmpdir/in" > "$win_tmpdir/fifo" 2>&1 & ;;
        *)
            # 无 pty 可用的兜底:丢实时性,但 stdin 同样接 FIFO,交互转发仍可用
            eval "$cmd" <"$win_tmpdir/in" > "$win_tmpdir/fifo" 2>&1 & ;;
    esac
    cmd_pid=$!
    exec {win_fd}<"$win_tmpdir/fifo"   # 读端与子命令的写端 rendezvous，不会死锁

    # 备份原 INT trap（通常是 handle_ctrl_c），窗口期间换成"只杀子命令"：
    # - 第一次 Ctrl+C：先按进程组整组 TERM(job control 下子命令在独立进程组),
    #   再用 kill_tree 递归兜底(杀不在组内的散兵),写端全部关闭 → EOF → 窗口正常收尾;
    # - 第二次 Ctrl+C：兜底恢复原 trap 并重新触发，走 handle_ctrl_c 整体退出。
    # trap 能在 0.1s 内执行的前提是下面 read 的 -t 轮询超时；无限阻塞的 read 会
    # 把 trap 推迟到 read 完成才执行（死锁），这里不存在。
    old_trap=$(trap -p INT)
    trap 'if (( interrupted )); then eval "$old_trap"; kill -INT $$; else interrupted=1; win_pgid=$(ps -o pgid= -p "$cmd_pid" 2>/dev/null | tr -d " "); [[ -n "$win_pgid" ]] && kill -TERM -- "-$win_pgid" 2>/dev/null; kill_tree "$cmd_pid"; fi' INT

    # 关本地回显:用户按键由子命令的 pty 回显(行提示原样回、菜单 UI 重画)
    # 后经输出 FIFO 画到窗口,避免 tty 与 pty 双重回显;ISIG 仍开,Ctrl+C 不受影响
    if [[ -t 0 ]]; then tty_save=$(stty -g); stty -echo; fi

    last_data=${EPOCHREALTIME/./}
    while :; do
        chunk=""
        # 按块读而非按行读：交互提示通常不带换行，按行 read 永远等不到 \n，
        # 用户只能面对空窗口；-N+超时能把无换行的残段也取出来实时展示
        IFS= read -r -t 0.1 -N 65536 -u "$win_fd" chunk
        rc=$?
        # 代读 tty:把用户按键(含方向键等转义序列)转发进子命令 stdin。
        # read -N 临时切非规范模式,无换行的字节也能立即取到
        if [[ -n "$tty_save" ]]; then
            key=""
            IFS= read -r -t 0.01 -N 4096 key
            if [[ -n "$key" ]]; then
                # 真实终端的 Enter 发 \r,本 tty 的 ICRNL 已转成 \n;raw 模式 UI
                # (vite/clack 等)只认 \r,还原后发送(规范模式侧 pty 会再转回 \n)
                key=${key//$'\n'/$'\r'}
                printf '%s' "$key" >&"$in_fd"
            fi
        fi
        if [[ -n "$chunk" ]]; then
            pending+="$chunk"
            # 无换行超长行:pending 限长只留尾部,保证每轮循环 O(1),
            # 信号(Ctrl+C)能在命令边界及时处理(见 PENDING_MAX 配置注释)
            (( ${#pending} > PENDING_MAX )) && pending="${pending: -PENDING_MAX}"
            # 批量拆行:while read 逐行(C 层实现,整体 O(n))。旧实现逐行
            # ${pending#*$'\n'} 每拆一行都全量复制剩余 pending——chunk 内含
            # 数千短行时(seq 输出/编译日志)该操作 O(n²),成为继
            # window_output += 之后的第二个卡死点(实测 10 万行 93 秒)。
            # 残段切分必须显式分三种情况:以 \n 结尾(全完整行)/含 \n 但尾段
            # 无换行(切残段)/完全无 \n(整个是残段)。坑:${pending%$'\n'*}
            # 在无 \n 时因不匹配返回原值,会把整个残段误当完整行做昂贵的
            # clean_line,实测直接卡死(CPU 100% 且两次 INT 杀不掉)。
            if [[ "$pending" == *$'\n' ]]; then
                _full="$pending"
                _tail=""
            elif [[ "$pending" == *$'\n'* ]]; then
                _tail="${pending##*$'\n'}"
                _full="${pending%$'\n'*}"
            else
                _tail="$pending"
                _full=""
            fi
            while IFS= read -r line; do
                # 先截长行再清洗:clean_line 的 ANSI 剥离是模式替换,在超长行
                # (256KB 含数千颜色码,idf.py build 真实行为)上即使 +() 也慢,
                # 截到 2000 字符后微秒级。环形缓冲本来就只留截断后的行。
                (( ${#line} > TOOL_OUTPUT_MAX_COL )) && line="${line:0:$TOOL_OUTPUT_MAX_COL}…(该行过长已截断)"
                clean_line
                # 环形缓冲追加(O(1)):头 N 行另存,最近 OUT_BUF_SIZE 行进环形
                total=$((total + 1))
                if (( total <= OUT_HEAD_LINES )); then
                    out_head+="$line"$'\n'
                fi
                tail_buf[$tail_idx]="$line"
                tail_idx=$(( (tail_idx + 1) % OUT_BUF_SIZE ))
                # 显示缓冲只存可视宽度:超长行若全量进 buf,paint 每帧向终端喷数 MB
                buf+=("${line:0:$DISP_COL}")
                (( ${#buf[@]} > WIN )) && buf=("${buf[@]:1}")
            done < <(printf '%s' "$_full")
            pending="$_tail"
            # 底行 partial 同样只清洗/显示可视宽度,保证每轮循环 O(1),
            # 信号能在命令边界及时处理
            line="${pending:0:$DISP_COL}"; clean_line; partial="$line"
            dirty=1
            last_data=${EPOCHREALTIME/./}
        fi
        # 超时退出码 >128（继续等）；1~128 是 EOF（命令结束）
        (( rc > 0 && rc <= 128 )) && break
        now=${EPOCHREALTIME/./}
        # 硬承诺:用户已按 Ctrl+C(杀组已执行)后,若 EOF 仍未到来,静默超时强制
        # 收尾。无论杀组是否成功、写端是否被孤儿攥着,最多再卡 INT_FORCE_TIMEOUT。
        if (( interrupted )); then
            if (( now - last_data > INT_FORCE_TIMEOUT )); then
                forced=1
                break
            fi
        fi
        # 兜底:命令主体已退出(变僵尸/被回收)但 FIFO 写端仍被其后台子进程
        # 握着(AI 在命令里用 & 启动服务),EOF 永远不会来。静默超时提前收尾,
        # 防止窗口永久挂死。正常慢命令的进程还活着,不会触发。
        if [[ -n "$cmd_pid" ]]; then
            cmd_stat=$(ps -o stat= -p "$cmd_pid" 2>/dev/null | tr -d ' ')
            if [[ -z "$cmd_stat" || "$cmd_stat" == Z* ]]; then
                if (( now - last_data > BG_SILENT_TIMEOUT )); then
                    bg_leak=1
                    break
                fi
            fi
        fi
        if (( dirty && now - last_paint > THROTTLE )); then
            paint
            last_paint=$now
            dirty=0
        fi
    done

    # 恢复回显与原 INT trap，关闭两个 FIFO 端并清理临时目录
    [[ -n "$tty_save" ]] && stty "$tty_save"
    [[ -n "$old_trap" ]] && eval "$old_trap" || trap - INT
    eval "exec $win_fd<&-"
    eval "exec $in_fd<&-"
    rm -rf "$win_tmpdir"

    # 收尾：无换行的残段按一行落盘（清洗后为空的不算，如子进程退出时打的转义序列）
    if [[ -n "$pending" ]]; then
        # 先截长行再清洗(同拆行循环:pending 最大 256KB,直接在原值上做
        # clean_line 的模式替换可能回溯爆炸)
        line="${pending:0:$TOOL_OUTPUT_MAX_COL}"; clean_line
        if [[ -n "$line" ]]; then
            total=$((total + 1))
            if (( total <= OUT_HEAD_LINES )); then
                out_head+="$line"$'\n'
            fi
            tail_buf[$tail_idx]="$line"
            tail_idx=$(( (tail_idx + 1) % OUT_BUF_SIZE ))
            buf+=("${line:0:$DISP_COL}")
            (( ${#buf[@]} > WIN )) && buf=("${buf[@]:1}")
        fi
    fi
    # 环形缓冲重组为最终输出:头 OUT_HEAD_LINES 行 + 省略计数 + 最近
    # OUT_BUF_SIZE 行。一次性拼接,无逐行 O(n²) 追加。
    window_output="$out_head"
    tail_count=0
    if (( total > OUT_HEAD_LINES )); then
        tail_count=$((total - OUT_HEAD_LINES))
        (( tail_count > OUT_BUF_SIZE )) && tail_count=$OUT_BUF_SIZE
    fi
    tstart=$(( (tail_idx - tail_count + OUT_BUF_SIZE) % OUT_BUF_SIZE ))
    if (( total > OUT_HEAD_LINES + tail_count )); then
        window_output+=$'\n'"... (中间 $((total - OUT_HEAD_LINES - tail_count)) 行已省略) ..."$'\n'
    fi
    for ((ti = 0; ti < tail_count; ti++)); do
        window_output+="${tail_buf[$(( (tstart + ti) % OUT_BUF_SIZE ))]}"$'\n'
    done
    partial=""
    paint    # 节流可能吞掉最后的内容，收尾必须补画一帧
    printf '\033[?25h\033[?7h'
    if (( forced )); then
        printf '\033[2m└─ 已强制停止(输出管道未关闭,停止等待) · 共 %d 行 ─┘\033[0m\n' "$total"
        # 告诉模型:命令被用户中断,且部分后台进程可能仍在运行
        window_output+=$'\n(命令已被用户中断。若后台仍有残留进程,可用 ps 查询并清理。)'
    elif (( bg_leak )); then
        printf '\033[2m└─ 命令主体已结束,但输出管道仍被后台进程占用,已停止等待 ─┘\033[0m\n'
        # 告诉模型:疑似用 & 启动了长驻服务,应改用 run_in_background=true
        window_output+=$'\n(检测到命令使用了 & 后台符号,输出管道被后台进程占用。若为长驻服务,请改用 run_in_background=true 参数重新启动。)'
    elif (( interrupted )); then
        printf '\033[2m└─ 已中断 · 共 %d 行 ─┘\033[0m\n' "$total"
        # 告诉模型命令是被用户中断的，而不是自然跑完
        window_output+=$'\n(命令已被用户中断)'
    else
        printf '\033[2m└─ 完成 · 共 %d 行 ─┘\033[0m\n' "$total"
    fi
}

# 工具 skill
# ===== harness 主逻辑(由 build 脚本拼接到提取的函数之后)=====
# 场景:
#   A  :双击 Ctrl+C —— 命令忽略 TERM 持续输出,第一下杀不掉,第二下应立即收尾
#   A2 :单击 Ctrl+C —— 正常命令,窗口应正常收尾且不置 input_aborted(本轮继续)
#   B  :窗口结束后 —— 进程替换 < <(sleep 100) 应回到主 shell 进程组,
#       Ctrl+C 能杀掉它让 read 返回(修复前 monitor 泄漏会导致永久阻塞)
case "${SCENARIO:-A}" in
    A)
        input_aborted=0
        # TERM/INT 全部忽略,持续每 0.5s 输出一行:第一下 Ctrl+C 杀不掉,
        # 且不断有数据(旧实现的 2 秒静默强制收尾也被持续输出挡住)
        run_with_window 'echo started; trap "" TERM INT; for i in $(seq 1 30); do echo tick; sleep 0.5; done'
        echo "WINDOW_RETURNED input_aborted=$input_aborted monitor=$([[ $- == *m* ]] && echo ON || echo OFF)"
        ;;
    A2)
        input_aborted=0
        run_with_window 'echo started; sleep 30'
        echo "WINDOW_RETURNED input_aborted=$input_aborted monitor=$([[ $- == *m* ]] && echo ON || echo OFF)"
        ;;
    B)
        input_aborted=0
        run_with_window 'echo wstart; sleep 0.2'
        echo "WINDOW_RETURNED monitor=$([[ $- == *m* ]] && echo ON || echo OFF)"
        # 验证 job control 恢复初始态:修复后 monitor=OFF,与程序启动时一致,
        # bash 会给 & 后台子进程置 SIG_IGN(SIGINT),^C 杀不掉它(BG_FIRED
        # 照常出现);若 set -m 泄漏(修复前),后台子进程继承 SIG_DFL,^C 会把
        # 它杀掉,BG_FIRED 缺失 → FAIL
        ( sleep 1.0; echo BG_FIRED ) &
        sleep 3            # 驱动器在此睡眠期间发 ^C
        echo "POST_INTERRUPT"
        sleep 1.5          # 保持存活,让 BG_FIRED 落盘
        echo "HARNESS_MAIN_END"
        ;;
esac
echo "HARNESS_DONE"

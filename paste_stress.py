#!/usr/bin/env python3
"""压测:分块慢粘贴复现粘行/错乱。模拟真实终端粘贴(块状到达+随机延迟)。"""
import os, pty, select, time, random, sys

REPRO = "/Users/manjioko/shell-scripts/paste_repro.sh"
BASH = "/opt/homebrew/bin/bash"

# 粘贴内容:含普通行、空行、行尾反斜杠行、@ 行,覆盖特殊判断
PASTE_LINES = [
    "Hello World from FreeRTOS task!",
    "Task created successfully",
    "",
    "path = /home/user/project/build/",
    "I (273) main_task: Returned from app_main()",
]
# 大内容:30 行代码块
BIG_LINES = [f"# line {i:02d} of pasted text with some content {i*i}" for i in range(30)]
BIG_LINES[7] = ""
BIG_LINES[15] = "trailing backslash \\"
BIG_LINES[22] = "@"

def make_bytes(lines):
    # 终端粘贴通常 \r\n 行尾(CRLF)
    return ("\r\n".join(lines)).encode() + b"\r\n"

def send_chunked(fd, data, min_chunk=3, max_chunk=18, min_delay=0.003, max_delay=0.04, seed=None):
    rnd = random.Random(seed)
    i = 0
    while i < len(data):
        n = rnd.randint(min_chunk, max_chunk)
        # 写前等可写:pty 输入队列约 1KB,readline 1s 延迟期间不消费,
        # 一次性写大内容会永久阻塞(master 写满)。真实终端有 XOFF 流控
        # 无此问题,这里是压测模拟的代价。
        piece = data[i:i+n]
        while piece:
            _, w, _ = select.select([], [fd], [], 5)
            if not w:
                raise TimeoutError("master write blocked")
            try:
                k = os.write(fd, piece)
            except BlockingIOError:
                continue
            piece = piece[k:]
        i += n
        time.sleep(rnd.uniform(min_delay, max_delay))

SEND_KW = {}

def run_once(mode, seed, lines=None):
    if lines is None:
        lines = PASTE_LINES
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(BASH, [BASH, REPRO])

    def drain(dur):
        buf = b""
        end = time.time() + dur
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    d = os.read(fd, 65536)
                except OSError:
                    return buf
                if not d:
                    return buf
                buf += d
        return buf

    drain(0.6)  # 启动
    if mode == "cont":
        os.write(fd, b"nihao\\\r")
        time.sleep(0.5)
        drain(0.2)
    send_chunked(fd, make_bytes(lines), seed=seed, **SEND_KW)
    # 等脚本处理完粘贴(连续 0.6s 无新输出)再提交
    text = b""
    quiet = 0
    deadline = time.time() + 30
    while time.time() < deadline:
        out = drain(0.3)
        if out:
            text += out
            quiet = 0
        else:
            quiet += 1
            if quiet >= 2:
                break
    os.write(fd, b"\r")
    # 收集直到 INPUT 段出现
    deadline = time.time() + 10
    while time.time() < deadline:
        out = drain(0.3)
        if out:
            text += out
            if b"=== END INPUT ===" in text:
                break
    text = text.decode(errors="replace")
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    # 提取 input
    start = text.find("=== INPUT ===")
    end = text.find("=== END INPUT ===")
    if start < 0 or end < 0:
        return None, text[-500:]
    seg = text[start+13:end].replace("\r", "").strip("\n")
    return seg, None

def check(seg, mode, lines=None):
    if lines is None:
        lines = PASTE_LINES
    got_lines = seg.split("\n")
    # input 末尾有多余空元素(结尾 \n 或空行提交),去掉尾部空串
    while got_lines and got_lines[-1] == "":
        got_lines.pop()
    if mode == "cont":
        # 允许 nihao\ 前缀
        if got_lines and got_lines[0] == "nihao\\":
            got_lines.pop(0)
    tail = got_lines
    # 严格:长度必须一致(多空行会被抓出)
    if len(tail) != len(lines):
        return (f"行数不符: 期望 {len(lines)} 实际 {len(tail)} "
                f"实际={tail!r}")
    problems = []
    for e, g in zip(lines, tail):
        if e != g:
            problems.append(f"期望 {e!r} 实际 {g!r}")
    return "; ".join(problems) if problems else None

def main():
    args = sys.argv[1:]
    rounds = 30
    # 可选参数: round数 min_chunk max_chunk min_delay max_delay [big]
    if args:
        rounds = int(args[0])
    if len(args) >= 5:
        SEND_KW.update(min_chunk=int(args[1]), max_chunk=int(args[2]),
                       min_delay=float(args[3]), max_delay=float(args[4]))
    big = len(args) >= 6 and args[5] == "big"
    lines = BIG_LINES if big else PASTE_LINES
    fails = 0
    for mode in ("cont", "direct"):
        print(f"===== 模式: {'先 \\ 续行' if mode == 'cont' else '直接粘贴'} "
              f"({'大内容' if big else '小内容'}) =====")
        for seed in range(rounds):
            seg, raw = run_once(mode, seed, lines)
            if seg is None:
                print(f"seed={seed}: 未拿到 INPUT 段")
                fails += 1
                continue
            err = check(seg, mode)
            if err:
                fails += 1
                print(f"seed={seed} FAIL: {err}")
                print(f"  input 内容: {seg.splitlines()!r}")
        print(f"模式完成")
    print(f"\n总计失败: {fails}/{rounds*2}")

if __name__ == "__main__":
    main()

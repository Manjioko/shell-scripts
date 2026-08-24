#!/usr/bin/env python3
"""pty 复现多行粘贴三种方式,打印终端回显全貌"""
import os, pty, select, sys, time

TEXT = (b"Hello World from FreeRTOS task!\r\n"
        b"Task created successfully\r\n"
        b"Task deleted successfully\r\n"
        b"I (273) main_task: Returned from app_main()")

def run_case(name, send_fn, tail=1.2):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("/opt/homebrew/bin/bash",
                  ["/opt/homebrew/bin/bash", "/Users/manjioko/shell-scripts/paste_repro.sh"])
    out = b""
    t_end = time.time() + 2.5
    while time.time() < t_end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                break
            if not d:
                break
            out += d
            # 第一个 ".. " 提示符后发送输入
            if b".. " in out and b"SENT" not in out:
                pass
    # 手动驱动:等第一个提示符
    out = b""
    def drain(dur):
        nonlocal out
        end = time.time() + dur
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    d = os.read(fd, 65536)
                except OSError:
                    return
                if not d:
                    return
                out += d
    drain(0.5)  # 启动输出
    os.write(fd, b"nihao\\\r")
    drain(0.4)
    out = b""  # 清空,只看粘贴后的回显
    send_fn(fd)
    drain(tail)
    print("=" * 60)
    print(f"=== 场景: {name} ===")
    print(out.decode(errors="replace").replace("\r\n", "\\r\\n\n"))
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    # 杀掉可能残留的子进程
    os.system("pkill -f paste_repro.sh 2>/dev/null")

# A: 纯大块(无括号序列)
run_case("A: 纯大块无尾换行", lambda fd: os.write(fd, TEXT), 1.2)

# B: 带括号粘贴序列
run_case("B: 括号粘贴序列包裹",
         lambda fd: os.write(fd, b"\x1b[200~" + TEXT + b"\x1b[201~"), 1.2)

# C: 逐行带延迟(慢速粘贴)
def slow(fd):
    for part in TEXT.split(b"\r\n"):
        os.write(fd, part + b"\r\n")
        time.sleep(0.06)
run_case("C: 逐行带延迟", slow, 1.5)

#!/usr/bin/env python3
"""pty 下测 raw + read -t -N 超时"""
import os, pty, select, time

pid, fd = pty.fork()
if pid == 0:
    os.execvp("/opt/homebrew/bin/bash",
              ["/opt/homebrew/bin/bash", "/Users/manjioko/shell-scripts/test_read_raw.sh"])

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

# 不写任何输入,观察超时行为
time.sleep(1.0)
out = drain(1.5)
print("无输入:", repr(out.decode(errors='replace')))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass

# 场景2:0.05s 后写一点字节
pid, fd = pty.fork()
if pid == 0:
    os.execvp("/opt/homebrew/bin/bash",
              ["/opt/homebrew/bin/bash", "/Users/manjioko/shell-scripts/test_read_raw.sh"])
time.sleep(0.5)
drain(0.2)
time.sleep(0.08)
os.write(fd, b"abc")
time.sleep(0.3)
out = drain(1.5)
print("延迟写入:", repr(out.decode(errors='replace')))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass

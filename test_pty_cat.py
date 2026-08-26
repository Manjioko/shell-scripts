#!/usr/bin/env python3
"""验证 pty 字节传输:cat 读原始字节"""
import os, pty, select, time

# 1) 默认终端 cat -v
pid, fd = pty.fork()
if pid == 0:
    os.execvp("/bin/cat", ["/bin/cat", "-v"])
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
time.sleep(0.3)
os.write(fd, b"a\rb\nc")
time.sleep(0.2)
out = drain(0.3)
print("默认终端 cat -v:", repr(out))
os.close(fd)
os.waitpid(pid, 0)

# 2) 子进程先 stty raw 再 cat
pid, fd = pty.fork()
if pid == 0:
    os.execvp("/opt/homebrew/bin/bash", ["/opt/homebrew/bin/bash", "-c", "stty raw; exec /bin/cat -v"])
time.sleep(0.3)
os.write(fd, b"a\rb\nc")
time.sleep(0.2)
out = drain(0.3)
print("stty raw + cat -v:", repr(out))
os.close(fd)
os.waitpid(pid, 0)

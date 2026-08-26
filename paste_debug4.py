#!/usr/bin/env python3
"""完整输出诊断"""
import os, pty, select, time, random, sys
sys.path.insert(0, "/Users/manjioko/shell-scripts")
import paste_stress as ps

pid, fd = pty.fork()
if pid == 0:
    os.execvp(ps.BASH, [ps.BASH, ps.REPRO])

def drain(dur):
    global allout
    buf = b""
    end = time.time() + dur
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                allout += buf
                return buf
            if not d:
                allout += buf
                return buf
            buf += d
    allout += buf
    return buf

allout = b""
drain(0.6)
os.write(fd, b"nihao\\\r")
time.sleep(0.5)
drain(0.2)
ps.send_chunked(fd, ps.make_bytes(ps.PASTE_LINES), seed=0)
time.sleep(1.0)
drain(0.2)
os.write(fd, b"END\r")
time.sleep(0.6)
drain(1.5)
text = allout.decode(errors="replace")
print(text.replace("\r", "<CR>"))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass

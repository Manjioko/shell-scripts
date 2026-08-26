#!/usr/bin/env python3
"""手动:粘完等 2s 发 Enter,看 INPUT"""
import os, pty, select, time, sys
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
os.write(fd, ps.make_bytes(ps.PASTE_LINES))
t = time.time()
drain(2.5)
print(f"--- 粘后 2.5s 输出({time.time()-t:.1f}s) ---")
print(repr(allout[-400:]))
os.write(fd, b"\r")
drain(2.0)
print(f"--- Enter 后 2s ---")
print(repr(allout[-600:]))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass

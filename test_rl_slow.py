#!/usr/bin/env python3
"""最小化:慢流逐字节发 'hello\\r',read -e 多久 accept"""
import os, pty, select, time

pid, fd = pty.fork()
if pid == 0:
    os.execvp("/opt/homebrew/bin/bash", ["/opt/homebrew/bin/bash", "-c", r'''
stty -isig intr undef
t0=$EPOCHREALTIME
IFS= read -e -r -p "> " line
rc=$?
t1=$EPOCHREALTIME
echo "READLINE_RC=$rc LINE=[$line] ELAPSED=$(awk "BEGIN{print $t1-$t0}")"
'''])

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

time.sleep(0.5)
drain(0.2)
# 逐字节慢发 hello\r,每字节间隔 30ms
for i, c in enumerate(b"hello\r"):
    os.write(fd, bytes([c]))
    time.sleep(0.03)
time.sleep(0.5)
out = drain(1.0)
print("场景A(30ms/字节):", repr(out.decode(errors='replace')))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass

# 场景B:hello 一次发,\r 延迟 1s 再发
pid, fd = pty.fork()
if pid == 0:
    os.execvp("/opt/homebrew/bin/bash", ["/opt/homebrew/bin/bash", "-c", r'''
stty -isig intr undef
t0=$EPOCHREALTIME
IFS= read -e -r -p "> " line
rc=$?
t1=$EPOCHREALTIME
echo "READLINE_RC=$rc LINE=[$line] ELAPSED=$(awk "BEGIN{print $t1-$t0}")"
'''])
time.sleep(0.5)
drain(0.2)
os.write(fd, b"hello")
time.sleep(1.0)
os.write(fd, b"\r")
time.sleep(0.5)
out = drain(1.0)
print("场景B(\\r延迟1s):", repr(out.decode(errors='replace')))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass

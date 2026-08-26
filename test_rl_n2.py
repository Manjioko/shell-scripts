#!/usr/bin/env python3
"""观察:bash -c 里 stty raw + read -t 的行为"""
import os, pty, select, time

pid, fd = pty.fork()
if pid == 0:
    os.execvp("/opt/homebrew/bin/bash", ["/opt/homebrew/bin/bash", "-c", r'''
t0=$EPOCHREALTIME
stty raw
t1=$EPOCHREALTIME
read -r -t 0.4 -N 50 chunk
rc=$?
t2=$EPOCHREALTIME
printf "[rc=%s got=%q stty=%s dt1=%.3f dt2=%.3f]\n" "$rc" "$chunk" "$(stty -g | head -c 30)" "$(awk "BEGIN{print $t1-$t0}")" "$(awk "BEGIN{print $t2-$t1}")"
stty sane
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
time.sleep(0.15)
out = drain(0.05)
os.write(fd, b"a\rb\nc")   # read 进入前写入
print("写入后 0.3s 输出:", repr(drain(0.3).decode(errors="replace")))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass

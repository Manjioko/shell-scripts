#!/usr/bin/env python3
"""对比:快速/慢速输入下 read -e 返回耗时"""
import os, pty, select, time

def run(label, sendfn):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("/opt/homebrew/bin/bash", ["/opt/homebrew/bin/bash", "-c", r'''
stty -isig intr undef
t0=$EPOCHREALTIME
IFS= read -e -r -p "> " line
rc=$?
t1=$EPOCHREALTIME
echo "ELAPSED=$(awk "BEGIN{print $t1-$t0}") LINE=[$line]"
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
    sendfn(fd)
    time.sleep(0.3)
    out = drain(1.5)
    print(f"{label}: {out.decode(errors='replace')!r}")
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

run("一次发完 hello\\r", lambda fd: os.write(fd, b"hello\r"))
run("hello 与 \\r 间隔 200ms", lambda fd: (os.write(fd, b"hello"), time.sleep(0.2), os.write(fd, b"\r")))
run("逐字节 50ms", lambda fd: [os.write(fd, bytes([c])) or time.sleep(0.05) for c in b"hello\r"])

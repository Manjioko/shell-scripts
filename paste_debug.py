#!/usr/bin/env python3
"""单轮诊断:修复后 paste_repro 的原始输出"""
import os, pty, select, time

pid, fd = pty.fork()
if pid == 0:
    os.execvp("/opt/homebrew/bin/bash",
              ["/opt/homebrew/bin/bash", "/Users/manjioko/shell-scripts/paste_repro.sh"])

def drain(dur):
    buf = b""
    end = time.time() + dur
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                return buf
            if not d:
                return buf
            buf += d
    return buf

print("--- 启动 ---")
print(repr(drain(0.8)))
os.write(fd, b"nihao\\\r")
time.sleep(0.4)
print("--- 续行轮后 ---")
print(repr(drain(0.6)))
os.write(fd, b"Hello World from FreeRTOS task!\r\n"
             b"Task created successfully\r\n"
             b"Task deleted successfully\r\n"
             b"I (273) main_task: Returned from app_main()\r\n")
time.sleep(0.5)
print("--- 粘贴后 ---")
print(repr(drain(1.5)))
os.write(fd, b"END\r")
time.sleep(0.3)
print("--- END 后 ---")
print(repr(drain(2.0)))
try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass
print("--- 完成 ---")

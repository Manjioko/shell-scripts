#!/usr/bin/env python3
"""分块慢发送单轮诊断"""
import os, pty, select, time, random

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
time.sleep(0.5)
print("--- 续行轮后 ---")
print(repr(drain(0.8)))

data = b"Hello World from FreeRTOS task!\r\n" \
       b"Task created successfully\r\n" \
       b"Task deleted successfully\r\n" \
       b"I (273) main_task: Returned from app_main()\r\n"
rnd = random.Random(7)
i = 0
sent_at = time.time()
while i < len(data):
    n = rnd.randint(2, 12)
    os.write(fd, data[i:i+n])
    i += n
    time.sleep(rnd.uniform(0.02, 0.15))
print(f"--- 粘贴发送完成,耗时 {time.time()-sent_at:.2f}s ---")
print("--- 发送后 0.5s ---")
print(repr(drain(0.5)))
print("--- 再 1.5s ---")
print(repr(drain(1.5)))
os.write(fd, b"END\r")
print("--- END 后 2s ---")
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

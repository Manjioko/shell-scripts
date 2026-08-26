#!/usr/bin/env python3
"""压测循环内跑 seed=11,保留日志"""
import os, sys, time
sys.path.insert(0, "/Users/manjioko/shell-scripts")
import paste_stress as ps

ps.SEND_KW.update(min_chunk=3, max_chunk=18, min_delay=0.003, max_delay=0.04)
for seed in range(11, 13):
    seg, raw = ps.run_once('direct', seed)
    print(f"seed={seed}: seg={seg!r}")
    # 把日志复制出来
    dbg = open(os.environ.get("TMPDIR", "/tmp") + "/paste_dbg.log", "rb").read()
    open(os.environ.get("TMPDIR", "/tmp") + f"/dbg_seed{seed}.log", "wb").write(dbg)
print("完成")

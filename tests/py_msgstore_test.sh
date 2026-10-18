#!/usr/bin/env bash
# W12-4d：`wchat_py/msgstore.py`（messages 的文件 store）闸门。
# 它守的是**语义**，不是实现：
#   · 回滚 = 截断到快照长度（O(1)），且**非法长度必须原样返回**（静默截到错位置 = 悄悄丢消息）；
#   · 写文件必须**原子**（半截 JSON 会让下次启动读不出历史 —— 本项目最贵的一类故障）；
#   · 读损坏文件必须**抛错**（静默返回空会把"读不出历史"伪装成"新会话"）。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }
py() { python3 - "$RLDIR" "$@"; }

# 单个 python 进程内跑完一条场景，输出一个可比较的短串（避免多层引号打架）。
run() { py <<'PY' 2>&1 | tail -1
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
from wchat_py.msgstore import load, save, snapshot, restore
print(eval(sys.argv[2]))
PY
}

ck "缺失 → 返回 init"        "$(py <<'PY' 2>&1 | tail -1
import os,sys,tempfile; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import load
print(len(load(os.path.join(tempfile.mkdtemp(),"m.json"), [{"a":1}])))
PY
)" "1"

ck "save/load 往返"          "$(py <<'PY' 2>&1 | tail -1
import os,sys,tempfile,json; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import load,save
d=tempfile.mkdtemp(); p=os.path.join(d,"m.json")
save(p,[{"role":"system","content":"s"},{"role":"user","content":"hi"}])
print(len(load(p)))
PY
)" "2"

ck "回滚=截断到快照长度"     "$(py <<'PY' 2>&1 | tail -1
import sys; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import snapshot,restore
m=[{"r":1}]; n=snapshot(m); m2=m+[{"r":2},{"r":3}]
print(len(restore(m2,n)))
PY
)" "1"

ck "非法长度原样返回(不静默截断)" "$(py <<'PY' 2>&1 | tail -1
import sys; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import restore
m=[{"r":1},{"r":2}]
print(str(restore(m,99) is m)+","+str(restore(m,-1) is m))
PY
)" "True,True"

ck "损坏文件抛错(不静默返回空)" "$(py <<'PY' 2>&1 | tail -1
import os,sys,tempfile; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import load
p=os.path.join(tempfile.mkdtemp(),"m.json"); open(p,"w").write("{坏")
try:
    load(p); print("no-raise")
except Exception:
    print("raise")
PY
)" "raise"

ck "原子写:无临时文件残留"   "$(py <<'PY' 2>&1 | tail -1
import os,sys,tempfile; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import save
d=tempfile.mkdtemp(); save(os.path.join(d,"m.json"),[{"a":1}])
print([x for x in os.listdir(d) if x.startswith(".msgs_")]==[])
PY
)" "True"

ck "全量替换语义(不被旧内容拼尾)" "$(py <<'PY' 2>&1 | tail -1
import os,sys,tempfile; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import load,save
d=tempfile.mkdtemp(); p=os.path.join(d,"m.json")
save(p,[{"a":1},{"a":2}]); save(p,[{"b":1}])
print(len(load(p)))
PY
)" "1"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]


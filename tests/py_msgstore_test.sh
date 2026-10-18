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

# ── 接线前必须证明的两件事(否则一切换,旧历史全读不出) ────────────────────────
# 1) 能读 bash/jq 现在写出的格式:jq 默认输出是 **2 空格缩进的多行 JSON**(不是紧凑格式)。
ck "能读 jq 的缩进格式历史" "$(py <<'PY' 2>&1 | tail -1
import os,sys,tempfile; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import load
p=os.path.join(tempfile.mkdtemp(),"h.json")
open(p,"w").write('{\n  "messages": [\n    {\n      "role": "system",\n      "content": "s"\n    }\n  ]\n}\n')
print(len(load(p)))
PY
)" "1"

# 2) 往返不改变内容(逐字节):store 写出去的,再读回来必须与内存里一致。
#    注意比较的是 **JSON 语义等价 + 字节一致** —— 未来若有人给 save 加 indent/排序,
#    历史文件的字节形态就变了(diff/备份/回放都会受影响),这条会红。
ck "save→load 逐字节往返" "$(py <<'PY' 2>&1 | tail -1
import os,sys,tempfile,json; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import load,save
d=tempfile.mkdtemp(); p=os.path.join(d,"m.json")
m=[{"role":"system","content":"中文 s"},{"role":"user","content":"hi"}]
save(p,m); once=open(p,"rb").read(); save(p,load(p)); twice=open(p,"rb").read()
print("same" if once==twice and json.loads(once)==m else "diff")
PY
)" "same"

# 历史文件是**对象**形状(带 thinking/model/title 兄弟字段)。save 若直接写数组,
# 这些字段会被静默抹掉 —— 表现是"续聊时思考模式变回默认/标题丢了",且没有任何报错。
ck "save 保留对象里的兄弟字段" "$(py <<'PY2' 2>&1 | tail -1
import os,sys,tempfile,json; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import load,save
p=os.path.join(tempfile.mkdtemp(),"h.json")
open(p,"w").write(json.dumps({"messages":[{"role":"system","content":"s"}],"thinking":{"type":"enabled"},"model":"m1","title":"t"}))
save(p,[{"role":"system","content":"s"},{"role":"user","content":"hi"}])
d=json.load(open(p))
print("%d,%s,%s,%s" % (len(d["messages"]), d["thinking"]["type"], d["model"], d["title"]))
PY2
)" "2,enabled,m1,t"

# CLI 入口(bash 接线就走它)。⚠ 上一版把"建文件→save→jq"拆成三条 shell 命令,结果
# 断言红而手工对 —— 教训:**这类有先后状态的检查要在一个 python 进程里跑完**,别让 shell 的
# 求值顺序/变量作用域插进来。下面每条都是自包含的。
ck "CLI load 只出 messages" "$(py <<'PY2' 2>&1 | tail -1
import json,os,subprocess,sys,tempfile
R=sys.argv[1]; p=os.path.join(tempfile.mkdtemp(),"h.json")
open(p,"w").write('{"messages":[{"a":1}],"thinking":{"type":"x"}}')
r=subprocess.run([sys.executable,os.path.join(R,"wchat_py","_run.py"),"msgstore","load",p],
                 capture_output=True,text=True)
print(len(json.loads(r.stdout)) if r.stdout else "empty")
PY2
)" "1"

ck "CLI save 保留兄弟字段" "$(py <<'PY2' 2>&1 | tail -1
import json,os,subprocess,sys,tempfile
R=sys.argv[1]; p=os.path.join(tempfile.mkdtemp(),"h.json")
open(p,"w").write('{"messages":[{"a":1}],"thinking":{"type":"x"},"title":"T"}')
subprocess.run([sys.executable,os.path.join(R,"wchat_py","_run.py"),"msgstore","save",p],
               input='[{"b":1},{"c":2}]', text=True, check=True)
d=json.load(open(p))
print("%d,%s,%s" % (len(d["messages"]), d["thinking"]["type"], d["title"]))
PY2
)" "2,x,T"

ck "CLI 坏 stdin → rc=2" "$(py <<'PY2' 2>&1 | tail -1
import os,subprocess,sys,tempfile
R=sys.argv[1]; p=os.path.join(tempfile.mkdtemp(),"h.json")
r=subprocess.run([sys.executable,os.path.join(R,"wchat_py","_run.py"),"msgstore","save",p],
                 input="not-json", text=True, capture_output=True)
print(r.returncode)
PY2
)" "2"

# 新文件必须能带上兄弟字段(否则"新会话存盘"会丢 thinking/model/title —— 无报错)。
ck "新文件带兄弟字段" "$(py <<'PY2' 2>&1 | tail -1
import os,sys,tempfile,json; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import save
p=os.path.join(tempfile.mkdtemp(),"new.json")
save(p,[{"role":"system","content":"s"}], {"thinking":{"type":"enabled"},"model":"m","title":"t"})
d=json.load(open(p)); print("%d,%s,%s,%s" % (len(d["messages"]), d["thinking"]["type"], d["model"], d["title"]))
PY2
)" "1,enabled,m,t"
ck "已有文件:siblings 覆盖同名键" "$(py <<'PY2' 2>&1 | tail -1
import os,sys,tempfile,json; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import save
p=os.path.join(tempfile.mkdtemp(),"h.json")
open(p,"w").write('{"messages":[],"thinking":{"type":"disabled"},"title":"old"}')
save(p,[{"a":1}], {"thinking":{"type":"enabled"}})
d=json.load(open(p)); print("%d,%s,%s" % (len(d["messages"]), d["thinking"]["type"], d["title"]))
PY2
)" "1,enabled,old"

# save_doc:整份对象原子写(save_history 用)。半截 JSON = 历史读不出,所以断"写坏不留残骸"。
ck "save_doc 整份写 + 无残留" "$(py <<'PY2' 2>&1 | tail -1
import os,sys,tempfile,json; sys.path.insert(0,sys.argv[1])
from wchat_py.msgstore import save_doc
d=tempfile.mkdtemp(); p=os.path.join(d,"h.json")
save_doc(p, {"messages":[{"a":1}],"thinking":{"type":"x"},"model":"m","title":"t"})
x=json.load(open(p))
print("%d,%s,%s,%s,%s" % (len(x["messages"]), x["thinking"]["type"], x["model"], x["title"],
                          [f for f in os.listdir(d) if f.startswith(".msgs_")]==[]))
PY2
)" "1,x,m,t,True"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]
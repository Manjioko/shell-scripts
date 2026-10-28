#!/usr/bin/env python3
"""Python 版本一致性闸门:shebang 管不了版本,那就必须有别的东西管。

## 起因(用户问的那个问题)

`wchat` 的第一行是 `#!/usr/bin/env python3`。用户在 brew 安装的上下文里问:
这行会不会让我们用错 Python?这个问题问得对,但它有两半,答案不一样:

  · **brew 装的 `wchat` 根本不读这行**:Formula 生成的 `bin/wchat` 是个 bash wrapper
    `exec "<venv>/bin/python" "<libexec>/wchat" "$@"` —— 解释器由 wrapper 指定,
    脚本路径只是参数。所以 shebang 在这条路上**一次都没有被用过**。
    (本闸门 §2 用实验证明,不是靠读代码下结论。)
  · **但仓库直跑 `./wchat` 读它**,而 `env python3` 解析到的是 PATH 上第一个
    python3 —— 本机实测是 CommandLineTools 的 **3.9.6**,而 brew 依赖的是 3.12。
    同一条代码因此有两个运行时,且**没有任何一处会报错**。

`#!/usr/bin/env python3` 这个写法本身没法表达"要 >= 3.9",更没法表达"brew 那份 3.12"。
所以判据不能落在 shebang 上,只能落在**启动时的版本校验**上(§3),并且要求
**所有入口的 shebang 保持一致**(§1,至少不能出现"两个入口两套解释器")。

## 这道闸门守什么

1. shebang 唯一形态:`#!/usr/bin/env python3`;`MIN_PYTHON` 只有一处定义;
2. brew 形态**不依赖** shebang(实验:把 shebang 换成不存在的路径,用 venv python
   当脚本参数照跑;直接执行则失败 —— 两个方向都证);
3. 低于 `MIN_PYTHON` 的解释器**不许**被选来跑业务/建 venv(纯函数判据 + 结构锁);
4. `_runtime_fallback` 的候选要过同一套校验(否则子进程会 ImportError);
5. 代码与声明的下限**真的对得上**:用声明的下限版本 compileall 全包(可选,慢)。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["WCHAT_API_LOG"] = "0"
os.environ["WCHAT_NO_SETUP"] = "1"

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


print("=== 1. shebang 只有一种形态,最低版本只有一个出处 ===")
SHEBANG = "#!/usr/bin/env python3"
ENTRIES = ["wchat", "configs/ctmodel.py", "wchat_py/inputbox.py",
           "wchat_py/mdrender.py", "wchat_py/installdeps.py"]
for rel in ENTRIES:
    first = read(rel).split("\n")[0].strip()
    ck("%s 用统一 shebang" % rel, first == SHEBANG, repr(first))
# 全部 .py 的 shebang 也一致(少一个不致命,但混进 #!/usr/bin/python 就会分裂)
bad = []
for root, _dirs, files in os.walk(os.path.join(REPO, "wchat_py")):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(root, fn), encoding="utf-8") as f:
            first = f.readline().strip()
        if first.startswith("#!") and first != SHEBANG:
            bad.append("%s: %s" % (fn, first))
ck("wchat_py/*.py 的 shebang 全部一致", not bad, repr(bad[:3]))

from wchat_py import bootstrap, version   # noqa: E402

ck("MIN_PYTHON 是元组 (major, minor)", isinstance(version.MIN_PYTHON, tuple)
   and len(version.MIN_PYTHON) == 2, repr(version.MIN_PYTHON))
ck("bootstrap 直接用 version.MIN_PYTHON(不是抄一份)",
   bootstrap.MIN_PY is version.MIN_PYTHON, "两处各有一份 → 迟早漂移")
# 别处不许再写一份字面量下限
dups = []
for root, _dirs, files in os.walk(os.path.join(REPO, "wchat_py")):
    for fn in files:
        if not fn.endswith(".py") or fn == "version.py":
            continue
        src = open(os.path.join(root, fn), encoding="utf-8").read()
        if re.search(r"MIN_PY\s*=\s*\(\s*3\s*,", src):
            dups.append(fn)
ck("其它模块没有另抄一份 MIN_PY", not dups, repr(dups))

print("=== 2. 实验:brew 形态不读 shebang(两个方向都证) ===")
tmp = tempfile.mkdtemp(prefix="pyver-")
bogus = os.path.join(tmp, "wchat")
src = read("wchat").split("\n")
src[0] = "#!/nonexistent/python9"
with open(bogus, "w", encoding="utf-8") as f:
    f.write("\n".join(src) + "\n")
os.chmod(bogus, 0o755)
# 造一个最小可运行环境:能 import wchat_py 即可(--version 只读 version.py)
shutil.copytree(os.path.join(REPO, "wchat_py"), os.path.join(tmp, "wchat_py"),
                ignore=shutil.ignore_patterns("__pycache__"))
shutil.copy(os.path.join(REPO, "requirements.txt"), tmp)

py = sys.executable
r = subprocess.run([py, bogus, "--version"], capture_output=True, text=True,
                   cwd=tmp, env=dict(os.environ, WCHAT_NO_BOOTSTRAP="1"))
ck("brew 形态(shebang 坏掉)→ 照跑",
   r.returncode == 0 and "wchat " in r.stdout, repr((r.stdout, r.stderr))[:200])
# 直接执行:内核读 shebang,bad interpreter 时 OSError(不是 rc!=0)。两种形态都算"失败",
# 断言写成"没有正常输出"而不是具体 errno —— 不同 shell/内核给出的错法不一样。
try:
    r2 = subprocess.run([bogus, "--version"], capture_output=True, text=True, cwd=tmp,
                        env=dict(os.environ, WCHAT_NO_BOOTSTRAP="1"))
    _bad_exec = (r2.returncode != 0) or ("wchat " not in (r2.stdout or ""))
    _detail = repr((r2.returncode, (r2.stderr or "")[:80]))
except OSError as e:
    _bad_exec = True
    _detail = "OSError: %s" % e
ck("直接执行同一份 → 因 shebang 失败(证明 shebang 只在直跑时生效)",
   _bad_exec, _detail)

print("=== 3. 低于下限的解释器不许被采用(纯判据) ===")
ck("_py_ok((3,8)) 为假", bootstrap._py_ok((3, 8)) is False)
ck("_py_ok(MIN_PYTHON) 为真", bootstrap._py_ok(version.MIN_PYTHON) is True)
ck("_py_ok(()) 为假(测不出版本 ≠ 合格)", bootstrap._py_ok(()) is False)
ck("当前解释器版本达标", bootstrap._py_ok(sys.version_info[:2]),
   "当前 %s" % (sys.version_info[:3],))
# 真跑一次子进程问版本(证明 _py_ver 不是空转)
_ver = bootstrap._py_ver(sys.executable)
ck("_py_ver 能问出真实版本", _ver[:2] == tuple(sys.version_info[:2]), repr(_ver))
ck("不存在的解释器判不合格", bootstrap._suitable("/nonexistent/python9") is False)
ck("不可执行的文件判不合格",
   bootstrap._suitable(os.path.join(REPO, "README.md")) is False)

print("=== 4. 结构锁:选解释器的地方都必须过校验 ===")
bs = read("wchat_py/bootstrap.py")
_gate = "if not _py_ok(sys.version_info[:2]):"
ck("ensure_runtime 里有版本门", _gate in bs, "找不到版本门:3.9 会被当成可用解释器")
ck("版本门在依赖检查之前",
   _gate in bs and "if _deps_ok()" in bs and bs.index(_gate) < bs.index("if _deps_ok()"),
   "顺序反了:3.9 上'依赖齐全'会掩盖'不该用这个解释器'")
ck("_base_python 每个候选都过 _suitable", "_suitable(c)" in bs)
ck("_base_python 不再\"存在即用\"", "os.path.isfile(cand)" not in bs)
rt = read("wchat_py/runtime.py")
ck("_runtime_fallback 也过 _suitable", "_suitable(c)" in rt,
   "只看 exists 会退到没有 openai 的 /usr/bin/python3")
ck("_runtime_fallback 里 venv 候选排在 /usr/bin/python3 之前",
   rt.index("venv\", \"bin\", \"python\"") < rt.index('"/usr/bin/python3"'))

print("=== 5. 声明与代码对得上:下限版本能编译全包 ===")
# 用**声明的下限**解释器去 compileall。找不到该版本就退化为"用当前解释器",
# 但那样就不算验证了下限 —— 所以这里显式说清楚,不假装测过。
want = "python%d.%d" % version.MIN_PYTHON
low = shutil.which(want)
if not low:
    # 没装带补丁号的命令名时,看 /usr/bin/python3 是不是正好那版本(本机就是 3.9.6)
    for cand in ("/usr/bin/python3", "/opt/homebrew/bin/python3"):
        if os.path.isfile(cand):
            v = bootstrap._py_ver(cand)
            if v and tuple(v[:2]) == tuple(version.MIN_PYTHON):
                low = cand
                break
if low:
    r = subprocess.run([low, "-m", "compileall", "-q", os.path.join(REPO, "wchat_py")],
                       capture_output=True, text=True)
    ck("Python %s 能编译全包(下限可信)" % want, r.returncode == 0,
       (r.stdout + r.stderr)[-300:])
else:
    # 没装下限解释器:改成静态扫描 3.10+ 语法 —— 有就说明下限写错了
    pat = re.compile(r"^\s*match\s+\w+\s*:|isinstance\([^)]*\|[^)]*\)", re.M)
    hits = []
    for root, _dirs, files in os.walk(os.path.join(REPO, "wchat_py")):
        for fn in files:
            if fn.endswith(".py"):
                if pat.search(open(os.path.join(root, fn), encoding="utf-8").read()):
                    hits.append(fn)
    ck("%s 未安装 → 静态扫描无 3.10+ 语法" % want, not hits, repr(hits[:5]))

print("=== 6. 非空转自检:把下限抬到当前版本之上,判定必须反转 ===")
# 证明 §3 的判据不是恒真:同一个 _py_ok,喂一个不可能满足的下限必须返回假。
_orig_min = bootstrap.MIN_PY
try:
    bootstrap.MIN_PY = (99, 0)
    ck("下限抬到 99.0 后当前解释器判不合格",
       bootstrap._py_ok(sys.version_info[:2]) is False,
       "判据恒真,测不出真问题")
finally:
    bootstrap.MIN_PY = _orig_min
ck("恢复下限后判据回到真", bootstrap._py_ok(sys.version_info[:2]) is True)

print("=== 7. 端到端:低于要求的解释器会自己换掉(真跑一次) ===")
# 这是本题的核心行为:把下限临时抬到当前版本之上,ensure_runtime 必须换到更高版本
# 的解释器然后继续跑,而不是"报个错"或"硬着头皮跑"。
newer = None
for cand in ("/opt/homebrew/opt/python@3.13/bin/python3.13",
             "/opt/homebrew/opt/python@3.12/bin/python3.12",
             "/opt/homebrew/bin/python3.12",
             "/usr/local/bin/python3.12"):
    if os.path.isfile(cand) and bootstrap._py_ver(cand)[:2] > tuple(sys.version_info[:2]):
        newer = cand
        break
if not newer:
    print("  --   跳过:本机没有比当前更高的解释器,无法做正向验证")
else:
    probe = os.path.join(REPO, "_pyver_probe_tmp.py")
    probe_src = (
        "import sys, os\n"
        "sys.path.insert(0, " + repr(REPO) + ")\n"
        "from wchat_py import bootstrap\n"
        "bootstrap.MIN_PY = (sys.version_info[0], sys.version_info[1] + 1)\n"
        "bootstrap.ensure_runtime()\n"
        "print('AFTER %d.%d' % sys.version_info[:2])\n")
    with open(probe, "w", encoding="utf-8") as f:
        f.write(probe_src)
    try:
        r = subprocess.run([sys.executable, probe], capture_output=True, text=True,
                           env=dict(os.environ, HOME=os.environ.get("HOME", "")),
                           timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        ck("低于要求时自动换解释器并继续跑", "AFTER" in out and "🔁" in out, out[-300:])
        m = re.search(r"AFTER (\d+)\.(\d+)", out)
        ck("换完的版本确实高于原版本",
           bool(m) and (int(m.group(1)), int(m.group(2))) > tuple(sys.version_info[:2]),
           out[-200:])
        ck("不重复换(re-exec 后不再触发)", out.count("🔁") == 1, out[-200:])
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)


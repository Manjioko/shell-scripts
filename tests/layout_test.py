#!/usr/bin/env python3
"""分层结构闸门:cli.py 不许再长回"上帝模块",分层不许出现循环依赖。

## 为什么要有这道闸门

这个项目从 bash 迁到 Python 时,所有东西先塞进 `wchat_py/cli.py`(入口 + 帮助 +
工具协议 + 审批 + 会话循环 + 配置装配 + 解释器兜底),那是迁移期的合理选择:先搬过来
能跑,再谈结构。搬到 2000 行之后,代价变成真的:

  · 改一个工具的必填校验,要在 2000 行里找位置;
  · `toolrun`/`approve`/`runtime` 三类职责共享同一批模块全局(`_GRANTED_PATHS`、
    `_IMG_REFS`、`_REQUIRED_CACHE`),谁都可能顺手改一条 —— 授权面因此没有单一出处;
  · 任何一处 `import` 顺序变化都可能踩到循环引用(实测 `compact_run`、`render`、
    `repl`、`session` 都曾通过 `from wchat_py.cli import X` 反向依赖入口)。

拆开之后最大的风险是**它自己长回去**:下一个人图省事,又往 `cli.py` 里加一个
`def _something(...)`。所以这道闸门守的是**结构契约**,不是行为:

  1. `cli.py` 行数有上限,且不许重新定义已经切出去的那批函数名;
  2. 分层方向单一:`toolrun/approve/runtime/imgstate` **不许**模块级 import `cli`
     (`toolrun` 的审批接缝必须走调用时解析,否则测试打桩失效);
  3. 任意导入顺序都能成功(循环依赖的实证判据);
  4. `cli.X` 与目标模块的 `X` 必须是**同一个对象**(防"门面里又写了一份实现")。

第 4 条最容易在重构里被破坏,而且破坏了不报错:两份实现各自维护状态,
现象是"授权记了但查不到""图片去重偶发失效"。
"""
import ast
import os
import subprocess
import sys

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


def top_names(src):
    """文件顶层的函数/类/赋值名(不含缩进在函数体里的)。"""
    out = {}
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n.lineno
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.lineno
    return out


print("=== 1. cli.py 不再是上帝模块(行数 + 职责名都不许长回去) ===")
cli_src = read("wchat_py/cli.py")
cli_lines = len(cli_src.split("\n"))
# 上限是"当前值 + 一点余量":不是审美,是防止它悄悄回到 2000 行。
LIMIT = 1250
ck("cli.py 行数 ≤ %d(当前 %d)" % (LIMIT, cli_lines), cli_lines <= LIMIT,
   "又往入口里塞东西了?按职责放到 toolrun/approve/runtime/会话层")
moved = ["_run_tool", "_banner_text", "_required_of", "_missing_required",
         "_args_obj", "_auto_backup", "_trunc_for_context", "_trunc_int",
         "_path_guard", "_grant_path", "_note_path_grants", "_cmd_key",
         "_load_ct_config", "_apply_runtime_defaults", "_detect_web_provider",
         "_runtime_fallback", "_sync_runtime", "_bump_auto", "_auto_summary",
         "_skills_dir_default", "_register_image_url", "_reset_image_refs"]
cli_top = top_names(cli_src)
redefined = sorted(n for n in moved if n in cli_top)
ck("已切出的函数不许在 cli.py 里重新定义", not redefined, repr(redefined))

print("=== 2. 分层方向单一:下层不许模块级 import cli ===")
LAYERS = {"wchat_py/toolrun.py": "工具协议层",
          "wchat_py/approve.py": "授权账本层",
          "wchat_py/runtime.py": "运行期装配层",
          "wchat_py/imgstate.py": "图片账本层"}
for rel, label in LAYERS.items():
    src = read(rel)
    bad = []
    for n in ast.parse(src).body:              # 只看顶层语句,函数内是合法接缝
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("wchat_py"):
            if n.module in ("wchat_py.cli", "wchat_py.repl", "wchat_py.session"):
                bad.append(n.module)
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in ("wchat_py.cli", "wchat_py.repl", "wchat_py.session"):
                    bad.append(a.name)
    ck("%s 顶层不 import cli(%s)" % (rel.split("/")[-1], label), not bad, repr(bad))

print("=== 3. 接缝必须\"调用时解析\"(否则测试打桩静默失效) ===")
tr = read("wchat_py/toolrun.py")
ck("toolrun 有 _seam() 取审批入口", "_seam()" in tr)
ck("toolrun 里审批走 _seam()._auth_think(不是裸名)", "_seam()._auth_think(" in tr)
ck("toolrun 里没有裸 _auth_think( 调用", "\n            _av, _ars = _auth_think(" not in tr)
ck("toolrun 里终审走 _seam()", "_seam()._auto_judge(" in tr)

print("=== 4. 任意导入顺序都成立(循环依赖的实证判据) ===")
MODS = ["approve", "imgstate", "runtime", "toolrun", "cli", "repl", "session", "mcp_boot"]
orders = [MODS, list(reversed(MODS)), ["toolrun", "cli", "approve", "runtime", "imgstate"]]
for i, order in enumerate(orders, 1):
    code = "import sys; sys.path.insert(0, %r)\n" % REPO
    code += "".join("import wchat_py.%s\n" % m for m in order)
    code += "print('OK')\n"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=dict(os.environ, WCHAT_NO_SETUP="1"))
    ck("导入顺序 %d(%s…)成功" % (i, order[0]), r.returncode == 0 and "OK" in r.stdout,
       (r.stderr or "").strip().splitlines()[-1:] and (r.stderr or "").strip().splitlines()[-1][:120])

print("=== 5. 门面再导出必须是同一个对象(防两份实现) ===")
import wchat_py.approve as A          # noqa: E402
import wchat_py.cli as C             # noqa: E402
import wchat_py.imgstate as I        # noqa: E402
import wchat_py.runtime as R         # noqa: E402
import wchat_py.toolrun as T         # noqa: E402

PAIRS = [(C._run_tool, T._run_tool), (C._banner_text, T._banner_text),
         (C._required_of, T._required_of), (C._trunc_for_context, T._trunc_for_context),
         (C._path_guard, A._path_guard), (C._grant_path, A._grant_path),
         (C._runtime_fallback, R._runtime_fallback), (C._load_ct_config, R._load_ct_config),
         (C._register_image_url, I._register_image_url),
         (C._reset_image_refs, I._reset_image_refs)]
for a, b in PAIRS:
    ck("cli.%s is 目标模块的实现" % a.__name__, a is b,
       "两个不同对象 = 两份实现,状态会分叉")
ck("_GRANTED_PATHS 是同一个容器", C._GRANTED_PATHS is A._GRANTED_PATHS)
ck("_IMG_URLS 是同一个容器", C._IMG_URLS is I._IMG_URLS)

print("=== 6. 共享账本用原地修改(重新绑定会让两边永久分叉) ===")
# 判据是"同一个名字在顶层被赋值**两次以上**":第一次是定义,第二次就是重新绑定。
# 写成"出现过即算"会把自己的定义行判红(第一版就踩了这个,断言等于恒真)。
for rel in ("wchat_py/imgstate.py", "wchat_py/approve.py"):
    src = read(rel)
    seen, rebound = {}, []
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id.startswith("_"):
                    seen[t.id] = seen.get(t.id, 0) + 1
                    if seen[t.id] > 1:
                        rebound.append(t.id)
    # 真正会分叉的写法是**函数里 `global X` 然后重新赋值**——模块顶层的第二次赋值
    # 反而一眼能看见。所以两条都要查:顶层重复赋值 + 函数内 global 声明。
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Global):
            for gname in n.names:
                if gname in seen:
                    rebound.append("global " + gname)
    ck("%s 没有把账本重新绑定成新对象" % rel.split("/")[-1], not rebound, repr(rebound))

print("=== 7. 顶层不再有 bash 时代的散装脚本(入口只有 wchat) ===")
STRAY = ["input_helper.py", "render_helper.py", "setup.py", "wchat.sh", "release.sh"]
present = [f for f in STRAY if os.path.exists(os.path.join(REPO, f))]
ck("顶层无遗留脚本", not present, repr(present))
for rel in ("wchat_py/inputbox.py", "wchat_py/mdrender.py", "wchat_py/installdeps.py"):
    ck("已收进包内:%s" % rel.split("/")[-1], os.path.isfile(os.path.join(REPO, rel)))

print("=== 8. 包内引用路径必须跟着搬(写死旧路径 = 静默降级) ===")
prod = ["wchat_py/render.py", "wchat_py/repl.py", "wchat_py/cli.py"]
stale = []
for rel in prod:
    for i, ln in enumerate(read(rel).split("\n"), 1):
        s = ln.strip()
        if s.startswith("#"):
            continue
        for old in ('"input_helper.py"', '"render_helper.py"', '"setup.py"'):
            if old in s:
                stale.append("%s:%d %s" % (rel, i, s[:60]))
ck("生产代码不再引用顶层旧文件名", not stale, repr(stale[:3]))
ck("inputbox 路径指到包内",
   os.path.isfile(os.path.join(REPO, "wchat_py", "inputbox.py")))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)


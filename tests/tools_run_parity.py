#!/usr/bin/env python3
"""B 阶段 1d：`wchat_py/tools_run.py` 五个工具实现 vs bash 的**逐字 parity** 闸门。

覆盖：list_dir / read_file / grep / write_file / edit_file。
判据：工具产出的 content（会进下一轮上下文）+ 屏幕状态行 + 落盘文件字节，三者与 bash 一致。
      这是"Python 能替代 bash 工具"的直接证据；铁律 5（迁一个模块同轮改闸门）。

用法：python3 tests/tools_run_parity.py   →  末行 `RESULT total=N pass=N fail=0`，fail>0 退出码 1。
"""
import json, os, re, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
BASH = os.environ.get("BASH_BIN", "/opt/homebrew/bin/bash")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

from wchat_py.tools_run import list_dir, read_file, grep, write_file, edit_file

_pass = [0, 0]  # [pass, fail]


def ck(name, ok, detail=""):
    _pass[0 if ok else 1] += 1
    if ok:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def _norm(s, d):
    return s.replace("/private" + d, "<ROOT>").replace(d, "<ROOT>")


def bash_tool(tool, d, args, env_extra=None):
    fx = ('cd "$2"; _WCHAT_LIB_ONLY=1 API_KEY=sk-stub; . "$1" < /dev/null > /dev/null; '
          'fn_args="$3"; content=""; tool_%s; printf "###C###%%s" "$content"' % tool)
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + ":" + os.environ.get("PYTHONPATH", "")
    env["WORK_ROOT"] = d
    env.update(env_extra or {})
    out = subprocess.run([BASH, "-c", fx, "_", REPO + "/wchat.sh", d, json.dumps(args)],
                         capture_output=True, env=env).stdout.decode("utf-8", "replace")
    return _norm(ANSI.sub("", out), d)


def py_render(status, content, d):
    return _norm(((status + "\n") if status else "") + "###C###" + content, d)


def _touch(d, files):
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data.encode() if isinstance(data, str) else data)


# ── list_dir ────────────────────────────────────────────────────────────────
def t_list_dir():
    for name, files, dirs in [
        ("有内容", {"a.txt": "x", "b.txt": "y"}, ["sub"]),
        ("空目录", {}, []),
        ("不存在", {}, []),
    ]:
        d = tempfile.mkdtemp(); _touch(d, files)
        for dd in dirs:
            os.makedirs(os.path.join(d, dd), exist_ok=True)
        path = "nope" if name == "不存在" else "."
        args = {"path": path}
        b = bash_tool("list_dir", d, args)
        resolved = os.path.join(d, path)
        c, s = list_dir(resolved, path)
        ck("list_dir %s" % name, b == py_render(s, c, d), "\n  bash=%r\n  py  =%r" % (b, py_render(s, c, d)))


# ── read_file ───────────────────────────────────────────────────────────────
def t_read_file():
    cases = [("存在", "f.txt", "hello\nworld\n", {"path": "f.txt"}),
             ("不存在", None, None, {"path": "nope.txt"}),
             ("是目录", None, None, {"path": "sub"})]
    for name, fn, data, args in cases:
        d = tempfile.mkdtemp()
        if fn:
            _touch(d, {fn: data})
        os.makedirs(os.path.join(d, "sub"), exist_ok=True)
        b = bash_tool("read_file", d, args)
        path = args["path"]; resolved = os.path.join(d, path)
        c, s = read_file(resolved, path, args.get("offset"), args.get("limit"), 30000, d)
        ck("read_file %s" % name, b == py_render(s, c, d), "\n  bash=%r\n  py  =%r" % (b, py_render(s, c, d)))


# ── grep ────────────────────────────────────────────────────────────────────
def t_grep():
    d = os.path.realpath(tempfile.mkdtemp())
    _touch(d, {"a.txt": "alpha\nbeta\ngamma\n", "b.txt": "nomatch\n",
               "sub/c.txt": "alpha in sub\n", "long.txt": "alpha " + "x" * 600 + "\n"})
    cases = [("命中", {"pattern": "alpha"}, 200),
             ("无命中", {"pattern": "zzz"}, 200),
             ("指定文件无命中", {"pattern": "alpha", "path": "b.txt"}, 200),
             ("子目录", {"pattern": "alpha", "path": "sub"}, 200),
             ("路径不存在", {"pattern": "alpha", "path": "nope"}, 200),
             ("限流", {"pattern": "alpha", "max_matches": 1}, 1)]
    for name, args, mm in cases:
        b = bash_tool("grep", d, args)
        c, s = grep(args.get("pattern") or "", args.get("path") or "", mm, 500, d)
        ck("grep %s" % name, b == py_render(s, c, d), "\n  bash=%r\n  py  =%r" % (b, py_render(s, c, d)))


# ── write_file / edit_file（含落盘字节比对）─────────────────────────────────
def _bytes_eq(d1, d2, name):
    p1 = os.path.join(d1, name); p2 = os.path.join(d2, name)
    b1 = open(p1, "rb").read() if os.path.isfile(p1) else None
    b2 = open(p2, "rb").read() if os.path.isfile(p2) else None
    return b1 == b2, b1, b2


def t_write_file():
    cases = [("嵌套目录", "out/x.txt", "hello\nworld", {}),
             ("覆盖", "b.txt", "no newline at end", {"b.txt": "old\n"}),
             ("空内容", "empty.txt", "", {}),
             ("结尾换行", "trail.txt", "a\n", {}),
             ("绝对路径", None, "absolute content", {})]
    for name, rel, text, init in cases:
        d1 = tempfile.mkdtemp(); d2 = tempfile.mkdtemp()
        _touch(d1, init); _touch(d2, init)
        path = rel if rel else os.path.join(d1, "abs.txt")
        pypath = rel if rel else os.path.join(d2, "abs.txt")
        args = {"path": path, "content": text}
        b = bash_tool("write_file", d1, args)
        c, s = write_file(os.path.join(d2, pypath), pypath, text, d2)
        same_txt = (b == py_render(s, c, d2) if path == pypath
                    else _norm(b, d1) == _norm(py_render(s, c, d2), d2))
        # 逐字节：bash 写 d1/path，py 写 d2/pypath
        bp = path if rel else os.path.join(d1, "abs.txt")
        pp = pypath if rel else os.path.join(d2, "abs.txt")
        b1 = open(bp, "rb").read() if os.path.isfile(bp) else None
        b2 = open(pp, "rb").read() if os.path.isfile(pp) else None
        ck("write_file %s" % name, same_txt and b1 == b2,
           "\n  bash=%r\n  py  =%r\n  file=%r/%r" % (b, py_render(s, c, d2), b1, b2))


def t_edit_file():
    cases = [("单替换", "t.txt", "line1\nline2\nline3\n", "line2", "TWO"),
             ("0匹配", "t.txt", "line1\nline2\n", "nope", "X"),
             ("多匹配", "t.txt", "a\na\n", "a", "b"),
             ("空old", "t.txt", "abc\n", "", "X"),
             ("不存在", "missing.txt", None, "a", "b"),
             ("多行替换", "t.txt", "x\nY\nz\n", "Y", "multi\nline")]
    for name, fn, init, old, new in cases:
        d1 = tempfile.mkdtemp(); d2 = tempfile.mkdtemp()
        if init is not None:
            _touch(d1, {fn: init}); _touch(d2, {fn: init})
        args = {"path": fn, "old_string": old, "new_string": new}
        b = bash_tool("edit_file", d1, args)
        c, s = edit_file(os.path.join(d2, fn), fn, old, new, d2)
        eq, b1, b2 = _bytes_eq(d1, d2, fn)
        ck("edit_file %s" % name, b == py_render(s, c, d2) and eq,
           "\n  bash=%r\n  py  =%r\n  file=%r/%r" % (b, py_render(s, c, d2), b1, b2))


def main():
    for fn in (t_list_dir, t_read_file, t_grep, t_write_file, t_edit_file):
        fn()
    print("RESULT total=%d pass=%d fail=%d" % (_pass[0] + _pass[1], _pass[0], _pass[1]))
    return 0 if _pass[1] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
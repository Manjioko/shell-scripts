#!/usr/bin/env python3
"""`wchat mcp add` —— 往清单里加一个 MCP server,不用手改 JSON。

**为什么不直接包 `mcporter config add`**(它有这套参数,而且不联网、秒回):
拿真实的 `~/.ct/mcporter.json` 实测过,四个问题都踩实了:

  1. 丢掉顶层 `_comment` —— 随包模板里那段"绝不许出现明文密钥"的告示会整段消失;
  2. 整份重排 —— 每个 server 内部键序被打乱、缩进改成 2 空格。用户自己的文件变成
     一整片排版 diff,`git blame` 从此全是别人的名字(本项目在 tools_spec.json 上
     刚吃过这个亏);
  3. 权限降级成 644 —— 我们 seed.ensure_manifest() 特意按 600 落盘(里面有 ${VAR}
     引用),上游写完不看权限;
  4. 不认 `allowedTools` —— 我们靠这个字段裁工具,加进来的 server 会把全部工具
     都暴露出去,上下文白涨一截。

所以自己写:只插入 `mcpServers.<name>` 这一段,其余一个字节不动。

**三种形态**(都想得到,所以都得有):
  · 交互:`wchat mcp add` —— 逐项问,最后摊开给你看再问 y/n;
  · 一行式:`wchat mcp add 名字 --url https://…` 或 `--command npx --arg -y --arg pkg`;
  · 会话内:`-mcp add …` 由 repl 转到这里(同一条实现,不另起一套)。
"""
import json
import os
import re
import shutil
import sys

# env/header 里出现这些形状就当作"用户把真密钥写进来了"。模板的硬性要求是只写
# ${VAR} 占位,而清单文件虽然 0600,但它会被复制、被同步、被 git add。
_SECRET_HINT = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def _out(s=""):
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


class _Cancelled(Exception):
    """用户按 Ctrl+C / Ctrl+D 中途放弃。**不是错误**,调用方应安静退出。

    为什么不用 KeyboardInterrupt:它是 BaseException,一旦从 _ask 冒出去,会穿过
    `_do_add` → `main` → `wchat` 入口,Python 打出两段 traceback
    ("During handling of the above exception, another exception occurred")。
    用户只是按了个 Ctrl+C,不该看到这种东西 —— 2026-11 实测就是这么报的。
    """


def _ask(prompt, default=""):
    if not sys.stdin.isatty():
        return default
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        a = (sys.stdin.readline() or "").strip()
    except (KeyboardInterrupt, EOFError):
        # 换行把光标挪到下一行,否则后面的提示会接在 ^C 后面同一行
        sys.stdout.write("\n")
        sys.stdout.flush()
        raise _Cancelled()
    return a or default


def _config_path():
    from wchat_py import mcp_bridge as mb
    return mb.config_path()


def _load(path):
    """读清单。文件不存在时返回空骨架(**不含** _comment:那张告示只属于随包模板)。"""
    if not os.path.isfile(path):
        return {"imports": [], "mcpServers": {}}
    with open(path, encoding="utf-8", errors="replace") as f:
        return json.load(f)


def _dump_preserving(path, data, indent_src, trailing_nl=1):
    """按**原文件的缩进**写回,而不是固定 2/4 空格。

    为什么不 json.dump 一把梭:用户(和我们)的文件是 4 空格 + 紧凑数组风格,
    换成 2 空格会让整份文件变成排版 diff。这里只按原缩进宽度重排,键序和内容
    完全由 data 决定 —— 而 data 是"原字典插入一个新键",顺序天然保持。

    `trailing_nl`:文件末尾**原本**有几个换行。随包模板结尾是 `}\\n\\n`(带一个空行),
    硬写成单换行会让每次 add 都多一条"删掉空行"的无关 diff ——
    本项目在 tools_spec.json 上刚吃过这个亏,这次连空行也照抄。
    """
    text = json.dumps(data, ensure_ascii=False, indent=indent_src)
    try:
        n = int(trailing_nl)
    except (TypeError, ValueError):
        n = 1
    if n > 0:
        text += "\n" * n
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(tmp, 0o600)   # 与 seed.ensure_manifest 同一口径:按凭据级处理
    except OSError:
        pass
    os.replace(tmp, path)


def _trailing_newline(path):
    """原文件末尾有几个换行;文件不存在时按 1(新建文件的常规约定)。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return 1
    n = 0
    for b in reversed(raw):
        if b in (0x0A, 0x0D):
            n += 1
        else:
            break
    return n


def _detect_indent(path):
    """探测原文件缩进宽度(默认 4 —— 与随包模板一致)。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^(\s+)\S", line)
                if m:
                    return len(m.group(1).expandtabs(4))
    except OSError:
        pass
    return 4


def _validate_env(pairs):
    """env/header 的值只允许 ${VAR} 占位或明显不是密钥的字面量。"""
    bad = []
    for k, v in pairs:
        if not isinstance(v, str):
            continue
        if _SECRET_HINT.search(v) and not (v.startswith("${") and v.endswith("}")):
            bad.append(k)
    return bad


def build_entry(kind, url="", command="", args=None, env=None, headers=None,
                description="", allowed=None):
    """按形态拼出 mcporter 认的条目。字段名照上游 `config add` 的实际产物。"""
    e = {}
    if kind == "http":
        e["baseUrl"] = url
    else:
        e["command"] = command
        if args:
            e["args"] = list(args)
    if env:
        e["env"] = dict(env)
    if headers:
        e["headers"] = dict(headers)
    if description:
        e["description"] = description
    if allowed:
        e["allowedTools"] = list(allowed)
    return e


def _preview(name, entry, path):
    _out("")
    _out("即将写入 %s" % path)
    _out("  server 名: %s" % name)
    for k, v in entry.items():
        if k in ("env", "headers"):
            # 只显示键名和"是否占位",不把值打全 —— 值可能长得像凭据
            shown = ", ".join("%s=%s" % (kk, ("${…} 占位" if str(vv).startswith("${") else "(字面值)"))
                              for kk, vv in v.items())
            _out("  %-10s %s" % (k + ":", shown))
        elif k == "args":
            _out("  %-10s %s" % (k + ":", " ".join(str(x) for x in v)))
        else:
            _out("  %-10s %s" % (k + ":", v))
    _out("")


def _parse_flags(argv):
    """一行式的参数解析。手写而不是 argparse:要支持 `--arg` 重复、`--` 后面的透传,
    而 argparse 的 usage/错误文案会挤进中文帮助里,反而更难读。"""
    o = {"kind": "", "url": "", "command": "", "args": [], "env": [], "headers": [],
         "description": "", "allowed": [], "yes": False, "dry": False, "name": "",
         "json": ""}
    i = 0
    rest = []
    while i < len(argv):
        a = argv[i]
        if a == "--":
            rest = [x for x in argv[i + 1:]]
            break
        if a in ("--url",) and i + 1 < len(argv):
            o["url"] = argv[i + 1]; o["kind"] = o["kind"] or "http"; i += 2
        elif a in ("--command", "--stdio") and i + 1 < len(argv):
            o["command"] = argv[i + 1]; o["kind"] = "stdio"; i += 2
        elif a == "--arg" and i + 1 < len(argv):
            o["args"].append(argv[i + 1]); i += 2
        elif a == "--env" and i + 1 < len(argv):
            kv = argv[i + 1]
            if "=" in kv:
                k, v = kv.split("=", 1)
                o["env"].append((k, v))
            i += 2
        elif a == "--header" and i + 1 < len(argv):
            kv = argv[i + 1]
            if "=" in kv:
                k, v = kv.split("=", 1)
                o["headers"].append((k, v))
            i += 2
        elif a == "--json" and i + 1 < len(argv):
            o["json"] = argv[i + 1]; i += 2
        elif a == "--from-json" and i + 1 < len(argv):
            o["json"] = argv[i + 1]; i += 2
        elif a == "--description" and i + 1 < len(argv):
            o["description"] = argv[i + 1]; i += 2
        elif a == "--allow-tools" and i + 1 < len(argv):
            o["allowed"] = [x.strip() for x in argv[i + 1].split(",") if x.strip()]; i += 2
        elif a in ("-y", "--yes"):
            o["yes"] = True; i += 1
        elif a in ("--dry-run", "-n"):
            o["dry"] = True; i += 1
        elif a.startswith("-") and a != "-":
            raise ValueError("不认识的参数: %s" % a)
        else:
            if not o["name"]:
                o["name"] = a
            else:
                rest.append(a)
            i += 1
    if rest:
        # `--` 之后的全部当 stdio 参数(与上游一致),省得一个个写 --arg
        if o["kind"] != "http":
            o["args"].extend(rest)
    return o


def _interactive(name_hint=""):
    """逐项问。返回 (name, entry) 或 None(用户中途取消)。

    `name_hint`:命令行已经给了名字时不再问一遍(只补问缺的那部分)。
    """
    _out("添加一个 MCP server(直接回车 = 用括号里的默认值;Ctrl+C 取消)")
    _out("")
    _out("  ① HTTP/SSE:给出一个 URL,对方是托管服务(如 https://mcp.linear.app/mcp)")
    _out("  ② stdio    :给出一条本地命令,由 wchat 拉起(如 npx -y some-mcp)")
    kind = _ask("形态 [1=HTTP / 2=stdio](默认 1):", "1")
    is_http = kind.strip() not in ("2", "stdio", "s")
    name = name_hint or _ask("server 名(自己起,之后 mcp_call 里用它):", "").strip()
    if not name:
        _out("没给名字,取消。")
        return None
    if is_http:
        url = _ask("URL:", "").strip()
        if not url:
            _out("没给 URL,取消。")
            return None
        entry = build_entry("http", url=url)
    else:
        cmd = _ask("命令(如 npx / uvx / python3):", "").strip()
        if not cmd:
            _out("没给命令,取消。")
            return None
        args_raw = _ask("参数(空格分隔,如 -y some-mcp@1.2.3):", "").strip()
        entry = build_entry("stdio", command=cmd, args=args_raw.split() if args_raw else [])
    desc = _ask("一句用途说明(会进模型看到的清单,可空):", "").strip()
    if desc:
        entry["description"] = desc
    if _ask("要加环境变量吗?[y/N]", "").strip().lower() in ("y", "yes"):
        _out("  一行一个 KEY = 值;值请写 ${VAR} 引用你的环境变量,**不要粘明文密钥**")
        inp = _ask("  (如 TOKEN=${GITHUB_TOKEN}):", "").strip()
        env = []
        for piece in inp.replace(",", "\n").split("\n"):
            if "=" in piece:
                k, v = piece.split("=", 1)
                env.append((k.strip(), v.strip()))
        if env:
            entry["env"] = dict(env)
    return name, entry


def _looks_like_json(s):
    t = (s or "").strip()
    return t[:1] in ("{", "[") and t[-1:] in ("}", "]")


def _entries_from_json(text, name_hint=""):
    """把一段 JSON 转成 [(name, entry)]。

    支持三种形状(都是真实会遇到的):
      ① 整份清单:`{"mcpServers": {"a": {...}, "b": {...}}}` —— 从别家程序/README 直接拷来的
      ② 单个条目 + 名字:`{"name": "a", "command": "npx", "args": [...]}`
      ③ 单个条目不带名字:`{"command": "npx"}` —— 名字取位置参数或 --name
    条目里的字段**原样保留**(用户可能写了 lifecycle/idleTimeoutMs 这类我们没建模的键),
    只把 `url` 归一成 mcporter 认的 `baseUrl`。
    """
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise ValueError("这段不是合法 JSON: %s" % e)
    if not isinstance(obj, dict):
        raise ValueError("最外层要是对象,收到的是 %s" % type(obj).__name__)

    out = []
    if isinstance(obj.get("mcpServers"), dict):
        for n, e in (obj["mcpServers"] or {}).items():
            if isinstance(e, dict):
                out.append((str(n), _normalize_entry(e)))
        if not out:
            raise ValueError("mcpServers 里没有任何条目")
        return out

    e = dict(obj)
    n = e.pop("name", "") or name_hint
    if not n:
        raise ValueError("这段 JSON 里没有 server 名:补一个位置参数,或在 JSON 里写 \"name\"")
    # 别把整份清单的壳当条目
    for shell in ("imports", "_comment"):
        e.pop(shell, None)
    return [(n, _normalize_entry(e))]


def _normalize_entry(e):
    """把常见写法归一到 mcporter 认的字段名;其余键原样带走。"""
    out = dict(e)
    if "url" in out and "baseUrl" not in out:
        out["baseUrl"] = out.pop("url")
    return out


def _do_add(argv):
    try:
        o = _parse_flags(argv)
    except ValueError as e:
        _out("⚠ %s" % e)
        _out("用法见: wchat mcp add -h")
        return 2

    path = _config_path()
    pairs = []                       # [(name, entry)] —— 所有形态最后都收敛到这里

    # ---------------- 形态 0:JSON(最常见:从 README / 别家程序拷一段过来)----------
    if o["json"]:
        raw = o["json"]
        if raw == "-":
            try:
                raw = sys.stdin.read()
            except (KeyboardInterrupt, OSError):
                _out("⚠ 读 stdin 失败")
                return 2
        try:
            pairs = _entries_from_json(raw, o.get("name") or "")
        except ValueError as e:
            _out("⚠ %s" % e)
            return 2
        # JSON 里的条目可能带 url 而不是 baseUrl,统一按 http 处理
        for _n, _e in pairs:
            if _e.get("baseUrl") and not _e.get("command"):
                pass

    # ---------------- 形态 1:一行式 ----------------
    elif o["name"] and (o["url"] or o["command"]):
        if o["kind"] == "http" and o["command"]:
            _out("⚠ --url 与 --command 只能二选一(一个 server 要么远端要么本地命令)")
            return 2
        pairs = [(o["name"], build_entry(
            "http" if o["url"] else "stdio",
            url=o["url"], command=o["command"], args=o["args"],
            env=o["env"], headers=o["headers"],
            description=o["description"], allowed=o["allowed"]))]

    # ---------------- 形态 2:交互(逐项问)----------------
    else:
        # 位置参数其实是粘进来的 JSON:说清楚,别掉进向导等输入(实测会卡住不回)
        if _looks_like_json(o["name"]):
            _out("⚠ 这不是 server 名,是一段 JSON。想直接用它:")
            _out("    wchat mcp add --json '<那段 JSON>'")
            _out("  或从 stdin 喂(免引号转义):")
            _out("    pbpaste | wchat mcp add --json -")
            _out("  或改成一行式: wchat mcp add 名字 --command npx --arg -y --arg 包名")
            return 2
        if not sys.stdin.isatty():
            _out("⚠ 非交互环境:请用一行式(或 --json),例如")
            _out("    wchat mcp add 名字 --command npx --arg -y --arg some-mcp")
            _out("    wchat mcp add 名字 --url https://example.com/mcp")
            _out("    wchat mcp add --json '{\"mcpServers\":{\"x\":{\"command\":\"npx\"}}}'")
            return 2
        got = _interactive(o["name"])
        if not got:
            return 1
        pairs = [got]

    # ---------------- 校验(全部在写盘之前,一次说清)----------------
    for name, entry in pairs:
        bad = _validate_env(list((entry.get("env") or {}).items())
                            + list((entry.get("headers") or {}).items()))
        if bad:
            _out("⚠ 这些值看起来是**真实密钥**(%s)。清单文件会被复制/同步/提交," % ", ".join(bad))
            _out("  请改成 ${环境变量名} 占位,例如 TOKEN=${GITHUB_TOKEN}。")
            _out("  已取消,什么都没写。")
            return 2
        if not re.match(r"^[A-Za-z0-9._-]+$", name or ""):
            _out("⚠ server 名只能用字母/数字/点/下划线/横线: %r" % name)
            return 2
        if not (entry.get("command") or entry.get("baseUrl") or entry.get("url")):
            _out("⚠ '%s' 既没有 command 也没有 url,连不上任何东西。已取消。" % name)
            return 2

    try:
        data = _load(path)
    except ValueError as e:
        # 绝不"顺手修好"用户手写坏的 JSON:那会把他的原内容一起改掉
        _out("⚠ 清单读不了(%s): %s" % (path, e))
        _out("  这份文件不是合法 JSON,先修好它(或备份后删掉,下次启动会重建)。已取消。")
        return 2
    servers = data.setdefault("mcpServers", {})
    dup = [n for n, _ in pairs if n in servers]
    if dup:
        for n in dup:
            _out("⚠ 已存在同名 server '%s': %s"
                 % (n, json.dumps(servers[n], ensure_ascii=False)[:160]))
        _out("  想改它:先 `wchat mcp remove 名字`,再重新 add。")
        _out("  (不提供静默覆盖:覆盖掉的那份配置里可能有你手写的 env 引用)")
        _out("  已取消,什么都没写。")
        return 2

    for name, entry in pairs:
        _preview(name, entry, path)
    if o["dry"]:
        _out("--dry-run:没有写入。")
        return 0
    if not o["yes"] and sys.stdin.isatty():
        if _ask("写入这份清单? [y/N]", "").strip().lower() not in ("y", "yes"):
            _out("已取消,什么都没写。")
            return 1

    # 备份一次:手改 JSON 出错的代价远高于多一个文件(seed 从不覆盖用户文件,这里同理)
    bak = ""
    if os.path.isfile(path):
        try:
            bak = path + ".bak"
            shutil.copyfile(path, bak)
            os.chmod(bak, 0o600)
        except OSError:
            bak = ""
    for name, entry in pairs:
        servers[name] = entry
    try:
        _dump_preserving(path, data,
                         _detect_indent(path) if os.path.isfile(path) else 4,
                         _trailing_newline(path))
    except OSError as e:
        _out("⚠ 写入失败: %s" % e)
        return 1
    _out("✅ 已写入 %s" % path)
    if bak:
        _out("   备份: %s" % bak)
    # 让"下次启动不再需要 -mcp":用户刚刚表达过要用 MCP。
    try:
        from wchat_py import mcp_boot
        mcp_boot.mark_enabled()
    except Exception:
        pass
    _out("")
    _out("下一步:重开 wchat 即可(它已经记住你要用 MCP,不用再带 -mcp)。")
    _out("  想先验证通不通: wchat -mcp=%s" % (pairs[0][0] if len(pairs) == 1
                                              else ",".join(n for n, _ in pairs)))
    return 0


def _do_remove(argv):
    if not argv:
        _out("用法: wchat mcp remove <名字>")
        return 2
    name = argv[0]
    path = _config_path()
    try:
        data = _load(path)
    except ValueError as e:
        _out("⚠ 清单读不了(%s): %s" % (path, e))
        return 2
    servers = data.get("mcpServers") or {}
    if name not in servers:
        _out("清单里没有 '%s'。现有: %s" % (name, ", ".join(servers) or "(空)"))
        return 1
    old = servers.pop(name)
    try:
        bak = path + ".bak"
        shutil.copyfile(path, bak)
        os.chmod(bak, 0o600)
    except OSError:
        bak = ""
    try:
        _dump_preserving(path, data, _detect_indent(path), _trailing_newline(path))
    except OSError as e:
        _out("⚠ 写入失败: %s" % e)
        return 1
    _out("✅ 已从清单移除 '%s'(内容如下,备份在 %s)"
         % (name, bak or "(备份失败)"))
    _out("   %s" % json.dumps(old, ensure_ascii=False)[:200])
    return 0


def _help():
    _out("用法: wchat mcp                 看清单在哪、有哪些 server")
    _out("      wchat mcp add              逐项问(不给任何参数时)")
    _out("      wchat mcp add 名字 --url https://host/mcp")
    _out("      wchat mcp add 名字 --command npx --arg -y --arg some-mcp@1.2.3")
    _out("      wchat mcp add 名字 --command uvx --arg mcp-server-git --env TOKEN=${GITHUB_TOKEN}")
    _out("      wchat mcp add --json '{\"mcpServers\":{\"x\":{\"command\":\"npx\"}}}'")
    _out("      pbpaste | wchat mcp add --json -        从剪贴板喂,免引号转义")
    _out("      wchat mcp remove 名字      从清单移除(先备份)")
    _out("")
    _out("  --json 接受三种形状(从别家程序/README 拷来的都能直接用):")
    _out("    ① 整份清单 {\"mcpServers\":{...}}        ② 单条带 name        ③ 单条不带 name(配位置参数)")
    _out("    条目里的字段原样保留;`url` 会自动归一到 mcporter 认识的 `baseUrl`。")
    _out("")
    _out("  可选: --description 文本 / --allow-tools a,b,c(只暴露这些工具)/")
    _out("        -y(不再确认)/ --dry-run(只看会写什么)")
    _out("")
    _out("  只插入你加的那一条,不动文件里其它内容;写入前自动备份 .bak,权限 600。")
    _out("  env/header 的值只写 ${VAR} 占位 —— 粘明文密钥会被拒绝。")
    _out("  server 名只能用字母/数字/点/下划线/横线(它会是 mcp_call 里 `server.tool` 的前半段)。")
    return 0


def main(argv):
    argv = list(argv or [])
    sub = argv[0] if argv else ""
    # Ctrl+C / Ctrl+D 必须像别的命令一样"安静退出",不能吐栈。
    # 用户按 Ctrl+C 的心理预期是"算了,不弄了",给他一段 KeyboardInterrupt 的
    # traceback 只会让他以为程序坏了(真出现过)。写入是原子的(tmp + os.replace),
    # 中断最多留下一个 .bak,不会留下半截清单。
    try:
        if sub in ("add", "remove", "rm"):
            if len(argv) > 1 and argv[1] in ("-h", "--help", "help"):
                return _help()
            return _do_add(argv[1:]) if sub == "add" else _do_remove(argv[1:])
        if sub in ("-h", "--help", "help"):
            return _help()
    except _Cancelled:
        # 注意:这里**不能**等 KeyboardInterrupt —— _ask 已把它转成 _Cancelled。
        # 之前 _ask 裸抛 KeyboardInterrupt,于是在这里被接住之前,Python 先打了两段栈
        # ("During handling of the above exception…"),用户只是按了个 Ctrl+C。
        _out("")
        _out("已取消,什么都没写。")
        return 130      # 与 -st 中断同一约定:130 = 被用户中断
    except KeyboardInterrupt:
        # 兜底:万一有别的路径直接抛(比如 json 解析时按 Ctrl+C)
        _out("")
        _out("已取消,什么都没写。")
        return 130
    return None      # 交给 seed.cmd_mcp 走只读状态
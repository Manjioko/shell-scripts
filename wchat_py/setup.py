#!/usr/bin/env python3
"""首次运行引导:把"填 API key"这件事压到一次粘贴。

设计目标(来自真实使用体验)
--------------------------
未配凭据时旧行为是直接发请求,用户看到的是 `API 请求失败: Connection error.`
—— 既没说少了什么,也没说怎么补。本模块负责:

  1. **先说清楚缺什么**(缺 key / 缺 BASE_URL / 缺模型),一句话讲完;
  2. **给一行可复制的模板**,把 key 与模型名替换掉即可;
  3. **直接粘回来就能用**:容忍 `KEY=value` 多空格/多行/带 `export`/带引号/带反引号;
  4. **写进 ~/.ct/config 并 chmod 600**,和运行期读取用的是同一个文件;
  5. **当场验证一次真请求**,把 401/404/模型名错/网络不通分成人话;
  6. 非交互(CI/管道)不阻塞:打印可以直接 `cat >` 的整段,退出码 2。

为什么不用"交互问答"(逐项问 URL、问 key、问模型)
--------------------------------------------------
一次粘贴比四轮问答快,而且能整段从服务商文档复制。逐项问答还容易在"模型名"
这一项上把人卡住(不同档位的模型名不一样)。
"""
import os
import re
import select
import sys

# 必须齐的最小集合:没有 BASE_URL 不知道发给谁,没有 key 发不出去,
# 没有模型名服务端会拒。PRO_MODEL 缺省跟随 FLASH_MODEL(少让用户想一件事)。
REQUIRED = ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL")
FILE_KEYS = ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL", "PRO_MODEL")
KEY_ALIASES = {"API_KEY": "WCHAT_API_KEY"}

# 面板里给的例子:只求"形似",不替用户猜服务商。
EXAMPLE = ("BASE_URL=https://api.example.com/v1/chat/completions  "
           "WCHAT_API_KEY=sk-xxxx  FLASH_MODEL=example-chat  PRO_MODEL=example-pro")


def config_path():
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")
    return os.path.join(ct, "config")


def _env(name):
    if name == "WCHAT_API_KEY":
        return os.environ.get("WCHAT_API_KEY") or os.environ.get("API_KEY") or ""
    return os.environ.get(name) or ""


def missing():
    """返回还缺的键名列表(按 REQUIRED 顺序)。PRO_MODEL 有 FLASH_MODEL 兜底,不算缺。"""
    out = []
    for k in REQUIRED:
        if not (_env(k) or "").strip():
            out.append(k)
    return out


def _unquote(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    return v.strip().strip("`").strip()


def normalize_base_url(u):
    """把用户给的地址补成**完整端点**。

    为什么必须补:本项目内部约定 BASE_URL 是完整端点(含 /chat/completions),
    而 SDK 的 base_url 是根 —— 写根会被再拼一层,表现为 404。用户从文档里复制到
    的往往是根地址或 `/v1` 结尾,所以这里补齐而不是报错。
    """
    u = (u or "").strip().strip("'\"`")
    if not u:
        return ""
    if not re.match(r"^https?://", u):
        return u
    if "/chat/completions" in u:
        return u
    return u.rstrip("/") + "/chat/completions"


def parse_pasted(text):
    """从一段粘贴文本里抠出配置项。宽容但不猜语义:

    支持 `KEY=value` / `export KEY=value` / `"KEY": "value"`(JSON 片段)/
    只有值(裸 key 或裸 URL)。其余文本忽略。
    """
    out = {}
    if not text:
        return out
    # 1) KEY=value / export KEY=value(允许值里带空格吗?不带:按空白切,值取到下一个 KEY 前)
    pat = re.compile(r"(?:^|[\s,;{`])(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*"
                     r"(\"[^\"]*\"|'[^']*'|`[^`]*`|[^\s,;}\"'`]+)")
    for m in pat.finditer(text):
        k, v = m.group(1), _unquote(m.group(2))
        k = KEY_ALIASES.get(k, k)
        if k in FILE_KEYS:
            out[k] = v
    rest = pat.sub(" ", text)
    # 2) 裸值:URL 与 key
    for tok in re.split(r"[\s,;]+", rest):
        tok = _unquote(tok)
        if not tok or tok.startswith("#"):
            continue
        if tok.startswith(("http://", "https://")) and "BASE_URL" not in out:
            out["BASE_URL"] = tok
        elif re.match(r"^sk-[A-Za-z0-9._\-]{6,}$", tok) and "WCHAT_API_KEY" not in out:
            out["WCHAT_API_KEY"] = tok
    if out.get("BASE_URL"):
        out["BASE_URL"] = normalize_base_url(out["BASE_URL"])
    if out.get("FLASH_MODEL") and not out.get("PRO_MODEL"):
        out["PRO_MODEL"] = out["FLASH_MODEL"]
    return out


def read_config():
    """读 ~/.ct/config(不落环境变量),返回 {KEY: value}。"""
    p = config_path()
    out = {}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[len("export "):].strip()
                k = KEY_ALIASES.get(k, k)
                if " #" in v:
                    v = v.split(" #", 1)[0]
                out[k] = _unquote(v)
    except OSError:
        pass
    return out


def write_config(pairs):
    """把 pairs 合并进 ~/.ct/config(保留文件里其它键与注释)。返回路径。

    权限一律 600:文件里是明文 key,而且运行期 `_load_ct_config` 对非 600 会**拒绝加载** ——
    写宽了等于下次启动又报"没配"。
    """
    p = config_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    old = ""
    try:
        with open(p, "r", encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = ""
    lines = old.splitlines()
    seen = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        k = KEY_ALIASES.get(k, k)
        if k in pairs:
            lines[i] = '%s="%s"' % (k, pairs[k])
            seen.add(k)
    for k, v in pairs.items():
        if k not in seen:
            lines.append('%s="%s"' % (k, v))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    return p


def apply(pairs):
    """写文件 + 同步进当前进程环境(否则本次会话仍按"没配"走)。"""
    p = write_config(pairs)
    for k, v in pairs.items():
        os.environ[k] = v
        if k == "WCHAT_API_KEY":
            os.environ.setdefault("API_KEY", v)
    return p


def _hint_for(exc):
    """把异常翻成人话 + 一句怎么改。"""
    s = "%s" % (exc)
    low = s.lower()
    if "401" in low or "unauthorized" in low or "invalid_api_key" in low or "authentication" in low:
        return ("key 无效(服务端返回 401)", "把 WCHAT_API_KEY 换成你自己的 key")
    if "403" in low or "permission" in low or "forbidden" in low:
        return ("这个 key 没有该模型的权限(403)",
                "换一个模型名(FLASH_MODEL),或去后台开通模型权限")
    if "404" in low or "not found" in low:
        return ("地址不对(404)", "BASE_URL 要写到 /chat/completions 结尾")
    if "model" in low and ("exist" in low or "invalid" in low or "not" in low):
        return ("模型名不对", "按服务商文档填 FLASH_MODEL(如 deepseek-chat)")
    if "connect" in low or "timeout" in low or "timed out" in low or "network" in low:
        return ("连不上服务器", "检查网络/代理,或确认 BASE_URL 域名拼写")
    return ("请求失败", s[:200])


def verify():
    """发一次**最小真实请求**验证配置。返回 (ok, 人话说明)。

    刻意走 `stream=True` 而不是非流式:这就是真实对话用的那条链路(SSE),
    验证它才算验证"待会儿能不能用"。非流式在部分网关上是另一套实现,
    验过了也可能一进对话就挂。
    """
    base = os.environ.get("BASE_URL", "")
    key = _env("WCHAT_API_KEY")
    model = os.environ.get("FLASH_MODEL") or os.environ.get("PRO_MODEL") or ""
    if not base or not key:
        return False, "还缺 BASE_URL 或 WCHAT_API_KEY"
    try:
        from wchat_py.llm import _make_client
        from openai import OpenAI  # noqa: F401  确认依赖在
    except ImportError:
        return False, "缺少 openai 依赖,请重装 wchat"
    try:
        cli = _make_client(base, key, os.environ.get("API_KEY_HEADER", ""))
        stream = cli.chat.completions.create(
            model=(model or "default"),
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1, stream=True)
        _got = 0
        for _chunk in stream:
            _got += 1
            if _got >= 1:
                break
        try:
            stream.close()
        except Exception:
            pass
        return True, "连通了(%s)" % (model or "default")
    except Exception as e:          # 网络/鉴权/模型名,全都在这条路上
        what, how = _hint_for(e)
        return False, "%s → %s" % (what, how)


def _dwidth(s):
    """终端显示宽度:东亚宽字符算 2 列(否则边框对不齐)。"""
    import unicodedata
    n = 0
    for ch in s:
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def _dpad(s, width):
    gap = width - _dwidth(s)
    return s + " " * max(0, gap)


def _panel(miss, path):
    W = 62
    L = []
    a = L.append
    a("")
    a("\033[1;36m╭%s╮\033[0m" % ("─" * W))
    a("\033[1;36m│\033[0m \033[1m%s\033[0m\033[1;36m│\033[0m"
      % _dpad("还差一步:填一个 API key 就能开始用了", W - 1))
    a("\033[1;36m╰%s╯\033[0m" % ("─" * W))
    a("")
    a("  当前缺:\033[1m%s\033[0m" % "、".join(miss))
    a("  wchat 支持任何 OpenAI 兼容接口,填一次就存起来。")
    a("")
    a("  \033[2m复制下面这行,把 sk-xxxx 和模型名换成你自己的,粘回来回车:\033[0m")
    a("")
    a("  \033[1;33mBASE_URL=\033[0m\033[4mhttps://api.example.com/v1/chat/completions\033[0m")
    a("  \033[1;33mWCHAT_API_KEY=\033[0m\033[4msk-xxxx\033[0m")
    a("  \033[1;33mFLASH_MODEL=\033[0m\033[4mexample-chat\033[0m"
      "   \033[2m(粘贴时写成一行、空格隔开也行)\033[0m")
    a("")
    a("  \033[2m· API key    服务商控制台建一个(形如 sk-...)\033[0m")
    a("  \033[2m· 模型名     服务商文档里的对话模型名,如 deepseek-chat / qwen-plus\033[0m")
    a("  \033[2m· BASE_URL   写到 /chat/completions 结尾;只写 /v1 也行,会自动补齐\033[0m")
    a("  \033[2m· 保存到     %s(权限 600),下次启动直接用\033[0m" % path)
    a("")
    return "\n".join(L)


def _noninteractive(path):
    """非交互:打印可以直接执行的整段,退出码 2。"""
    return "\n".join([
        "",
        "还差一步:填一个 API key 就能开始用了。",
        "当前不是交互终端,请复制下面整段到终端执行(把 sk-xxxx 与模型名换成你自己的):",
        "",
        "  mkdir -p ~/.ct && cat > ~/.ct/config <<'EOF'",
        "  BASE_URL=https://api.example.com/v1/chat/completions",
        "  WCHAT_API_KEY=sk-xxxx",
        "  FLASH_MODEL=example-chat",
        "  PRO_MODEL=example-chat",
        "  EOF",
        "  chmod 600 ~/.ct/config",
        "",
        "然后重跑同一条命令即可。也可以用环境变量临时给:",
        "  BASE_URL=... WCHAT_API_KEY=... FLASH_MODEL=... wchat",
        "",
    ])


def _pending(stream):
    """stdin 里是否还有已经到达的字节(粘贴多行时用)。拿不到 fileno 就当没有。"""
    try:
        r, _, _ = select.select([stream], [], [], 0.15)
        return bool(r)
    except Exception:
        return False


def _effective():
    """当前进程能看到的配置(环境变量 + 文件)。环境变量优先级更高(与运行期一致)。"""
    d = {}
    for k in FILE_KEYS:
        v = (_env(k) or "").strip()
        if v:
            d[k] = v
    for k, v in read_config().items():
        if v and k not in d:
            d[k] = v
    if not d.get("PRO_MODEL") and d.get("FLASH_MODEL"):
        d["PRO_MODEL"] = d["FLASH_MODEL"]
    if not d.get("FLASH_MODEL") and d.get("PRO_MODEL"):
        d["FLASH_MODEL"] = d["PRO_MODEL"]
    return d


def run(interactive=None):
    """跑引导。返回 0=配好了 / 2=没配成(非交互) / 3=配了但验证失败。"""
    path = config_path()
    miss = missing()
    if not miss:
        return 0
    if interactive is None:
        try:
            interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        except Exception:
            interactive = False
    if not interactive:
        sys.stdout.write(_noninteractive(path))
        sys.stdout.flush()
        return 2
    sys.stdout.write(_panel(miss, path))
    sys.stdout.flush()

    got = {}
    for attempt in range(3):
        sys.stdout.write("\033[1;32m粘贴后回车 ›\033[0m ")
        sys.stdout.flush()
        try:
            line = sys.stdin.readline()
        except (KeyboardInterrupt, EOFError):
            line = ""
        if not line.strip():
            sys.stdout.write("\n  \033[2m已跳过。想稍后配置:重跑 wchat,或 wchat --setup\033[0m\n\n")
            return 3
        text = line
        while not _satisfied(dict(_effective(), **got, **parse_pasted(text))) and _pending(sys.stdin):
            more = sys.stdin.readline()
            if not more:
                break
            text += "\n" + more
        got.update(parse_pasted(text))
        merged = dict(_effective(), **got)
        if not merged.get("FLASH_MODEL") and merged.get("PRO_MODEL"):
            merged["FLASH_MODEL"] = merged["PRO_MODEL"]
        if not merged.get("PRO_MODEL") and merged.get("FLASH_MODEL"):
            merged["PRO_MODEL"] = merged["FLASH_MODEL"]
        still = [k for k in REQUIRED if not (merged.get(k) or "").strip()]
        if still:
            sys.stdout.write("  \033[33m还没认出来:%s。再贴一次(含 key=值),或按回车退出\033[0m\n"
                             % "、".join(still))
            continue
        # 只把"这次粘贴带来的"写进文件:有些人刻意用环境变量临时给 BASE_URL,
        # 把它固化到配置文件会改掉他下次启动的行为。PRO_MODEL 兜底跟着写。
        to_write = {k: v for k, v in got.items() if v}
        if merged.get("PRO_MODEL") and "PRO_MODEL" not in to_write:
            to_write["PRO_MODEL"] = merged["PRO_MODEL"]
        p = apply(to_write)
        sys.stdout.write("  \033[2m已写入 %s(600)\033[0m\n" % p)
        if os.environ.get("WCHAT_SETUP_NO_VERIFY") == "1":
            sys.stdout.write("  \033[2m跳过验证(WCHAT_SETUP_NO_VERIFY=1)\033[0m\n\n")
            return 0
        sys.stdout.write("  \033[2m验证中…\033[0m\r")
        sys.stdout.flush()
        ok, detail = verify()
        sys.stdout.write("\033[2K")
        if ok:
            sys.stdout.write("  \033[1;32m✅ 配置成功\033[0m \033[2m%s\033[0m\n\n" % detail)
            return 0
        sys.stdout.write("  \033[1;31m✗ 还是不通:%s\033[0m\n" % detail)
        sys.stdout.write("  \033[2m配置已保存,改好后重跑 wchat 即可。想重来: wchat --setup\033[0m\n\n")
        return 3
    sys.stdout.write("  \033[2m试了几次都没认出完整配置。稍后再试: wchat --setup\033[0m\n\n")
    return 3


def _satisfied(d):
    for k in REQUIRED:
        if not (d.get(k) or "").strip():
            return False
    return True


def ensure_ready(argv):
    """入口调用:需要引导就引导。返回 None=继续跑,否则返回退出码。"""
    if not argv:
        pass
    if os.environ.get("WCHAT_NO_SETUP") == "1":
        return None
    if any(a in ("-h", "--help", "-V", "--version") for a in argv):
        return None
    if argv and argv[0] in ("repl", "run-st", "parse", "usage", "hardwall", "help"):
        return None
    force = bool(argv and argv[0] == "setup") or os.environ.get("WCHAT_SETUP") == "1"
    if force and argv and argv[0] == "setup":
        argv = argv[1:]
    if not force and not missing():
        return None
    rc = run()
    if rc != 0:
        return rc
    return None
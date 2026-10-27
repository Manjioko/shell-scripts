#!/usr/bin/env python3
"""配置检测与填写:把"填 API key"这件事做到不用排版。

两个职责,分开用:

  · `diagnose()`  —— 只**看**不写。给 `wchat` 用:缺什么/坏在哪/下一步该敲什么。
  · `configure()` —— 逐项问或一次给完 → 校验 → 写盘(600) → 验连通。给 `wchat -config` 用。

为什么不做"一次粘一整行"(上一版的做法)
----------------------------------------
让人把 `BASE_URL=... WCHAT_API_KEY=... FLASH_MODEL=...` 写成一行,等于让他做排版:
少个空格、多个换行、多敲个引号就"没认出来"。实测这是最容易掉用户的一步。
所以现在默认是**一项一问**,值里带空格/斜杠/等号都无所谓 —— 用户只需要"粘一个值"。

退出码(调用方据此决定是否继续)
------------------------------
  0 = 配置齐了,可以用
  1 = 内部错误(依赖缺失等)
  2 = 需要用户动作(缺项/格式错/权限/连不通),并打印 "退出码 2:原因"
"""
import getpass
import os
import re
import sys

# 必须齐的最小集合:没有 BASE_URL 不知道发给谁,没有 key 发不出去,
# 没有模型名服务端会拒。PRO_MODEL 缺省跟随 FLASH_MODEL(少让用户想一件事)。
REQUIRED = ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL")
FILE_KEYS = ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL", "PRO_MODEL")
KEY_ALIASES = {"API_KEY": "WCHAT_API_KEY"}

RC_OK = 0
RC_ERR = 1
RC_NEED_USER = 2

# 退出码 2 的具体原因(打印在末尾,方便用户/脚本对账,也方便贴给人看)
R_MISSING = "缺少必要配置"
R_PERM = "配置无法加载(文件权限)"
R_BADFMT = "配置格式不正确"
R_NOCONN = "无法连通配置"
R_ABORT = "已取消,未修改"

# 每一项的说明:长什么样、从哪拿。不要把变量名当说明书。
FIELD_HELP = {
    "BASE_URL": "服务商给的接口地址。要写到 /v1/chat/completions 结尾;"
                "只写 https://<域名>/v1 也行,会自动补齐",
    "WCHAT_API_KEY": "服务商控制台建的一串(通常 sk- 开头)。输入不回显,粘上回车即可"
                     ",回车后会告诉你收到多少字符",
    "FLASH_MODEL": "服务商文档里的对话模型名,如 deepseek-chat / qwen-plus",
}


def config_path():
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")
    return os.path.join(ct, "config")


def _env(name):
    if name == "WCHAT_API_KEY":
        return os.environ.get("WCHAT_API_KEY") or os.environ.get("API_KEY") or ""
    return os.environ.get(name) or ""


def _unquote(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    return v.strip().strip("`").strip()


def mask(key):
    """给屏幕看的脱敏形式:sk-ab…wxyz。不打印完整 key(投屏/录屏/日志)。"""
    k = key or ""
    if not k:
        return "(未设置)"
    if len(k) <= 8:
        return k[:2] + "…"
    return k[:5] + "…" + k[-3:]


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
        return u.rstrip("/")
    return u.rstrip("/") + "/chat/completions"


def file_mode_ok(path=None):
    """配置文件权限是否安全(600/400)。文件不存在返回 True(还没写过)。"""
    p = path or config_path()
    try:
        import stat as _st
        m = os.stat(p).st_mode & 0o777
    except OSError:
        return True
    return m in (0o600, 0o400)


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


def parse_pasted(text):
    """从一段文本里抠出配置项(命令行一次给完时用)。

    支持 `KEY=value` / `export KEY=value` / `"KEY": "value"`(JSON 片段)/
    只有值(裸 key 或裸 URL)。交互填空不走这里 —— 那边一项就是一个值,不需要解析。
    """
    out = {}
    if not text:
        return out
    pat = re.compile(r"(?:^|[\s,;{`])(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*"
                     r"(\"[^\"]*\"|'[^']*'|`[^`]*`|[^\s,;}\"'`]+)")
    for m in pat.finditer(text):
        k, v = m.group(1), _unquote(m.group(2))
        k = KEY_ALIASES.get(k, k)
        if k in FILE_KEYS:
            out[k] = v
    rest = pat.sub(" ", text)
    for tok in re.split(r"[\s,;]+", rest):
        tok = _unquote(tok)
        if not tok or tok.startswith("#"):
            continue
        if tok.startswith(("http://", "https://")) and "BASE_URL" not in out:
            out["BASE_URL"] = tok
        elif re.match(r"^[A-Za-z0-9._\-]{12,}$", tok) and "WCHAT_API_KEY" not in out:
            out["WCHAT_API_KEY"] = tok
    if out.get("BASE_URL"):
        out["BASE_URL"] = normalize_base_url(out["BASE_URL"])
    if out.get("FLASH_MODEL") and not out.get("PRO_MODEL"):
        out["PRO_MODEL"] = out["FLASH_MODEL"]
    return out


def validate(pairs):
    """逐项校验。返回 [(字段名, 问题描述)]，空列表 = 通过。

    每条都要"指到点上"：用户照着就能改，不需要猜。
    """
    issues = []
    bu = (pairs.get("BASE_URL") or "").strip()
    key = (pairs.get("WCHAT_API_KEY") or "").strip()
    model = (pairs.get("FLASH_MODEL") or "").strip()

    if not bu:
        issues.append(("BASE_URL", "还缺 BASE_URL —— 服务商给的接口地址"))
    elif not re.match(r"^https?://", bu):
        issues.append(("BASE_URL", "BASE_URL 要以 http:// 或 https:// 开头,你给的是 [%s]" % bu))
    elif not re.match(r"^https?://[^/\s]+", bu):
        issues.append(("BASE_URL", "BASE_URL 里没有域名:[%s]" % bu))

    if not key:
        issues.append(("WCHAT_API_KEY", "还缺 WCHAT_API_KEY —— 服务商控制台建的那串"))
    elif re.search(r"\s", key):
        issues.append(("WCHAT_API_KEY", "WCHAT_API_KEY 里不能有空格/换行 —— 它是一整串;[%s]" % key))
    elif re.search(r"[A-Za-z_][A-Za-z0-9_]*=", key):
        issues.append(("WCHAT_API_KEY",
                       "WCHAT_API_KEY 里像是混进了另一个设置项(出现了 xxx=),"
                       "多半是把两项写成一行漏了分隔:[%s]" % key))
    elif key.count('"') % 2 or key.count("'") % 2:
        issues.append(("WCHAT_API_KEY", "WCHAT_API_KEY 里的引号没配对"))

    if not model:
        issues.append(("FLASH_MODEL", "还缺 FLASH_MODEL —— 服务商文档里的对话模型名"))
    elif re.search(r"[\s\u4e00-\u9fff]", model):
        issues.append(("FLASH_MODEL",
                       "FLASH_MODEL 不该含空格或中文,一般只有字母数字和 . - /;[%s]" % model))
    return issues


def _effective():
    """当前进程能看到的配置(环境变量 + 文件)。环境变量优先(与运行期一致)。"""
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


def diagnose():
    """只检查不修改。返回 (ok, [(label, 说明)])。

    label 是退出码 2 的那句原因(如"缺少必要配置"),说明写清到底哪儿不对 ——
    用户照着就能改。
    """
    d = _effective()

    # ① 权限:文件里有明文 key,同机他人可读等于把 key 交出去。
    #    只在"确实要靠这个文件"时才拦 —— 环境变量已给全的话,运行期根本不读文件。
    need_file = any(not (d.get(k) or "").strip() for k in REQUIRED)
    if need_file and os.path.exists(config_path()) and not file_mode_ok():
        import stat as _st
        m = os.stat(config_path()).st_mode & 0o777
        # 具体的一行由 _load_ct_config 在拒绝加载时已经打了(那里是真正的动作点),
        # 这里只补"怎么改",不重复复述一遍路径与数字。
        return False, [(R_PERM,
                        "配置文件权限是 %o,里面有明文 key,已拒绝加载  →  修: chmod 600 %s"
                        % (m, config_path()))]
    # ② 缺项 / 格式
    bad = validate(d)
    if bad:
        missing = [k for k, m in bad if m.startswith("还缺")]
        label = R_MISSING if missing else R_BADFMT
        return False, [(label, m) for _k, m in bad]
    return True, []


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

    走 `stream=True`:这就是真实对话用的那条链路(SSE),验它才算验"待会儿能不能用"。
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
        for _chunk in stream:
            break
        try:
            stream.close()
        except Exception:
            pass
        return True, "连通了(%s)" % (model or "default")
    except Exception as e:          # 网络/鉴权/模型名,全都在这条路上
        what, how = _hint_for(e)
        return False, "%s → %s" % (what, how)


# ---------------------------------------------------------------- 展示

def _w(s=""):
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def show_problems(issues, non_interactive=False):
    """把 diagnose 的结果打成"能照着做"的一段。"""
    _w("")
    _w("  \033[1;31m✗ 还不能开始用\033[0m")
    _w("")
    for _label, msg in issues:
        _w("    · %s" % msg)
    _w("")
    if non_interactive:
        _w("  这里没有交互终端。直接给值:")
        _w("")
        _w("    \033[1;33mwchat -config \"BASE_URL=https://<域名>/v1/chat/completions "
           "WCHAT_API_KEY=sk-xxxx FLASH_MODEL=<模型名>\"\033[0m")
        _w("")
        _w("  或者写进 %s(权限 600):" % config_path())
        _w("")
        _w("    BASE_URL=https://<域名>/v1/chat/completions")
        _w("    WCHAT_API_KEY=sk-xxxx")
        _w("    FLASH_MODEL=<模型名>")
        _w("")
    else:
        _w("  \033[1m下一步:\033[0m")
        _w("")
        _w("    \033[1;33mwchat -config\033[0m")
        _w("")
        _w("  它会一项一项问你(每项都说明了长什么样),填完当场验证能不能连通。")
        _w("")


def show_header(existing=None):
    _w("")
    _w("  \033[1m配置 wchat\033[0m  \033[2m→ %s(权限 600)\033[0m" % config_path())
    _w("  \033[2m逐项填写;只想改某一项时,其余项直接回车保留原值。\033[0m")
    _w("")


# ---------------------------------------------------------------- 交互填写

def _read_secret():
    """读 key:不回显。**从 stdin 读**,不经 /dev/tty。

    为什么不用 getpass:getpass 会自己打开 `/dev/tty`。如果进程的 stdin 与它
    不是同一个终端(嵌套 pty、被包装器启动、tmux/IDE 终端等),键就**读到另一个
    设备上去了** —— 表现正是"输入框像被锁住:敲字没反应,但回车能走下去"(实测)。
    这里只操作**当前 stdin 这个 fd** 的 ECHO 位,读完在 finally 里恢复,
    不碰 /dev/tty,也就不存在"两个终端"的分裂。
    """
    try:
        fd = sys.stdin.fileno()
    except Exception:
        return (sys.stdin.readline() or "").strip()
    if os.environ.get("WCHAT_SETUP_PLAIN_INPUT") == "1":
        return (sys.stdin.readline() or "").strip()
    try:
        import termios
        if not os.isatty(fd):
            return (sys.stdin.readline() or "").strip()
        old = termios.tcgetattr(fd)
    except Exception:
        # 拿不到 termios(非 tty/平台不支持):退回普通读,绝不因此卡住
        return (sys.stdin.readline() or "").strip()
    new = list(old)
    new[3] = new[3] & ~termios.ECHO          # lflags:关回显
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            line = ""
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)   # 无论怎么退出都恢复
        except Exception:
            pass
    return (line or "").strip()


def _ask(field, current=""):
    """问一项。返回 (值, 是否用户主动改了)。key 不回显。"""
    _w("  \033[1;36m%s\033[0m" % field)
    _w("    \033[2m%s\033[0m" % FIELD_HELP.get(field, ""))
    if current:
        shown = mask(current) if field == "WCHAT_API_KEY" else current
        _w("    \033[2m现在: %s(回车保留)\033[0m" % shown)
    else:
        _w("    \033[2m现在: (未设置)\033[0m")
    sys.stdout.write("    \033[1;32m›\033[0m ")
    sys.stdout.flush()
    if field == "WCHAT_API_KEY":
        ans = _read_secret()
        # 不回显时用户没有任何反馈,容易以为"输入框卡住了"。给个计数证明它活着,
        # 同时不泄露内容。
        n = len(ans or "")
        _w("\033[2m    已收到 %d 个字符%s\033[0m"
           % (n, "" if n else "(直接回车 = 跳过)"))
    else:
        try:
            ans = (sys.stdin.readline() or "").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
    ans = _unquote(ans)
    if not ans:
        return current, False
    return ans, True


def configure_interactive(existing=None, verify_conn=True):
    """逐项问答 → 校验 → 写盘 → 验连通。返回退出码。"""
    existing = dict(existing or _effective())
    show_header(existing)
    vals = {}
    for f in REQUIRED:
        v, changed = _ask(f, existing.get(f, ""))
        if changed:
            vals[f] = v
        elif not (existing.get(f) or "").strip():
            # 该项本来就缺,又留空了 → 记下来,后面统一报
            vals[f] = ""
        _w("")
    # 合并后整体校验(允许"只改一项")
    merged = dict(existing)
    merged.update({k: v for k, v in vals.items() if v})
    if merged.get("BASE_URL"):
        merged["BASE_URL"] = normalize_base_url(merged["BASE_URL"])
        if vals.get("BASE_URL"):
            vals["BASE_URL"] = merged["BASE_URL"]
    if not merged.get("PRO_MODEL") and merged.get("FLASH_MODEL"):
        merged["PRO_MODEL"] = merged["FLASH_MODEL"]
        vals.setdefault("PRO_MODEL", merged["FLASH_MODEL"])

    issues = validate(merged)
    if issues:
        _w("  \033[1;31m✗ 有几处需要改:\033[0m")
        for k, m in issues:
            _w("    · %s" % m)
        _w("")
        _w("  \033[2m没有写入任何东西。重跑 wchat -config 再填一次。\033[0m")
        _w("")
        return RC_NEED_USER

    if not vals:
        _w("  \033[2m什么都没改,直接退出。\033[0m")
        _w("")
        return RC_NEED_USER

    p = apply(vals)
    _w("  \033[2m已写入 %s(600)\033[0m" % p)
    return _after_write(verify_conn)


def _after_write(verify_conn):
    """写盘之后:验连通 → 打印结论。返回退出码。"""
    if not verify_conn:
        _w("  \033[2m跳过连通性验证(--no-verify)\033[0m")
        _w("")
        _w("  \033[1;32m✅ 配置已保存\033[0m  现在直接运行 \033[1mwchat\033[0m 开始用。")
        _w("")
        return RC_OK
    sys.stdout.write("  \033[2m验证中…\033[0m\r")
    sys.stdout.flush()
    ok, detail = verify()
    sys.stdout.write("\033[2K")
    if ok:
        _w("  \033[1;32m✅ 配置成功\033[0m \033[2m%s\033[0m" % detail)
        _w("")
        _w("  现在直接运行 \033[1mwchat\033[0m 开始用。")
        _w("")
        return RC_OK
    _w("  \033[1;31m✗ 配置已保存,但连不通\033[0m")
    _w("    %s" % detail)
    _w("")
    _w("  \033[2m改好后重跑 wchat -config(已经把填的值存下来了,不用重敲)。\033[0m")
    _w("")
    return RC_NEED_USER


# ---------------------------------------------------------------- 入口

USAGE = """用法:
  wchat -config                     逐项填写(推荐)
  wchat -config "BASE_URL=... WCHAT_API_KEY=... FLASH_MODEL=..."    一次给完
  wchat -config --check "..."       只校验,不写盘
  wchat -config --no-verify "..."   先不验连通(离线/内网)

配置写在 ~/.ct/config(权限 600)。已有的其它配置项不会被改动。"""


def configure(argv):
    """`wchat -config` 的实现。argv 是去掉 `-config` 之后的部分。返回退出码。"""
    if any(a in ("-h", "--help") for a in argv):
        _w(USAGE.rstrip())
        return RC_OK
    check_only = False
    verify_conn = os.environ.get("WCHAT_SETUP_NO_VERIFY") != "1"
    rest = []
    for a in argv:
        if a in ("--check", "-c"):
            check_only = True
        elif a in ("--no-verify", "-n"):
            verify_conn = False
        elif a.startswith("-") and "=" not in a:
            _w("  未知选项:%s" % a)
            _w(USAGE.rstrip())
            return RC_ERR
        else:
            rest.append(a)

    if rest:
        # 一次给完:解析 → 校验 → (验) → 写
        pairs = parse_pasted(" ".join(rest))
        merged = dict(_effective())
        merged.update(pairs)
        if merged.get("BASE_URL"):
            merged["BASE_URL"] = normalize_base_url(merged["BASE_URL"])
            if pairs.get("BASE_URL"):
                pairs["BASE_URL"] = merged["BASE_URL"]
        if not merged.get("PRO_MODEL") and merged.get("FLASH_MODEL"):
            merged["PRO_MODEL"] = merged["FLASH_MODEL"]
            pairs.setdefault("PRO_MODEL", merged["FLASH_MODEL"])
        issues = validate(merged)
        if issues:
            _w("")
            _w("  \033[1;31m✗ 格式有问题,没有写入:\033[0m")
            for k, m in issues:
                _w("    · %s" % m)
            _w("")
            _w("  \033[2m提示:也可以不带参数跑 `wchat -config`,一项一项填,不用自己排版。\033[0m")
            return RC_NEED_USER
        if check_only:
            _w("")
            _w("  \033[1;32m✅ 格式检查通过\033[0m")
            for k in FILE_KEYS:
                if merged.get(k):
                    _w("    %-14s %s" % (k, mask(merged[k]) if k == "WCHAT_API_KEY" else merged[k]))
            if verify_conn:
                ok, detail = _verify_pairs(merged)
                _w("    \033[2m连通性: %s\033[0m" % ("✅ " + detail if ok else "✗ " + detail))
                if not ok:
                    _w("")
                    _w("  \033[2m(--check:只看不写)\033[0m")
                    _w("")
                    return RC_NEED_USER
            _w("")
            _w("  \033[2m(--check:只看不写)\033[0m")
            _w("")
            return RC_OK
        p = apply(pairs)
        _w("")
        _w("  \033[2m已写入 %s(600)\033[0m" % p)
        return _after_write(verify_conn)

    # 无参数:交互逐项
    if not _is_tty():
        _w("")
        _w("  这里没有交互终端,没法逐项问。请一次给完:")
        _w("")
        _w("    \033[1;33mwchat -config \"BASE_URL=https://<域名>/v1/chat/completions "
           "WCHAT_API_KEY=sk-xxxx FLASH_MODEL=<模型名>\"\033[0m")
        _w("")
        return RC_NEED_USER
    return configure_interactive(verify_conn=verify_conn)


def _verify_pairs(merged):
    """--check 时验证"给定值"(而不是进程里现有的),验完把环境恢复原样。"""
    saved = {k: os.environ.get(k) for k in merged}
    try:
        for k, v in merged.items():
            if v:
                os.environ[k] = v
        if merged.get("WCHAT_API_KEY") and not os.environ.get("API_KEY"):
            os.environ["API_KEY"] = merged["WCHAT_API_KEY"]
        return verify()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _is_tty():
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def ensure_ready(argv):
    """入口:需要用户动手就返回退出码,否则 None(继续正常跑)。

    只**检查**并提示,不再内联跑引导 —— 用户跑 `wchat` 知道缺什么,
    跑 `wchat -config` 把事做完,再跑 `wchat` 进会话。中间不会停在半配置状态。
    """
    if os.environ.get("WCHAT_NO_SETUP") == "1":
        return None
    if any(a in ("-h", "--help", "-V", "--version") for a in argv):
        return None
    if argv and argv[0] in ("repl", "run-st", "parse", "usage", "hardwall", "help"):
        return None
    ok, issues = diagnose()
    if ok:
        return None
    show_problems(issues, non_interactive=not _is_tty())
    label = issues[0][0] if issues else R_MISSING
    _w("  \033[2m退出码 2：%s\033[0m" % label)
    _w("")
    return RC_NEED_USER
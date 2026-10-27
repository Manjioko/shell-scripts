#!/usr/bin/env python3
"""配置检测与填写:把"填 API key"这件事做到不用排版。

两个职责,分开用:

  · `diagnose()`  —— 只**看**不写。给 `wchat` 用:缺什么/坏在哪/下一步该敲什么。
  · `configure()` —— 逐项问或一次给完 → 校验 → 写盘(600) → 验连通。给 `wchat -config` 用。

退出码(调用方据此决定是否继续)
------------------------------
  0 = 配置齐了,可以用
  1 = 内部错误(依赖缺失等)
  2 = 需要用户动作(缺项/格式错/权限/连不通),并打印 "退出码 2:原因"

写盘格式 = ctmodel 的格式
-------------------------
`~/.ct/config` 里同一家厂商的几行用行尾 `# @model <组名>` 标记成一组;
活动组不加注释,其余组整行注释。这样 `ctmodel qwen` / `ctmodel deepseek`
可以原样切换 —— 本模块只负责**写对格式**,不重复实现切换逻辑。

  # BASE_URL="https://qwen.example/v1/chat/completions" # @model qwen
  BASE_URL="https://api.deepseek.com/chat/completions" # @model deepseek
  ...
  WCHAT_API_KEY="sk-xxx" # @model deepseek
  ...
  FLASH_MODEL="deepseek-flash" # @model deepseek
  PRO_MODEL="deepseek-flash" # @model deepseek
  # FLASH_MODEL="qwen3.8-flash" # @model qwen
"""
import os
import re
import sys

# 必须齐的最小集合:没有 BASE_URL 不知道发给谁,没有 key 发不出去,
# 没有模型名服务端会拒。PRO_MODEL 缺省跟随 FLASH_MODEL(少让用户想一件事)。
REQUIRED = ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL")
# 一组里按这个顺序写(与 ctmodel 示例一致:地址 → 密钥 → 两个模型名)
GROUP_KEYS = ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL", "PRO_MODEL")
FILE_KEYS = GROUP_KEYS
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

MARK = re.compile(r"#\s*@model\s+(\S+)\s*$")
ASSIGN = re.compile(r"^(\s*)(#\s*)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
GROUP_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

HEADER = """# 模型切换用 ~/.ct/ctmodel.py(终端敲 `ctmodel qwen` / `ctmodel deepseek`)
# 行尾的 "@model <名字>" 标记是给脚本看的:哪行生效、哪行注释由脚本维护,不要手改;
# 想加第四家(如 mimo)= 照抄一组赋值行并打上 "@model mimo" 标记,脚本无需改动。
# (本段说明刻意不写成 "# @model" 那种形式 —— 那会被脚本当成一条真标记。)
"""

# 每一项的说明:长什么样、从哪拿。不要把变量名当说明书。
FIELD_HELP = {
    "BASE_URL": "服务商给的接口地址。要写到 /v1/chat/completions 结尾;"
                "只写 https://<域名>/v1 也行,会自动补齐",
    "WCHAT_API_KEY": "服务商控制台建的一串(通常 sk- 开头)。会显示出来,方便你核对自己粘对了没有",
    "FLASH_MODEL": "服务商文档里的对话模型名,如 deepseek-chat / qwen-plus",
    "PRO_MODEL": "加强档的模型名。直接回车 = 与上面那个相同",
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
    """给屏幕/日志看的脱敏形式:sk-ab…wxyz。汇总行一律用它。"""
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
        m = os.stat(p).st_mode & 0o777
    except OSError:
        return True
    return m in (0o600, 0o400)


# ---------------------------------------------------------------- 读

def _read_lines():
    try:
        with open(config_path(), "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def _parse_line(line):
    """拆一行 → (缩进, 是否注释, 键, 值, 组名)。不是赋值行返回 None。"""
    m = ASSIGN.match(line or "")
    if not m:
        return None
    indent, hashmark, key, rest = m.group(1), m.group(2) or "", m.group(3), m.group(4)
    mk = MARK.search(rest)
    group = mk.group(1) if mk else ""
    if mk:
        rest = rest[:mk.start()]
    if " #" in rest:
        rest = rest.split(" #", 1)[0]
    return indent, bool(hashmark), KEY_ALIASES.get(key, key), _unquote(rest), group


def read_config():
    """读**活动**配置(不加注释的行),返回 {KEY: value}。"""
    out = {}
    for line in _read_lines():
        p = _parse_line(line)
        if not p:
            continue
        _i, commented, key, val, _g = p
        if commented:
            continue
        out[key] = val
    return out


def read_groups():
    """读文件中**所有**组(含被注释的),返回 {组名: {KEY: value}}。"""
    groups = {}
    for line in _read_lines():
        p = _parse_line(line)
        if not p:
            continue
        _i, _c, key, val, group = p
        if not group:
            continue
        groups.setdefault(group, {})[key] = val
    return groups


def active_group():
    """当前生效的组名(第一条未注释的 @model 行),没有返回空串。"""
    for line in _read_lines():
        p = _parse_line(line)
        if not p:
            continue
        _i, commented, _k, _v, group = p
        if group and not commented:
            return group
    return ""


def _effective():
    """当前进程能看到的有效配置(环境变量 + 活动配置)。环境变量优先(与运行期一致)。"""
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


# ---------------------------------------------------------------- 写

def _render(key, val, group, commented=False):
    return '%s%s="%s" # @model %s' % ("# " if commented else "", key, val, group)


def _adopt_legacy(lines, name):
    """把**无 `@model` 标记的赋值行**归到 `name` 组,而不是另起一组。

    为什么必须有这一步:老版本的 config(以及手写的 `KEY="v"` 行)没有行尾标记。
    旧实现在"覆盖本组已有键"里只认 `group == name`,于是这些行永远匹配不上,
    缺键补齐那一步就把整组配置**追加**到文件末尾 —— 同一个 PRO_MODEL 出现两次,
    而且每多跑一次就多一份(用户实测就是这个)。值看起来还对(README 的
    `read_config` 是后写覆盖先写),所以它不会报错,只会慢慢把文件搞脏。

    口径:接管**所有模型组键**(GROUP_KEYS)的无标记行 —— 不是只接管本次要写的那些。
    只接管 pairs 会在"这次没改 PRO_MODEL、文件里那条 PRO_MODEL 还是无标记"时留下
    一条无标记生效行,它与组内那条并存 = `ctmodel check` 会报"有多条生效行"。
    非模型组键(如 CTX_WINDOW)一律不动 —— 那是另一类配置,不该被 -config 吞掉。
    """
    n = 0
    for i, line in enumerate(lines):
        p = _parse_line(line)
        if not p:
            continue
        _ind, commented, key, val, group = p
        if group or key not in GROUP_KEYS:
            continue
        lines[i] = _render(key, val, name, commented=commented)
        n += 1
    return n


def _dedupe(lines):
    """同一 (键, 组) 出现多行时只保留**最后一行**,其余注释掉。

    "保留最后一行"不是随手选的:运行期 `read_config()` 是 `out[key] = val` 逐行覆盖,
    也就是**后写的赢**。所以按"留最后一行"清理,配置的有效值与本清理之前**逐字节等价**,
    清理只是让文件不再自相矛盾。

    被清理的行保留原文并加 `# [重复已忽略]` 前缀,但**不带 `@model` 标记** ——
    否则 `ctmodel <组>` 切换时会把它重新取消注释,重复又长回来。
    """
    last = {}
    for i, line in enumerate(lines):
        p = _parse_line(line)
        if not p:
            continue
        _ind, commented, key, _val, group = p
        if not group or commented:
            continue          # 只看"生效中的带标记行"
        last[(group, key)] = i
    dropped = []
    for i, line in enumerate(lines):
        p = _parse_line(line)
        if not p:
            continue
        _ind, commented, key, _val, group = p
        if not group or commented:
            continue
        if last.get((group, key)) != i:
            # 必须**抹掉标记**:留着 `# @model X` 的话,`ctmodel X` 切换时
            # `_set_active` 会把它当"本组的一行"重新解注释,重复立刻长回来。
            body = MARK.sub("", line).rstrip()
            lines[i] = "# [重复已忽略] " + body.lstrip()
            dropped.append(key)
    return dropped


def write_group(name, pairs, activate=True):
    """把一组配置写进 ~/.ct/config(ctmodel 格式)。返回路径。

    · 已存在的组:只改这几个键的值,保留其它行与其它组;
    · 新组:在文件末尾追加;
    · 无标记的老行:就地归入本组(不另起重复的一份);
    · 同一键出现多行:只留最后一行(与运行期"后写覆盖"同义),其余注释留痕;
    · `activate=True` 时把这组设为生效组(去注释本组、注释其它组);
    · 权限强制 600 —— 里面是明文 key,而且运行期对非 600 会拒绝加载。
    """
    name = (name or "").strip()
    if not GROUP_NAME_OK.match(name):
        raise ValueError("组名只能用字母、数字、点、下划线、短横: %r" % name)
    lines = _read_lines()
    if not lines or not any(MARK.search(x) for x in lines):
        # 新文件 / 老格式(无 @model):先写说明头,便于用户理解这文件怎么被维护
        if not lines:
            lines = HEADER.rstrip("\n").split("\n")

    # ①a 老行归位(否则下面会追加出一份重复的)
    _adopt_legacy(lines, name)
    # ①b 清重复(留最后一行 = 与 read_config 的后写覆盖同义)
    _dedupe(lines)

    # ① 覆盖本组已有键
    seen = set()
    for i, line in enumerate(lines):
        p = _parse_line(line)
        if not p:
            continue
        _ind, _c, key, _v, group = p
        if group != name or key not in pairs:
            continue
        lines[i] = _render(key, pairs[key], name, commented=not activate)
        seen.add(key)

    # ② 补齐本组缺的键:插在该组最后一行之后,保持成组
    missing = [k for k in GROUP_KEYS if k in pairs and k not in seen]
    if missing:
        last = -1
        for i, line in enumerate(lines):
            p = _parse_line(line)
            if p and p[4] == name:
                last = i
        new_lines = [_render(k, pairs[k], name, commented=not activate) for k in missing]
        if last >= 0:
            lines[last + 1:last + 1] = new_lines
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(new_lines)

    # ②b 不属于本组的其它键(如 CTX_WINDOW):原样写成普通行,**不能丢**
    #     (老调用方 write_config 会塞进非模型组的配置项;丢掉等于静默改用户配置)
    extras = {k: v for k, v in pairs.items() if k not in GROUP_KEYS}
    if extras:
        seen_x = set()
        for i, line in enumerate(lines):
            p = _parse_line(line)
            if not p:
                continue
            _i, commented, key, _v, group = p
            if key in extras and not group:
                lines[i] = '%s="%s"' % (key, extras[key])
                seen_x.add(key)
        for k, v in extras.items():
            if k not in seen_x:
                lines.append('%s="%s"' % (k, v))

    # ③ 切换生效组:本组去注释,其它组加注释(与 ctmodel 的 _set_active 同语义)
    if activate:
        for i, line in enumerate(lines):
            p = _parse_line(line)
            if not p:
                continue
            ind, commented, key, val, group = p
            if not group:
                continue
            if group == name:
                if commented:
                    lines[i] = _render(key, val, group, commented=False)
            elif not commented:
                lines[i] = _render(key, val, group, commented=True)

    p = config_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    # 覆盖前留一份 .bak:这个文件是明文凭据,且我们刚做了"归位/去重"这类改写。
    # 只在文件确实存在、且内容会变时备份(避免把 .bak 写成空文件/无谓刷时间戳)。
    body = "\n".join(lines).rstrip() + "\n"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
        if old != body:
            with open(p + ".bak", "w", encoding="utf-8") as f:
                f.write(old)
            os.chmod(p + ".bak", 0o600)
    except OSError:
        pass
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    return p


def write_config(pairs):
    """兼容入口:写进**当前生效组**(没有则新建 main 组)。"""
    name = active_group() or "main"
    return write_group(name, pairs, activate=True)


def apply(pairs, group=None):
    """写文件 + 同步进当前进程环境(否则本次会话仍按"没配"走)。"""
    name = group or active_group() or "main"
    p = write_group(name, pairs, activate=True)
    for k, v in pairs.items():
        os.environ[k] = v
        if k == "WCHAT_API_KEY":
            os.environ.setdefault("API_KEY", v)
    return p


# ---------------------------------------------------------------- 解析/校验

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


def validate_group(name):
    """组名校验(ctmodel 要用它做切换命令,不能有花样的字符)。"""
    n = (name or "").strip()
    if not n:
        return ["组名不能为空 —— 就是一个短名字,比如 deepseek / qwen / mimo"]
    if not GROUP_NAME_OK.match(n):
        return ["组名只能用字母、数字、点、下划线、短横(不能有空格/中文):[%s]" % n]
    return []


def guess_group(pairs=None, fallback="main"):
    """猜该写进哪一组:已有生效组就用它(重跑 = 改这组),否则按域名猜一个短名。"""
    cur = active_group()
    if cur:
        return cur
    bu = ((pairs or {}).get("BASE_URL") or _effective().get("BASE_URL") or "").lower()
    for token in ("deepseek", "qwen", "mimo", "openai", "moonshot", "zhipu", "glm",
                  "anthropic", "gemini", "ollama", "localhost", "127.0.0.1"):
        if token in bu:
            return {"127.0.0.1": "local", "localhost": "local"}.get(token, token)
    return fallback


# ---------------------------------------------------------------- 诊断

def diagnose():
    """只检查不修改。返回 (ok, [(原因标签, 说明)])。"""
    d = _effective()

    # ① 权限:文件里有明文 key,同机他人可读等于把 key 交出去。
    #    只在"确实要靠这个文件"时才拦 —— 环境变量已给全的话,运行期根本不读文件。
    need_file = any(not (d.get(k) or "").strip() for k in REQUIRED)
    if need_file and os.path.exists(config_path()) and not file_mode_ok():
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


def show_header(existing=None, group=""):
    _w("")
    _w("  \033[1m配置 wchat\033[0m  \033[2m→ %s(权限 600)\033[0m" % config_path())
    _w("  \033[2m逐项填写;只想改某一项时,其余项直接回车保留原值。\033[0m")
    if group:
        _w("  \033[2m这一组叫 \033[0m\033[1m%s\033[0m\033[2m,以后可用 `ctmodel %s` 切回来。\033[0m"
           % (group, group))
    _w("")


# ---------------------------------------------------------------- 交互填写

def _read_line():
    try:
        return (sys.stdin.readline() or "").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _ask(field, current="", label=None):
    """问一项。返回 (值, 是否用户主动改了)。key 也回显(用户要能确认自己粘对了)。"""
    _w("  \033[1;36m%s\033[0m" % (label or field))
    _w("    \033[2m%s\033[0m" % FIELD_HELP.get(field, ""))
    if current:
        _w("    \033[2m现在: %s(回车保留)\033[0m" % current)
    else:
        _w("    \033[2m现在: (未设置)\033[0m")
    sys.stdout.write("    \033[1;32m›\033[0m ")
    sys.stdout.flush()
    ans = _unquote(_read_line())
    if field == "WCHAT_API_KEY":
        # 双重确认:终端会把输入回显出来(用户能核对);这里再补一行打码摘要,
        # 万一终端不回显/用管道喂值,用户也能确认"确实收到了",不用靠猜。
        n = len(ans or "")
        _w("    \033[2m已收到 %d 个字符%s\033[0m"
           % (n, (" → " + mask(ans)) if n else "(直接回车 = 跳过)"))
    if not ans:
        return current, False
    return ans, True


def _ask_group(suggest):
    while True:
        _w("  \033[1;36m这组配置叫什么名字?\033[0m")
        _w("    \033[2m就是一个短名字,用来说明这组是给哪家用的。"
           "以后可以用 `ctmodel <名字>` 在几组之间切换(如 deepseek / qwen / mimo)\033[0m")
        _w("    \033[2m现在: %s(回车就用它)\033[0m" % (suggest or "main"))
        sys.stdout.write("    \033[1;32m›\033[0m ")
        sys.stdout.flush()
        ans = _unquote(_read_line())
        name = ans or suggest or "main"
        bad = validate_group(name)
        if not bad:
            _w("")
            return name
        _w("    \033[1;31m%s\033[0m" % bad[0])
        _w("")


def configure_interactive(existing=None, verify_conn=True, group=None):
    """逐项问答 → 校验 → 写盘(ctmodel 格式) → 验连通。返回退出码。"""
    existing = dict(existing or _effective())
    g = group or guess_group(existing)
    show_header(existing, g)
    g = _ask_group(g)
    vals = {}
    for f in ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL"):
        v, changed = _ask(f, existing.get(f, ""))
        if changed:
            vals[f] = v
        elif not (existing.get(f) or "").strip():
            vals[f] = ""
        _w("")
    # PRO_MODEL:回车跟随 FLASH_MODEL(少问一个不必要的问题)
    pm, pm_changed = _ask("PRO_MODEL", existing.get("PRO_MODEL", ""), label="PRO_MODEL(可选)")
    if pm_changed:
        vals["PRO_MODEL"] = pm
    _w("")

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

    p = apply(vals, group=g)
    _w("  \033[2m已写入 %s(权限 600),这组叫 \033[0m\033[1m%s\033[0m" % (p, g))
    _w("  \033[2m以后切回这一组:\033[0m \033[1mctmodel %s\033[0m\033[2m"
       "(加 -l 可列出全部组)\033[0m" % g)
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
  wchat -config --name deepseek     指定这组配置的名字(默认沿用当前生效组/按域名猜)

配置写在 ~/.ct/config(权限 600),格式与 ctmodel 一致(行尾 # @model <组名>),
因此可以用 `ctmodel <组名>` 在几组(不同厂商)之间切换。"""


def configure(argv):
    """`wchat -config` 的实现。argv 是去掉 `-config` 之后的部分。返回退出码。"""
    if any(a in ("-h", "--help") for a in argv):
        _w(USAGE.rstrip())
        return RC_OK
    check_only = False
    verify_conn = os.environ.get("WCHAT_SETUP_NO_VERIFY") != "1"
    group = ""
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--check", "-c"):
            check_only = True
        elif a in ("--no-verify", "-n"):
            verify_conn = False
        elif a in ("--name", "-N"):
            if i + 1 >= len(argv):
                _w("  --name 后面要跟组名")
                return RC_ERR
            group = argv[i + 1]
            i += 1
        elif a.startswith("--name="):
            group = a.split("=", 1)[1]
        elif a.startswith("-") and "=" not in a:
            _w("  未知选项:%s" % a)
            _w(USAGE.rstrip())
            return RC_ERR
        else:
            rest.append(a)
        i += 1

    if group:
        bad = validate_group(group)
        if bad:
            _w("")
            _w("  \033[1;31m✗ %s\033[0m" % bad[0])
            _w("")
            return RC_NEED_USER

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
            _w("")
            return RC_NEED_USER
        g = group or guess_group(merged)
        if check_only:
            _w("")
            _w("  \033[1;32m✅ 格式检查通过\033[0m")
            _w("    组名           %s" % g)
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
        p = apply(pairs, group=g)
        _w("")
        _w("  \033[2m已写入 %s(权限 600),这组叫 \033[0m\033[1m%s\033[0m" % (p, g))
        _w("  \033[2m以后切回这一组:\033[0m \033[1mctmodel %s\033[0m" % g)
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
    return configure_interactive(verify_conn=verify_conn, group=group or None)


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
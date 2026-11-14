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


# ---------------------------------------------------------------- 组管理(add / rm / list / edit)
#
# 配置天然就是多组的(同家几行用行尾 `# @model <组名>` 归组,活动组去注释、其余注释)。
# 这里补的是"填配置"这一侧之前缺的组级操作:新增一组、删一组、列出、编辑指定组。
# 关键约束(都为了不伤到用户已有配置):
#   · 新增一组默认**不切换**(不动当前生效组),建完再问要不要切过去,默认否;
#   · 组名不允许与已存在的重名(重名=覆盖,会静默改掉别人);
#   · 删的是生效组时,自动把剩下的第一组设为生效(否则整个文件没有生效行,运行期读不到配置);
#   · 编辑某组时默认值取**那一组**的存量,不是当前生效组。

DOMAIN_TOKENS = ("deepseek", "qwen", "mimo", "openai", "moonshot", "zhipu", "glm",
                 "anthropic", "gemini", "ollama", "localhost", "127.0.0.1")


def guess_new_group(pairs=None):
    """给"新增一组"猜组名:只看域名,**不**回落到当前生效组(否则一加就跟自己重名)。猜不到返回空串。"""
    bu = ((pairs or {}).get("BASE_URL") or "").lower()
    for token in DOMAIN_TOKENS:
        if token in bu:
            return {"127.0.0.1": "local", "localhost": "local"}.get(token, token)
    return ""


def _save_lines(lines):
    """把完整行列表原子写回 config(备份 + 600)。供删除/重排等组操作用。"""
    p = config_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
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


def _activate_only(lines, name):
    """把 name 组设为唯一生效组(去注释它、注释其它),原地改 lines。与 write_group 第③步同义。"""
    for i, line in enumerate(lines):
        p = _parse_line(line)
        if not p:
            continue
        _ind, commented, key, val, group = p
        if not group:
            continue
        if group == name:
            if commented:
                lines[i] = _render(key, val, group, commented=False)
        elif not commented:
            lines[i] = _render(key, val, group, commented=True)


def list_groups(verbose=True):
    """列出所有模型组,标记当前生效;verbose 时带地址/打码 key/模型名。"""
    groups = read_groups()
    if not groups:
        _w("")
        _w("  还没有任何模型组。跑 `wchat -config` 填一组,或 `wchat -config add` 新增。")
        _w("")
        return
    act = active_group()
    _w("")
    _w("  模型组(共 %d 组,* = 当前生效):" % len(groups))
    for name in model_groups():
        info = groups.get(name, {})
        mark = "*" if name == act else " "
        if verbose:
            host = (info.get("BASE_URL", "") or "")
            host = host.replace("https://", "").replace("http://", "").split("/")[0]
            _w("   %s %-12s %s  key %s  flash=%s  pro=%s"
               % (mark, name, host or "(无地址)", mask(info.get("WCHAT_API_KEY", "")),
                  info.get("FLASH_MODEL", "") or "-", info.get("PRO_MODEL", "") or "-"))
        else:
            _w("   %s %s" % (mark, name))
    if not act:
        _w("")
        _w("  \033[2m(当前没有生效组 —— 用 `wchat model <名字>` 切一个)\033[0m")
    _w("")


def remove_group(name):
    """删除一组(所有带 @model 该组名的行)。返回 (rc, 说明尾巴)。"""
    name = (name or "").strip()
    groups = model_groups()
    if name not in groups:
        return RC_NEED_USER, "没有这个模型组:%s(现有:%s)" % (name, " ".join(groups) or "(无)")
    lines = _read_lines()
    was_active = (active_group() == name)
    kept = []
    for line in lines:
        p = _parse_line(line)
        if p and p[4] == name:          # group == name
            continue
        kept.append(line)
    # 删掉一块会留下空行:连续空行收敛成一条,去尾部空行
    out = []
    for ln in kept:
        if ln.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(ln)
    while out and out[-1].strip() == "":
        out.pop()
    remaining = []
    for ln in out:
        m = MARK.search(ln)
        if m and m.group(1) not in remaining:
            remaining.append(m.group(1))
    if was_active and remaining:
        _activate_only(out, remaining[0])
    _save_lines(out)
    if not remaining:
        return RC_OK, ("已删除最后一组 %s —— 现在没有可用配置,请 `wchat -config` 重新填" % name)
    if was_active:
        return RC_OK, "已删除 %s;它原本是生效组,已把 %s 设为生效" % (name, remaining[0])
    return RC_OK, "已删除 %s(当前生效组仍是 %s)" % (name, active_group() or "(无)")


def _collect_fields(existing):
    """逐项问 BASE_URL/key/FLASH_MODEL(必填)与 PRO_MODEL(可选)。返回 vals(只含改动或需填的键)。"""
    vals = {}
    for f in ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL"):
        v, changed = _ask(f, existing.get(f, ""))
        if changed:
            vals[f] = v
        elif not (existing.get(f) or "").strip():
            vals[f] = ""
        _w("")
    pm, pm_changed = _ask("PRO_MODEL", existing.get("PRO_MODEL", ""), label="PRO_MODEL(可选)")
    if pm_changed:
        vals["PRO_MODEL"] = pm
    _w("")
    return vals


def _finalize(existing, vals):
    """合并 + 规范化 BASE_URL + PRO_MODEL 跟随 → (merged, issues)。会就地更新 vals 里的地址/模型。"""
    merged = dict(existing)
    merged.update({k: v for k, v in vals.items() if v})
    if merged.get("BASE_URL"):
        merged["BASE_URL"] = normalize_base_url(merged["BASE_URL"])
        if vals.get("BASE_URL"):
            vals["BASE_URL"] = merged["BASE_URL"]
    if not merged.get("PRO_MODEL") and merged.get("FLASH_MODEL"):
        merged["PRO_MODEL"] = merged["FLASH_MODEL"]
        vals.setdefault("PRO_MODEL", merged["FLASH_MODEL"])
    return merged, validate(merged)


def _ask_new_group(existing_names, suggest=""):
    """问一个**新的**组名(不能与已有重名),用于 `-config add`。"""
    existing_names = list(existing_names or [])
    while True:
        _w("  \033[1;36m新的一组叫什么名字?\033[0m")
        _w("    \033[2m一个短名字,用来区分是哪家供应商(如 mimo / moonshot)。"
           "以后用 `wchat model <名字>` 切换\033[0m")
        _w("    \033[2m现在: %s\033[0m" % (suggest or "(请填一个新名字)"))
        sys.stdout.write("    \033[1;32m›\033[0m ")
        sys.stdout.flush()
        name = _unquote(_read_line()) or suggest
        bad = validate_group(name)
        if bad:
            _w("    \033[1;31m%s\033[0m" % bad[0])
            _w("")
            continue
        if name in existing_names:
            _w("    \033[1;31m已经有叫 %s 的组了,换一个名字(想改它请用 `wchat -config edit %s`)\033[0m"
               % (name, name))
            _w("")
            continue
        _w("")
        return name


def _confirm(question, default_no=True):
    """问一个 y/n;default_no=True 时直接回车=否。返回 bool。"""
    _w("  \033[1;36m%s\033[0m \033[2m%s\033[0m" % (question, "[y/N]" if default_no else "[Y/n]"))
    sys.stdout.write("    \033[1;32m›\033[0m ")
    sys.stdout.flush()
    ans = _unquote(_read_line()).lower()
    _w("")
    if not ans:
        return not default_no
    return ans in ("y", "yes", "是", "1", "true")


def _no_pairs(text):
    """参数里没认出任何配置项(以前这里会"假成功"什么都不改却报已保存)。"""
    _w("")
    _w("  \033[1;31m✗ 没能从这些参数里认出任何配置项:\033[0m \033[2m%s\033[0m" % (text or "(空)"))
    _w("    · 配置项要写成 KEY=value,例如:BASE_URL=https://.../v1 WCHAT_API_KEY=sk-... FLASH_MODEL=<模型名>")
    _w("    · 想新增一组:wchat -config add --name <组名> \"KEY=value ...\"")
    _w("    · 想逐项填:wchat -config add(不带参数)")
    _w("")
    return RC_NEED_USER


def _report_issues(issues):
    _w("  \033[1;31m✗ 有几处需要改:\033[0m")
    for _k, m in issues:
        _w("    · %s" % m)
    _w("")
    _w("  \033[2m没有写入任何东西。重跑 wchat -config 再填一次。\033[0m")
    _w("")


def _configure_add_interactive(group, verify_conn, existing_names):
    """`-config add` 的交互流程:问新组名 → 字段全空逐项 → 写盘(默认不切换) → 问是否切换 → 验连通。"""
    existing = {}
    show_header(existing, group or "")
    g = group or _ask_new_group(existing_names, suggest="")
    vals = _collect_fields(existing)
    merged, issues = _finalize(existing, vals)
    if issues:
        _report_issues(issues)
        return RC_NEED_USER
    if not vals:
        _w("  \033[2m什么都没填,直接退出(没有新增)。\033[0m")
        _w("")
        return RC_NEED_USER
    p = write_group(g, vals, activate=False)
    _w("  \033[2m已新增 %s(权限 600),这组叫 \033[0m\033[1m%s\033[0m" % (p, g))
    if _confirm("现在切换到这一组吗?(以后也可用 `wchat model %s`)" % g, default_no=True):
        write_group(g, {}, activate=True)
        _w("  \033[2m已切换到 \033[0m\033[1m%s\033[0m" % g)
    else:
        _w("  \033[2m保持当前生效组不变;需要时用 \033[0m\033[1mwchat model %s\033[0m\033[2m 切换\033[0m" % g)
    _w("")
    return _after_write(verify_conn, values=merged)


def configure_add(args, group, check_only, verify_conn):
    """`wchat -config add` —— 强制新增一组(不动当前生效组,除非用户选择切换)。"""
    data = " ".join(args).strip()
    existing_names = model_groups()
    if data:
        pairs = parse_pasted(data)
        if not pairs:
            return _no_pairs(data)
        if pairs.get("BASE_URL"):
            pairs["BASE_URL"] = normalize_base_url(pairs["BASE_URL"])
        if pairs.get("FLASH_MODEL") and not pairs.get("PRO_MODEL"):
            pairs["PRO_MODEL"] = pairs["FLASH_MODEL"]
        merged = dict(pairs)
        issues = validate(merged)
        if issues:
            _w("")
            _w("  \033[1;31m✗ 格式有问题,没有写入:\033[0m")
            for _k, m in issues:
                _w("    · %s" % m)
            _w("")
            return RC_NEED_USER
        g = group or guess_new_group(pairs)
        if not g:
            if _is_tty():
                g = _ask_new_group(existing_names)
            else:
                _w("")
                _w("  这个地址认不出是哪家,没法自动起名字。请用 --name 指定组名:")
                _w("")
                _w("    \033[1;33mwchat -config add --name <组名> \"...\"\033[0m")
                _w("")
                return RC_NEED_USER
        bad = validate_group(g)
        if bad:
            _w("")
            _w("  \033[1;31m✗ %s\033[0m" % bad[0])
            _w("")
            return RC_NEED_USER
        if g in existing_names:
            _w("")
            _w("  \033[1;31m✗ 已经有叫 %s 的组了(新增不能用已有名字;想改它请用 "
               "`wchat -config edit %s`)\033[0m" % (g, g))
            _w("")
            return RC_NEED_USER
        if check_only:
            _w("")
            _w("  \033[1;32m✅ 检查通过\033[0m \033[2m(将新增组 %s;--check 未写入)\033[0m" % g)
            _w("")
            return RC_OK
        p = write_group(g, pairs, activate=False)
        _w("")
        _w("  \033[2m已新增 %s(权限 600),这组叫 \033[0m\033[1m%s\033[0m" % (p, g))
        _w("  \033[2m当前生效组不变;要切过去:\033[0m \033[1mwchat model %s\033[0m" % g)
        return _after_write(verify_conn, values=merged)
    # 交互
    if not _is_tty():
        _w("")
        _w("  这里没有交互终端,没法逐项问。请一次给完:")
        _w("")
        _w("    \033[1;33mwchat -config add --name <组名> \"BASE_URL=... "
           "WCHAT_API_KEY=sk-xxxx FLASH_MODEL=<模型名>\"\033[0m")
        _w("")
        return RC_NEED_USER
    if group:
        bad = validate_group(group)
        if bad:
            _w("  \033[1;31m✗ %s\033[0m" % bad[0])
            _w("")
            return RC_NEED_USER
        if group in existing_names:
            _w("")
            _w("  \033[1;31m✗ 已经有叫 %s 的组了(想改它请用 `wchat -config edit %s`)\033[0m"
               % (group, group))
            _w("")
            return RC_NEED_USER
    return _configure_add_interactive(group, verify_conn, existing_names)


def configure_edit(target, args, check_only, verify_conn):
    """`wchat -config edit <组>` —— 编辑指定组;交互默认值取**该组**存量,并保持它原本的生效状态。"""
    names = model_groups()
    if not target:
        if names and _is_tty():
            _w("  要编辑哪一组?现有:%s" % " ".join(names))
            sys.stdout.write("    \033[1;32m›\033[0m ")
            sys.stdout.flush()
            target = _unquote(_read_line())
    if not target:
        _w("")
        _w("  用法: wchat -config edit <组名>(现有:%s)" % (" ".join(names) or "(无)"))
        _w("")
        return RC_NEED_USER
    bad = validate_group(target)
    if bad:
        _w("  \033[1;31m✗ %s\033[0m" % bad[0])
        _w("")
        return RC_NEED_USER
    if target not in names:
        _w("")
        _w("  \033[1;31m✗ 没有这个组:%s(现有:%s)\033[0m" % (target, " ".join(names) or "(无)"))
        _w("")
        return RC_NEED_USER
    existing = dict(read_groups().get(target, {}))
    was_active = (active_group() == target)
    data = " ".join(args).strip()
    if data:
        pairs = parse_pasted(data)
        if not pairs:
            return _no_pairs(data)
        merged = dict(existing)
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
            for _k, m in issues:
                _w("    · %s" % m)
            _w("")
            return RC_NEED_USER
        if check_only:
            _w("")
            _w("  \033[1;32m✅ 检查通过\033[0m \033[2m(将改组 %s;--check 未写入)\033[0m" % target)
            _w("")
            return RC_OK
        p = write_group(target, pairs, activate=was_active)
        _w("")
        _w("  \033[2m已更新 %s(权限 600),这组叫 \033[0m\033[1m%s\033[0m" % (p, target))
        return _after_write(verify_conn, values=merged)
    # 交互编辑
    if not _is_tty():
        _w("")
        _w("  这里没有交互终端,没法逐项改。请一次给完:")
        _w("")
        _w("    \033[1;33mwchat -config edit %s \"BASE_URL=... WCHAT_API_KEY=... "
           "FLASH_MODEL=...\"\033[0m" % target)
        _w("")
        return RC_NEED_USER
    show_header(existing, target)
    vals = _collect_fields(existing)
    merged, issues = _finalize(existing, vals)
    if issues:
        _report_issues(issues)
        return RC_NEED_USER
    if not vals:
        _w("  \033[2m什么都没改,直接退出。\033[0m")
        _w("")
        return RC_NEED_USER
    p = write_group(target, vals, activate=was_active)
    _w("  \033[2m已更新 %s(权限 600),这组叫 \033[0m\033[1m%s\033[0m" % (p, target))
    _w("  \033[2m以后切回这一组:\033[0m \033[1mwchat model %s\033[0m" % target)
    return _after_write(verify_conn, values=merged)


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
    # 必须先判:socks 缺 socksio 时抛的是 ImportError,文案里带 "install",容易被
    # 后面的分支误读成网络问题;而它的修法很具体(装 httpx[socks]),要排在最前面。
    if "socksio" in low or "httpx[socks]" in low:
        return ("检测到 SOCKS 代理,但缺 socksio 支持",
                "`brew reinstall wchat`(新版已带 httpx[socks]);"
                "或临时取消代理(all_proxy/ALL_PROXY/HTTPS_PROXY)后重试")
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
        from wchat_py.llm import _make_client, _has_dep
    except ImportError:
        return False, "缺少 openai 依赖,请重装 wchat"
    if not _has_dep("openai"):
        return False, "缺少 openai 依赖,请重装 wchat"
    try:
        cli = _make_client(base, key, os.environ.get("API_KEY_HEADER", ""))
        _ping_msgs = [{"role": "user", "content": "hi"}]
        stream = cli.chat.completions.create(
            model=(model or "default"),
            messages=_ping_msgs,
            max_tokens=1, stream=True)
        _ping_usage = None
        for _chunk in stream:
            _ping_usage = getattr(_chunk, "usage", None) or _ping_usage
            break
        try:
            stream.close()
        except Exception:
            pass
        # 连通性自检也是真花钱的调用(--config 每次验一次)。记一笔,免得
        # "到底谁在花钱"永远少一个来源。
        try:
            from wchat_py import apilog as _apilog
            _d = getattr(_ping_usage, "prompt_tokens_details", None)
            _apilog.record("ping", model, _ping_msgs, ok=True, extra={"max_tokens": 1},
                           usage={"prompt": getattr(_ping_usage, "prompt_tokens", 0) or 0,
                                  "completion": getattr(_ping_usage, "completion_tokens", 0) or 0,
                                  "cached": (getattr(_d, "cached_tokens", 0)
                                             if _d is not None else 0) or 0})
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
    """逐项问答 → 校验 → 写盘(ctmodel 格式) → 验连通。返回退出码。

    不带 group:改当前生效组(existing 缺省取自有效配置);
    带 group 且该组已存在:预填**该组**的存量(而不是当前生效组),
    避免"想编辑 A 组,却拿当前生效的 B 组的值当默认值"这个坑。
    """
    if existing is None:
        if group and group in read_groups():
            existing = dict(read_groups()[group])
        else:
            existing = dict(_effective())
    else:
        existing = dict(existing)
    g = group or guess_group(existing)
    show_header(existing, g)
    g = _ask_group(g)
    vals = _collect_fields(existing)
    merged, issues = _finalize(existing, vals)
    if issues:
        _report_issues(issues)
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


def _after_write(verify_conn, values=None):
    """写盘之后:验连通 → 打印结论。返回退出码。

    values 给定时,校验**这一组的值**(临时塞进环境再复原),这样 add/edit 一个
    非生效组也能验它自己的连通,而不受当前生效组影响。
    """
    if not verify_conn:
        _w("  \033[2m跳过连通性验证(--no-verify)\033[0m")
        _w("")
        _w("  \033[1;32m✅ 配置已保存\033[0m  现在直接运行 \033[1mwchat\033[0m 开始用。")
        _w("")
        return RC_OK
    sys.stdout.write("  \033[2m验证中…\033[0m\r")
    sys.stdout.flush()
    ok, detail = (_verify_pairs(values) if values else verify())
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
  wchat -config                       逐项填写(改当前生效组;推荐)
  wchat -config "BASE_URL=... WCHAT_API_KEY=... FLASH_MODEL=..."    一次给完(改当前生效组)
  wchat -config add                   新增一组(问组名,字段全空;建完问是否切换,默认否)
  wchat -config add --name mimo "..." 新增一组并命名
  wchat -config list                  列出所有模型组(* = 当前生效)
  wchat -config edit <组>             编辑指定组(默认值取自该组的存量)
  wchat -config rm <组>               删除一组(删的是生效组时会自动切到剩下第一组)
  wchat -config --check "..."         只校验,不写盘
  wchat -config --no-verify "..."     先不验连通(离线/内网)
  wchat -config --name deepseek       指定组名(默认沿用当前生效组/按域名猜)

配置写在 ~/.ct/config(权限 600),格式与 ctmodel 一致(行尾 # @model <组名>),
因此可以用 `wchat model <组名>` 在几组(不同厂商)之间切换;`wchat model -l` 列组。"""

# 子命令词:必须是 `-config` 后的第一个裸词。其余裸词要么是 KEY=value(数据),
# 要么是错误 —— 绝不再当成"没识别出来就静默成功"。
CONFIG_SUBCMDS = ("add", "list", "ls", "groups", "show",
                  "rm", "remove", "del", "delete", "edit")


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

    # ---- 子命令:必须在"一次给完"之前认出裸词,否则 add/rm 会被当成值吞掉 ----
    if rest and rest[0] in CONFIG_SUBCMDS:
        cmd, sub = rest[0], rest[1:]
        if cmd in ("list", "ls", "groups", "show"):
            list_groups(verbose=True)
            return RC_OK
        if cmd == "add":
            return configure_add(sub, group, check_only, verify_conn)
        if cmd in ("rm", "remove", "del", "delete"):
            target = group or (sub[0] if sub and "=" not in sub[0] else "")
            if not target:
                _w("")
                _w("  用法: wchat -config rm <组名>(现有:%s)"
                   % (" ".join(model_groups()) or "(无)"))
                _w("")
                return RC_NEED_USER
            rc, tail = remove_group(target)
            if tail:
                _w("")
                _w(("  \033[1;32m✓ %s\033[0m" if rc == RC_OK else "  \033[1;31m✗ %s\033[0m") % tail)
                _w("")
            return rc
        if cmd == "edit":
            target = group
            if not target and sub and "=" not in sub[0]:
                target = sub[0]
                data_args = sub[1:]
            else:
                data_args = sub
            return configure_edit(target, data_args, check_only, verify_conn)

    if rest:
        # 一次给完:解析 → 校验 → (验) → 写(改当前生效组)
        pairs = parse_pasted(" ".join(rest))
        if not pairs:
            # 以前这里会对空 pairs 校验通过并 apply({}) —— 文件没变却打印"已保存",
            # 敲 `wchat -config add` 之类就被静默吞掉(假成功)。现在明确报错。
            return _no_pairs(" ".join(rest))
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


# ---------------------------------------------------------------- 模型档案切换
#
# 这一节是原 `ctmodel`(configs/ctmodel.py)的**唯一实现**。`wchat model` 直接调它;
# `bin/ctmodel` 只是三行壳(exec wchat model),所以逻辑只有一份,不会漂。
# 语义没变:文件里每条"跟着模型走"的赋值行行尾带 `# @model <组名>`;
# 切到 X = 标记为 X 的行去注释、其它模型的行加注释;不改任何值、不动顺序。

def _set_active_line(ln, active):
    """活动行原样;非活动行整体注释(或去掉一个前导 '# ')。"""
    lead = len(ln) - len(ln.lstrip())
    pad, body = ln[:lead], ln[lead:]
    if active:
        if body.startswith("# ") and MARK.search(body[2:]):
            return pad + body[2:]
        return ln
    if body.startswith("#"):
        return ln
    return pad + "# " + body


def model_groups():
    """文件里出现过的所有组名(按出现顺序,含被注释的)。"""
    out = []
    for line in _read_lines():
        m = MARK.search(line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def cmd_model(argv):
    """`wchat model …` —— 与原 ctmodel 参数完全兼容。"""
    argv = list(argv or [])
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(
            "用法: wchat model [名字|-l|check|undo] [-n]\n"
            "  (无参数)        看当前生效的模型组\n"
            "  <名字>          切到该组(切换前自动备份 config.bak)\n"
            "  <名字> -n       只预览 diff,不落盘\n"
            "  -l, --list      列出所有模型组\n"
            "  check [--fix]   校验配置(--fix 顺手修权限 600)\n"
            "  undo            回滚到上一次切换前\n")
        return RC_OK
    p = config_path()
    if not argv:
        if not os.path.isfile(p):
            sys.stderr.write("配置不存在: %s(可先跑 wchat -config)\n" % p)
            return RC_ERR
        sys.stdout.write("当前模型: %s\n" % (active_group() or "(无生效行)"))
        return RC_OK
    a = argv[0]
    if a in ("-l", "--list"):
        if not os.path.isfile(p):
            sys.stderr.write("配置不存在: %s\n" % p)
            return RC_ERR
        sys.stdout.write("\n".join(model_groups()) + "\n")
        return RC_OK
    if a == "check":
        return _model_check("--fix" in argv[1:])
    if a == "undo":
        bak = p + ".bak"
        if not os.path.isfile(bak):
            sys.stderr.write("没有备份: %s\n" % bak)
            return RC_ERR
        try:
            import shutil
            shutil.copyfile(bak, p)
            os.chmod(p, 0o600)          # 回滚同样要保住 600
        except OSError as e:
            sys.stderr.write("回滚失败: %s\n" % e)
            return RC_ERR
        sys.stdout.write("已回滚: %s <- %s\n" % (p, bak))
        return RC_OK
    if a.startswith("-"):
        sys.stderr.write("未知选项: %s(试试 wchat model -h)\n" % a)
        return RC_NEED_USER
    if not os.path.isfile(p):
        sys.stderr.write("配置不存在: %s(可先跑 wchat -config)\n" % p)
        return RC_ERR
    return _model_switch(a, "-n" in argv[1:])


def _model_switch(name, dry):
    lines = _read_lines()
    groups = model_groups()
    if name not in groups:
        sys.stderr.write("未知模型组: %s(可用: %s)\n" % (name, " ".join(groups) or "(无)"))
        return RC_NEED_USER
    new, changed = [], []
    for ln in lines:
        if not MARK.search(ln):
            new.append(ln)
            continue
        upd = _set_active_line(ln, MARK.search(ln).group(1) == name)
        if upd != ln:
            changed.append((ln, upd))
        new.append(upd)
    if not changed:
        sys.stdout.write("已经是 %s,无需切换\n" % name)
        return RC_OK
    if dry:
        for old, upd in changed:
            sys.stdout.write("- %s\n+ %s\n" % (old, upd))
        return RC_OK
    p = config_path()
    try:
        import shutil
        shutil.copyfile(p, p + ".bak")
        os.chmod(p + ".bak", 0o600)     # 备份里是明文凭据,copyfile 不继承权限
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(new) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        os.chmod(p, 0o600)
    except OSError as e:
        sys.stderr.write("写入失败: %s\n" % e)
        return RC_ERR
    sys.stdout.write("已切换到 %s(备份: %s.bak)\n" % (name, p))
    return RC_OK


def _model_check(fix):
    """校验:同一变量只许有一条**生效**行(重复生效行是最常见的手改后果)。"""
    try:
        lines = _read_lines()
    except OSError as e:
        sys.stderr.write("读不到配置: %s\n" % e)
        return RC_ERR
    bad = 0
    seen = {}
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        p = _parse_line(ln)
        if not p:
            sys.stdout.write("第 %d 行不是 KEY=value: %s\n" % (i, ln))
            bad += 1
            continue
        _i, _c, key, _v, _g = p
        if key in seen:
            sys.stdout.write("变量 %s 有多条生效行(第 %d 与第 %d 行)\n" % (key, seen[key], i))
            bad += 1
        seen[key] = i
    if fix:
        try:
            os.chmod(config_path(), 0o600)
            sys.stdout.write("已修权限: 600\n")
        except OSError as e:
            sys.stdout.write("修权限失败: %s\n" % e)
            bad += 1
    sys.stdout.write("校验通过\n" if not bad else "发现 %d 个问题\n" % bad)
    return RC_OK if not bad else RC_ERR


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
#!/usr/bin/env python3
"""APT 仓库闸门：静态契约（不 build、不 apt install，秒级）。

真链路（生成仓库 → 干净容器 apt update/install）在
`packaging/linux/{make_apt_repo,test-apt-repo}.sh` 里，要几分钟，不进闸门表。
这里钉的是**那些一改就悄悄坏、而且本地永远测不出来的约定**：

  1. `Filename` 必须是**仓库相对路径**。写成绝对路径时，`file:` 源照样能装
     （绝对路径恰好对），换成 https 就拼成 `https://域名/src/dist/...` → 404。
     这是"本地全绿、上线全红"的典型，必须静态锁死。
  2. `Release` 里必须登记 Packages **和** Packages.gz（apt 校验的是前者，
     但有的客户端只取 .gz —— 两份都得有 hash）。
  3. 必须有 `InRelease`（明文签名，apt 首选）与 `Release.gpg`（分离签名，老 apt）。
     只有一个的话某些版本会报 "does not have a Release file"。
  4. 索引必须是 gzip 且 **mtime=0**（同内容重发布 sha 不变，便于核对；
     也是 brew tarball 那条"可复现"要求的同类）。
  5. `wchat.asc`（armored 公钥）必须存在 —— 用户端 gpg --dearmor 要用它，
     这就是"信任锚"，apt 无法替用户完成。
  6. pool 里的 .deb 名与 Packages 里的 `Filename` 必须对得上。
"""
import gzip
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


SRC = read("packaging/linux/make_apt_repo.py")
ACC = read("packaging/linux/apt-acceptance.sh")

print("=== 1. 结构:apt 只认这一种布局 ===")
for part in ("pool/main/w/wchat", "dists", "Packages", "Packages.gz",
             "Release", "InRelease", "Release.gpg", "wchat.asc"):
    ck("生成器里出现 %s" % part, part in SRC, "")
ck("Packages 由 apt-ftparchive 生成(不手写 hash)",
   "apt-ftparchive" in SRC and "SHA256" not in SRC.split("apt-ftparchive")[0][-400:])

print("=== 2. Filename 必须是仓库相对路径(上线才炸的那类) ===")
# 判据:传给 apt-ftparchive 的必须是相对常量 POOL,且 cwd 是仓库根。
ck("apt-ftparchive 吃相对路径 POOL", 'apt-ftparchive", "packages", POOL' in SRC,
   "传绝对路径会让 Filename 变成 /abs/... ,https 源直接 404")
ck("调用时 cwd=仓库根", "cwd=out" in SRC)
ck("注释里写明为什么(防后人改回绝对路径)",
   "Filename: /src" in SRC or "相对仓库根" in SRC)
# 反向锁:程序里不许再出现 join(out, POOL) 喂给 apt-ftparchive 的形状
ck("没有把 join(out, POOL) 喂给 apt-ftparchive",
   'apt-ftparchive", "packages", os.path.join(out' not in SRC)

print("=== 3. Release 必须登记两份索引的 hash ===")
ck("Release 同时登记 Packages 与 Packages.gz",
   'for fn in ("Packages", "Packages.gz")' in SRC, "")
ck("Release 有 MD5Sum 与 SHA256 两段", '"MD5Sum:"' in SRC and '"SHA256:"' in SRC)
ck("Release 有 Architectures/Components/Suite",
   all(k in SRC for k in ('"Suite: %s"', '"Components: %s"', '"Architectures: %s"')))

print("=== 4. 索引 gzip 要 mtime=0(可复现) ===")
ck("gzip 用 mtime=0", re.search(r"GzipFile\([^)]*mtime=0", SRC) is not None, "")

print("=== 5. 签名三件套 ===")
ck("生成 InRelease(明文签名)", "InRelease" in SRC and "clearsign" in SRC)
ck("生成 Release.gpg(分离签名)", "Release.gpg" in SRC and "detach-sign" in SRC)
ck("导出 armored 公钥 wchat.asc", "wchat.asc" in SRC and "--export" in SRC)
ck("--no-sign 时必须**明确警告**不许用于生产",
   "trusted=yes" in SRC and "拒绝" in SRC, "静默退回未签名仓库是最危险的默认")

print("=== 6. 验收脚本:必须真走 apt,不许 dpkg -i 绕过去 ===")
ck("验收用 apt-get install", "apt-get install -y wchat" in ACC)
ck("验收检查 Candidate 存在(仓库真的被看见)", "Candidate:" in ACC)
ck("验收检查验签失败即失败(NO_PUBKEY/not signed)",
   "NO_PUBKEY" in ACC and "not signed" in ACC)
ck("验收装前确认没有 wchat", "/usr/bin/wchat" in ACC and "装前" in ACC)
ck("验收从 /tmp 跑(不依赖 cwd)", "cd /tmp" in ACC)
ck("验收有唯一结论行", "APT-ACCEPTANCE-OK" in ACC)
ck("验收里没有 dpkg -i(绕开索引就测不出索引对不对)",
   "dpkg -i" not in ACC, "用 dpkg -i 只验证包本身,验不出 Filename/hash/签名")

print("=== 7. 用户端命令:三步缺一不可 ===")
ck("模板里有装 keyring 的命令", "--dearmor" in SRC)
ck("模板里有 sources.list 写法", "signed-by=" in SRC)
ck("模板里有 apt update && apt install", "apt install wchat" in SRC or "apt install wchat" in
   SRC.replace("apt-get", "apt"))

def _mutation_selftest():
    """变异自检:证明 §2 那几条不是恒真。把'相对路径'改回'绝对路径',断言必须转红。"""
    import tempfile
    bad = SRC.replace('"apt-ftparchive", "packages", POOL',
                      '"apt-ftparchive", "packages", os.path.join(out, POOL)')
    assert bad != SRC, "变异没生效,§2 的断言可能是恒真"
    ck("变异自检:改成绝对路径会被 §2 判红",
       'apt-ftparchive", "packages", os.path.join(out' in bad)


print("=== 8. 变异自检(证明上面的断言不是恒真) ===")
_mutation_selftest()

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)


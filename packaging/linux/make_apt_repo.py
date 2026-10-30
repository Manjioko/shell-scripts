#!/usr/bin/env python3
"""把 .deb 组织成一个**可被 apt 直接消费**的仓库（pool + dists + 签名）。

## 产出结构（apt 只认这一种）

    <repo>/
      pool/main/w/wchat/wchat_0.2.7_arm64.deb
      dists/stable/main/binary-arm64/Packages
      dists/stable/main/binary-arm64/Packages.gz
      dists/stable/Release
      dists/stable/InRelease          <- 明文签名（apt 首选）
      dists/stable/Release.gpg        <- 分离签名（老 apt 兜底）
      wchat.gpg / wchat.asc           <- 给用户装的公钥（keyring / armored）

## 为什么不能只给 .deb 的 URL

apt 要的是**索引**,不是文件。它先取 `dists/<suite>/InRelease` 验签,再从
`Packages.gz` 里查包名→文件名→sha256,校验通过才下载。少任何一环都不用谈
`apt install wchat`。

## 用法

    # 生成（首次会自动建签名密钥;生产环境应改成用你自己的离线密钥）
    python3 packaging/linux/make_apt_repo.py --deb dist/*.deb --out dist/apt \\
        --gnupg-home .scratch/gnupg --key-email wchat@example.com

    # 只生成不签名（本地试装/CI 冒烟;用户端要写 [trusted=yes]）
    python3 packaging/linux/make_apt_repo.py --deb dist/*.deb --out dist/apt --no-sign
"""
import argparse
import gzip
import hashlib
import os
import shutil
import subprocess
import sys

POOL = "pool/main/w/wchat"


def _run(argv, **kw):
    r = subprocess.run(argv, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.stderr.write("✗ %s\n%s%s\n" % (" ".join(argv), r.stdout, r.stderr))
        sys.exit(1)
    return r.stdout


def _field(deb, name):
    return _run(["dpkg-deb", "-f", deb, name]).strip()


def _ensure_key(gnupg_home, email, name):
    """没有密钥就生成一把(仅用于**演示/自建仓库**)。

    生产环境应把私钥放在离线机器/CI secret 里,这里生成只是为了把链路跑通:
    没有签名的 APT 仓库在现代 apt 上默认会被拒(见 --no-sign 的说明)。
    """
    os.makedirs(gnupg_home, mode=0o700, exist_ok=True)
    env = dict(os.environ, GNUPGHOME=gnupg_home)
    out = _run(["gpg", "--batch", "--list-secret-keys", "--with-colons"], env=env)
    if "sec:" in out:
        for line in out.splitlines():
            if line.startswith("sec:"):
                pass
        fpr = ""
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("fpr:"):
                fpr = line.split(":")[9]
                break
        return fpr
    _run(["gpg", "--batch", "--passphrase", "", "--quick-generate-key",
          "%s <%s>" % (name, email), "rsa3072", "sign", "never"], env=env)
    out = _run(["gpg", "--batch", "--list-secret-keys", "--with-colons"], env=env)
    for line in out.splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    sys.exit("✗ 生成了密钥但读不到指纹")


def build(debs, out, suite, component, arch_override="", sign=True,
          gnupg_home="", email="", name="wchat archive signing key"):
    # apt-ftparchive 要**绝对路径**:它内部 realpath 解析配置里的 Dir/File,
    # 相对路径会报 `Failed to resolve pool/... (2: No such file or directory)`,
    # 而那句报错看不出是"路径要绝对"这件事。
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    pool = os.path.join(out, POOL)
    os.makedirs(pool, exist_ok=True)

    # 1) 归集 .deb 到 pool（同名覆盖:同一版本重发就用新的）
    placed = []
    for deb in debs:
        dst = os.path.join(pool, os.path.basename(deb))
        if os.path.abspath(deb) != os.path.abspath(dst):
            shutil.copy2(deb, dst)
        placed.append(dst)

    # 2) 按架构分组(apt 的 Packages 是**每个架构一份**)
    by_arch = {}
    for deb in placed:
        arch = arch_override or _field(deb, "Architecture")
        by_arch.setdefault(arch, []).append(deb)

    dists = os.path.join(out, "dists", suite)
    for arch, group in sorted(by_arch.items()):
        bindir = os.path.join(dists, component, "binary-%s" % arch)
        os.makedirs(bindir, exist_ok=True)
        # 用 apt-ftparchive 生成 Packages:它算 Size/SHA256/MD5sum/Filename 与
        # apt 自己的解析器同源。手写这些字段是"看着对、装时报 hash 不匹配"的经典来源。
        # 用 apt-ftparchive 生成内容(Size/SHA256/MD5sum/Filename 与 apt 的解析器同源),
        # 但**自己把 stdout 写进文件**:`-c` 里那句 `File "Packages" ...` 在
        # apt-ftparchive 上行为不稳定(实测:不报错、stdout 有完整内容、文件却没写出来),
        # 于是整条链会在最后一步"没有 Packages"处失败,而报错完全指不到原因。
        # ⚠ 必须给**相对路径** + cwd=仓库根:apt 的 `Filename` 字段会原样取这个路径,
        #   而它是**相对仓库根**的(apt 用 `URI + Filename` 拼下载地址)。
        #   传绝对路径会得到 `Filename: /src/dist/apt/pool/...` —— 本地 file: 源
        #   看起来还能用(绝对路径恰好对),换成 https 就拼成
        #   `https://域名/src/dist/apt/pool/...` 直接 404。这类错**在本地永远测不出来**。
        pkgout = _run(["apt-ftparchive", "packages", POOL], cwd=out)
        pkg = os.path.join(bindir, "Packages")
        raw = pkgout.encode("utf-8")
        if not raw.strip():
            sys.exit("✗ apt-ftparchive 没输出内容(pool 里没有 .deb?)")
        with open(pkg, "wb") as f:
            f.write(raw)
        with gzip.GzipFile(pkg + ".gz", "wb", mtime=0) as gz:
            gz.write(raw)
        n = raw.count(b"\nPackage:") + (1 if raw.startswith(b"Package:") else 0)
        print("  Packages(%s): %d 个包 / %d 字节" % (arch, n, len(raw)))

    # 3) Release:列出各索引文件的 size+hash。apt 验完 Release 的签名,再按这里
    #    的 sha256 校验 Packages —— 所以 Releases 里**必须**包含 Packages 与 .gz。
    rel = os.path.join(dists, "Release")
    lines = [
        "Origin: wchat",
        "Label: wchat",
        "Suite: %s" % suite,
        "Codename: %s" % suite,
        "Architectures: %s" % " ".join(sorted(by_arch)),
        "Components: %s" % component,
        "Description: wchat APT repository",
    ]
    files = []
    for arch in sorted(by_arch):
        for fn in ("Packages", "Packages.gz"):
            p = os.path.join(dists, component, "binary-%s" % arch, fn)
            with open(p, "rb") as f:
                data = f.read()
            files.append((os.path.join(component, "binary-%s" % arch, fn),
                          len(data), hashlib.md5(data).hexdigest(),
                          hashlib.sha256(data).hexdigest()))
    lines.append("Date: %s" % _run(["date", "-u", "+%a, %d %b %Y %H:%M:%S UTC"]).strip())
    lines.append("MD5Sum:")
    for rel_path, size, md5, _sha in files:
        lines.append(" %s %16d %s" % (md5, size, rel_path))
    lines.append("SHA256:")
    for rel_path, size, _md5, sha in files:
        lines.append(" %s %16d %s" % (sha, size, rel_path))
    with open(rel, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("  Release: %d 个索引文件已登记" % len(files))

    # 4) 签名:InRelease(明文签名,apt 首选) + Release.gpg(分离签名,老 apt)
    if sign:
        if not gnupg_home or not email:
            sys.exit("✗ 签名需要 --gnupg-home 与 --key-email(或用 --no-sign 只做本地试装)")
        fpr = _ensure_key(gnupg_home, email, name)
        env = dict(os.environ, GNUPGHOME=gnupg_home)
        _run(["gpg", "--batch", "--yes", "--armor", "--local-user", fpr,
              "--clearsign", "--output", rel + ".in", rel], env=env)
        os.replace(rel + ".in", os.path.join(dists, "InRelease"))
        _run(["gpg", "--batch", "--yes", "--armor", "--local-user", fpr,
              "--detach-sign", "--output", os.path.join(dists, "Release.gpg"), rel], env=env)
        pub = _run(["gpg", "--batch", "--armor", "--export", fpr], env=env)
        with open(os.path.join(out, "wchat.asc"), "w", encoding="utf-8") as f:
            f.write(pub)
        # 用户端 keyring 用的就是这串（gpg --dearmor 后放 /usr/share/keyrings/）
        print("  签名: %s" % fpr)
        print("  公钥: %s/wchat.asc" % out)
    else:
        print("  ⚠ 未签名:现代 apt 默认拒绝,用户端源必须写 [trusted=yes](不安全,仅本地试装)")

    # 5) 给用户抄的两段配置（deb822 新格式,sources.list 老格式各一份）
    with open(os.path.join(out, "wchat.sources"), "w", encoding="utf-8") as f:
        f.write("Types: deb\n"
                "URIs: https://<你的域名>/apt\n"
                "Suites: %s\n"
                "Components: %s\n"
                "Architectures: %s\n"
                "Signed-By: /usr/share/keyrings/wchat.gpg\n" % (suite, component,
                                                                 " ".join(sorted(by_arch))))
    with open(os.path.join(out, "READ-ME.txt"), "w", encoding="utf-8") as f:
        f.write(
            "wchat APT 仓库\n"
            "==============\n\n"
            "用户端（Ubuntu 24.04 / Debian 12 起推荐 deb822）:\n\n"
            "  sudo install -d -m 0755 /usr/share/keyrings\n"
            "  curl -fsSL https://<域名>/apt/wchat.asc | sudo gpg --dearmor -o /usr/share/keyrings/wchat.gpg\n"
            "  echo 'deb [signed-by=/usr/share/keyrings/wchat.gpg] https://<域名>/apt stable main' \\\n"
            "    | sudo tee /etc/apt/sources.list.d/wchat.list\n"
            "  sudo apt update && sudo apt install wchat\n\n"
            "之后升级 = sudo apt update && sudo apt upgrade。\n")
    print("  配置模板: %s/wchat.sources  %s/READ-ME.txt" % (out, out))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deb", nargs="+", required=True)
    ap.add_argument("--out", default="dist/apt")
    ap.add_argument("--suite", default="stable")
    ap.add_argument("--component", default="main")
    ap.add_argument("--arch", default="", help="覆盖架构(不填就按 deb 的 Architecture 字段)")
    ap.add_argument("--no-sign", action="store_true")
    ap.add_argument("--gnupg-home", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         ".gnupg-apt"))
    ap.add_argument("--key-email", default="")
    a = ap.parse_args()
    build(a.deb, a.out, a.suite, a.component, a.arch, not a.no_sign,
          a.gnupg_home, a.key_email)


if __name__ == "__main__":
    main()
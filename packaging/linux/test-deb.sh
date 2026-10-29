#!/bin/sh
# deb 验收:在**干净的** ubuntu:24.04(不带 venv、不带源码)里 dpkg -i 装上去,
# 然后只用 `/usr/bin/wchat` 验证"装完就能用"。
#
# 关键:这里**不挂载源码**。挂源码就测不出"包里的东西是否自足"——这类测试最容易
# 犯的错(在自己机器上永远绿,因为 cwd 里就有那份代码)。
#
# 用法(仓库根):sh packaging/linux/test-deb.sh dist/wchat_0.2.7_arm64.deb
set -e
DEB="${1:?用法: test-deb.sh <xxx.deb>}"
[ -f "$DEB" ] || { echo "找不到 $DEB"; exit 1; }

# 只挂 deb 所在目录(只读) + 验收脚本;容器内逻辑在 deb-acceptance.sh 里,
# 不在这里套多层引号(那样引号会随目录名/文件名变化,且极难读)。
docker run --rm \
    -v "$PWD":"/src":ro \
    -v "$(cd "$(dirname "$DEB")" && pwd)":"/debs":ro \
    wchat-deb:24.04 \
    sh /src/packaging/linux/deb-acceptance.sh "/debs/$(basename "$DEB")"


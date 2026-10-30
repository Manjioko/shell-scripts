#!/bin/sh
# APT 仓库端到端验收:**在干净容器里**只走 apt(不加任何 --allow-unauthenticated、
# 不手动 dpkg -i),证明"一行 apt install wchat"这条路真的通。
#
# 验的是完整信任链,不是"文件复制成功":
#   ① 用户按 README 装公钥 → ② 配 source → ③ apt update **验签通过**
#   ④ apt install wchat(靠 Packages 索引找到包) → ⑤ 跑起来 → ⑥ apt remove 清干净
#
# 用法(仓库根):
#   sh packaging/linux/test-apt-repo.sh dist/apt
set -e
REPO="${1:?用法: test-apt-repo.sh <apt-repo-dir>}"
[ -d "$REPO" ] || { echo "找不到仓库目录 $REPO"; exit 1; }

# ⚠ 只挂**单个脚本文件**,不挂整个仓库:验收必须证明"包自足",挂上源码就测不出来
#   (容器里 cwd 就有那份代码,缺文件也照样跑)。
docker run --rm \
    -v "$(cd "$REPO" && pwd)":/apt:ro \
    -v "$PWD/packaging/linux/apt-acceptance.sh":/usr/local/bin/apt-acceptance.sh:ro \
    wchat-deb:24.04 \
    sh /usr/local/bin/apt-acceptance.sh /apt
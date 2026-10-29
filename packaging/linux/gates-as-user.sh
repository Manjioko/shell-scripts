#!/bin/sh
# 以**普通用户 + 真实 /home** 跑整套闸门(Linux 容器内)。**在宿主机上运行**。
#
# 为什么必须这样跑(两条前提,root 或 HOME 在 /tmp 都会让它们不成立):
#   ① `技能目录` 那几条测的是"他人属主目录被退回" —— root 下写谁都能成,前提不成立;
#   ② HOME 落在 /tmp 下会让"工作根内的凭据词可豁免"这条既有设计把 ~/.npmrc 也吐掉,
#      于是 `判据出口话术`/`硬墙语料`/`确认链` 一起红 —— 那是**前提不成立**,不是产品坏了。
#
# 用法(仓库根):sh packaging/linux/gates-as-user.sh
#
# ⚠ 不要把 `su ... || true` 写进来:那会吞掉退出码,"整套跑完了"看起来成功而实际有红。
#   本脚本的退出码 = 闸门表的退出码(有红就是非 0)。
set -e
docker run --rm -v "$PWD":/src:ro -v "$PWD/.scratch/emptybrew":/opt/homebrew:ro \
    wchat-linux:24.04 sh -c '
set -e
useradd -m -s /bin/bash runner 2>/dev/null || true
cp -R /src /tmp/w
chown -R runner:runner /tmp/w
exec su runner -c "export PATH=/work/.venv/bin:\$PATH HOME=/home/runner; cd /tmp/w && bash tests/run_gates.sh"
'


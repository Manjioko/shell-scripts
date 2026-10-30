#!/bin/sh
# 用**真 HTTP**验证 APT 源（`file:` 源测不出 URL 拼接问题，必须走 http）。
#
# 为什么这一步不能省:
#   `file:` 源里 apt 直接用绝对/相对路径取文件,即使 `Filename` 写错（绝对路径）
#   也照样装 —— 于是"本地全绿、上线 404"。只有真 http 才能把
#   `URI + Filename` 拼出来的地址暴露出来。
#
# 做法:宿主机 `python3 -m http.server` 把仓库目录当静态站点,容器内用
# `http://host.docker.internal:PORT` 当源（等价于 https://<域名>/apt）。
#
# 用法(仓库根): sh packaging/linux/test-apt-http.sh dist/apt
set -e
REPO="${1:?用法: test-apt-http.sh <apt-repo-dir>}"
REPO="$(cd "$REPO" && pwd)"
PORT=$(( ( RANDOM % 2000 ) + 18000 ))

python3 -m http.server "$PORT" --directory "$REPO" --bind 0.0.0.0 > /tmp/apt-http.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT INT TERM
sleep 1

# 先确认宿主侧真的能取到索引（否则容器里失败会像是 apt 的问题）
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/dists/stable/InRelease" \
    || { echo "✗ 宿主侧取不到 InRelease(端口 $PORT)"; cat /tmp/apt-http.log; exit 1; }
echo "  宿主侧 OK:http://127.0.0.1:$PORT"

docker run --rm \
    --add-host=host.docker.internal:host-gateway \
    -e APT_URI="http://host.docker.internal:$PORT" \
    -v "$PWD/packaging/linux/apt-acceptance.sh":/usr/local/bin/apt-acceptance.sh:ro \
    -v "$REPO/wchat.asc":/apt/wchat.asc:ro \
    wchat-deb:24.04 \
    sh /usr/local/bin/apt-acceptance.sh /apt --http


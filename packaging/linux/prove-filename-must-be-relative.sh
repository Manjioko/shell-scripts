#!/bin/sh
# 反向验证:把 Packages 里的 Filename 改成**绝对路径**,看 apt 实际拼出什么 URL。
# 用途:证明"Filename 必须仓库相对"这条不是理论洁癖 —— 它真的会指到错地方。
#
# 做法:用 `apt-get install --print-uris`(只打印要下载的地址,不真装),
#      这是我们能拿到的最权威的"apt 打算怎么拼 URL"。
set -e
REPO="${1:?用法: prove-filename-must-be-relative.sh <apt-repo-dir>}"
REPO="$(cd "$REPO" && pwd)"
PORT=$(( ( RANDOM % 2000 ) + 20000 ))

python3 -m http.server "$PORT" --directory "$REPO" --bind 0.0.0.0 > /tmp/apt-proof.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT INT TERM
sleep 1

docker run --rm --add-host=host.docker.internal:host-gateway \
    -e APT_URI="http://host.docker.internal:$PORT" \
    -v "$REPO/wchat.asc":/apt/wchat.asc:ro \
    wchat-deb:24.04 sh -c '
set -e
install -d -m 0755 /usr/share/keyrings
gpg --batch --yes --dearmor -o /usr/share/keyrings/wchat.gpg /apt/wchat.asc
echo "deb [signed-by=/usr/share/keyrings/wchat.gpg] $APT_URI stable main" \
    > /etc/apt/sources.list.d/wchat.list
apt-get update > /dev/null 2>&1
echo "  源: $APT_URI"
echo "  apt 打算下载:"
apt-get install -y --print-uris wchat 2>/dev/null | grep -o "http://[^ ]*\.deb" | head -3 | sed "s/^/    /"
' 2>&1 | tail -8


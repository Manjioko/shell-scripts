#!/bin/sh
# APT 仓库验收的**容器内**部分。由 test-apt-repo.sh / test-apt-http.sh 调起。
# 单独一个文件是为了引号只有一层(项目里因为多层引号踩过 4 次)。
#
# 两种模式:
#   sh apt-acceptance.sh /apt          → 用 file:/apt 当源
#   sh apt-acceptance.sh /apt --http   → 用 $APT_URI 当源（真 HTTP，能验出 URL 拼接）
set -e
REPO="${1:-/apt}"
MODE="${2:-file}"

if [ "$MODE" = "--http" ]; then
    # ⚠ 这一行是整套验收里最值钱的:apt 会拼 `URI + Filename`,
    #   Filename 一旦是绝对路径,这里就会请求到
    #   `$URI/src/dist/...` 而 404。file: 源测不出这个。
    URI="${APT_URI:?--http 模式需要 APT_URI}"
    echo "=== 源:$URI(真 HTTP) ==="
else
    URI="file:$REPO"
    echo "=== 源:$URI(本地) ==="
fi

echo '=== 0. 装前:镜像里没有 wchat ==='
if [ -e /usr/bin/wchat ]; then echo '✗ 装前就有,测不出东西'; exit 1; fi
echo '  ok 无 wchat'

echo '=== 1. 用户按 README 装公钥(apt 无法替用户建立信任锚) ==='
install -d -m 0755 /usr/share/keyrings
gpg --batch --yes --dearmor -o /usr/share/keyrings/wchat.gpg "$REPO/wchat.asc"
echo '  ok keyring 就位'

echo '=== 2. 配 source ==='
echo "deb [signed-by=/usr/share/keyrings/wchat.gpg] $URI stable main" \
    > /etc/apt/sources.list.d/wchat.list
cat /etc/apt/sources.list.d/wchat.list

echo '=== 3. apt update:必须验签通过 ==='
apt-get update > /tmp/upd.log 2>&1 || { cat /tmp/upd.log; exit 1; }
if grep -qi 'NO_PUBKEY\|not signed\|is not signed\|does not have a Release' /tmp/upd.log; then
    echo '✗ 验签/索引失败'; cat /tmp/upd.log; exit 1
fi
grep -E '^(Get|Hit)' /tmp/upd.log | head -4
echo '  ok 索引验签通过'

echo '=== 4. apt policy:仓库里的版本真的被看见 ==='
apt-cache policy wchat
apt-cache policy wchat | grep -q 'Candidate: ' || { echo '✗ 没有 Candidate'; exit 1; }

echo '=== 5. apt install wchat ==='
apt-get install -y wchat > /tmp/inst.log 2>&1 || { cat /tmp/inst.log; exit 1; }
grep -E '^(Setting up|Unpacking) wchat' /tmp/inst.log | tail -2

echo '=== 6. 装完能不能用(从 /tmp 跑,不依赖 cwd) ==='
cd /tmp
wchat --version
wchat -h > /tmp/h.txt 2>&1 && grep -q '使用方法' /tmp/h.txt && echo '  ok -h 正常'
/usr/lib/wchat/venv/bin/python -c 'import openai, httpx, prompt_toolkit, rich, PIL; print("  ok 依赖齐全")'

echo '=== 7. apt remove 清干净 ==='
apt-get remove -y wchat > /tmp/rm.log 2>&1 || { cat /tmp/rm.log; exit 1; }
if [ -e /usr/bin/wchat ]; then echo '✗ 可执行还在'; exit 1; fi
if [ -d /usr/lib/wchat/venv ]; then echo '✗ venv 还在'; exit 1; fi
echo '  ok 已清干净'

echo
echo 'APT-ACCEPTANCE-OK'


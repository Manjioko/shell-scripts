#!/bin/bash

# 测试 rich 渲染器对 markdown 的处理

# 创建测试内容
test_content='### 💭 思考
逻辑看起来是正确的。第73行已经检查了 `/etc/shells`，但问题可能是 `grep -q "$brew_bash" /etc/shells` 在 macOS上需要 sudo 权限才能读取 `/etc/shells`。

让我检查一下。

实际上，问题可能出在 else 分支 - 当 bash 已经安装时，`brew install bash` 返回成功，但脚本仍然进入安装成功的分支，尝试添加到 `/etc/shells`。让我检查一下逻辑。

从输出来看：
1. bash 已经安装（5.3.15）
2. `brew install bash` 返回成功（因为已安装）
3. 进入安装成功的分支
4. 检查 /etc/shells - 应该已经存在了
5. 但仍然要求密码

让我检查一下 `/etc/shells` 的内容。
----------------------------------------------'

echo "=== 测试1: 原始内容 ==="
echo "$test_content"
echo ""

echo "=== 测试2: 使用 render_markdown 渲染 ==="
# 模拟 render_markdown 函数
if [[ -n "$RENDER_HELPER" ]]; then
    printf '%s' "$test_content" | sed 's/\\$/\\\\/' | python3 "$RENDER_HELPER" 2>/dev/null
else
    echo "$test_content"
fi
echo ""

echo "=== 测试3: 检查最后一句话 ==="
last_line=$(echo "$test_content" | tail -1)
echo "最后一句话: '$last_line'"
echo ""

echo "=== 测试4: 测试 rich 对反引号的处理 ==='
echo "反引号会被渲染为代码块吗？"
echo "让我检查一下 `/etc/shells` 的内容。"
echo ""

echo "=== 测试5: 测试分隔线的处理 ==='
echo "分隔线 '----------------------------------------------' 会被渲染为什么？"
echo "----------------------------------------------"
echo ""

echo "=== 测试6: 测试下划线的情况 ==='
echo "如果最后一句话被解释为某种格式，可能会有下划线"
echo "让我检查一下 `/etc/shells` 的内容。" | cat -A

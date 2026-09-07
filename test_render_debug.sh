#!/bin/bash

# 测试 get_history 函数的渲染效果

# 模拟 render_markdown 函数（使用真实的 render_helper.py）
render_markdown() {
    local text="$1"
    if [[ -n "$RENDER_HELPER" ]]; then
        printf '%s' "$text" | python3 "$RENDER_HELPER" 2>/dev/null
    else
        echo "$text"
    fi
}

# 测试文件
TEST_FILE="test_history_debug.json"

# 读取消息
messages=$(jq '.messages' <<< "$(<"$TEST_FILE")")

echo "=== 测试1: 检查消息结构 ==="
echo "$messages" | jq '.[] | {role, content: (.content // "null"), reasoning_content: (.reasoning_content // "null")}'
echo ""

echo "=== 测试2: 模拟 get_history 循环 ==="
full_text=""
while IFS= read -r line; do
    role=$(jq -r '.role' <<< "$line")
    content=$(jq -r '.content // empty' <<< "$line")
    reasoning=$(jq -r '.reasoning_content // empty' <<< "$line")
    
    echo "处理消息: role=$role"
    
    # 构建该消息的文本
    msg_text=""
    
    # 思考内容
    if [[ -n "$reasoning" ]]; then
        echo "  -> 有思考内容"
        msg_text+="### 💭 思考"$'\n'
        msg_text+="$reasoning"$'\n'
        msg_text+="----------------------------------------------"$'\n'
    fi
    
    msg_text+="## $role"$'\n'
    msg_text+="----------------------------------------------"$'\n'
    
    if [[ "$role" == "tool" ]]; then
        echo "  -> 处理 tool 消息"
        echo "  -> 原始 content: '$content'"
        
        # 工具输出处理
        disp=${content//"\\n"/$'\n'}
        disp=${disp/#$'\xef\xbb\xbf'/}
        disp=$(printf '%s' "$disp" | grep -vE '^[[:space:]]*$')
        nlines=$(printf '%s' "$disp" | awk 'END{print NR}')
        shown=$(printf '%s' "$disp" | awk -v w=200 'NR<=5 { if (length($0)>w) print substr($0,1,w) "…(该行过长已截断)"; else print }')
        
        echo "  -> 处理后 disp: '$disp'"
        echo "  -> nlines: $nlines"
        echo "  -> shown: '$shown'"
        
        if (( nlines > 5 )); then
            msg_text+="$shown"$'\n'"…(其余 $((nlines - 5)) 行已省略)"$'\n'
        else
            msg_text+="$shown"$'\n'
        fi
    else
        # assistant 的 tool_calls 消息 content 为 null,不再渲染出字面 "null"
        [[ -n "$content" ]] && msg_text+="$content"$'\n'
    fi
    
    echo "  -> msg_text 长度: ${#msg_text}"
    echo ""
    
    full_text+="$msg_text"
done < <(jq -c '. [1:][]' <<< "$messages")

echo "=== 测试3: 完整渲染内容 ==="
render_markdown "$full_text"

echo ""
echo "=== 测试4: 检查思考内容的最后一句话 ==="
# 提取第一个思考内容
first_reasoning=$(echo "$messages" | jq -r '.[2].reasoning_content // empty')
echo "思考内容最后一句话:"
echo "$first_reasoning" | tail -1

echo ""
echo "=== 测试5: 检查分隔线 ==="
echo "分隔线 '----------------------------------------------' 在 markdown 中会被渲染为什么？"

# 清理
rm -f test_history_debug.json

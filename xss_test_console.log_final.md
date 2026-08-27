# DOMPurify XSS测试向量（console.log版本）

## 第1组 (1-5) - 基础事件处理器
```javascript
<script>console.log("%c1. 基础script标签注入", "color: red; font-size: 14px")</script>
<ScRiPt>console.log("%c2. 大小写混合script标签", "color: red; font-size: 14px")</ScRiPt>
< img src=x onerror=console.log("%c3. img标签onerror事件", "color: red; font-size: 14px")>
<svg onload=console.log("%c4. SVG标签onload事件", "color: red; font-size: 14px")>
<body onload=console.log("%c5. body标签onload事件", "color: red; font-size: 14px")>
```

## 第2组 (6-10) - 自动聚焦和媒体事件
```javascript
<input autofocus onfocus=console.log("%c6. autofocus/onfocus事件", "color: red; font-size: 14px")>
<details open ontoggle=console.log("%c7. details标签ontoggle事件", "color: red; font-size: 14px")>
<video autoplay onloadeddata=console.log("%c8. video标签onloadeddata事件", "color: red; font-size: 14px")>
<audio autoplay oncanplay=console.log("%c9. audio标签oncanplay事件", "color: red; font-size: 14px")>
">< img src=x onerror=console.log("%c10. 双引号逃逸后注入img", "color: red; font-size: 14px")>
```

## 第3组 (11-15) - 引号逃逸注入
```javascript
'>< img src=x onerror=console.log("%c11. 单引号逃逸后注入img", "color: red; font-size: 14px")>
"><svg onload=console.log("%c12. 双引号逃逸后注入svg", "color: red; font-size: 14px")>
'><svg onload=console.log("%c13. 单引号逃逸后注入svg", "color: red; font-size: 14px")>
" autofocus onfocus=console.log("%c14. 属性注入（双引号）", "color: red; font-size: 14px") x="
' autofocus onfocus=console.log("%c15. 属性注入（单引号）", "color: red; font-size: 14px") x='
```

## 第4组 (16-20) - 协议和链接注入
```javascript
<a href="javascript:console.log('%c16. 链接href中的空格', 'color: red; font-size: 14px')">test</a >
<a href='javascript:console.log("%c17. javascript协议（单引号）", "color: red; font-size: 14px")'>test</a >
javascript:console.log("%c18. 直接javascript协议", "color: red; font-size: 14px")
JaVaScRiPt:console.log("%c19. 大小写混合javascript协议", "color: red; font-size: 14px")
<svg><script>console.log("%c20. SVG内script标签", "color: red; font-size: 14px")</script></svg>
```

## 第5组 (21-25) - SVG和MathML注入
```javascript
<svg><a href="javascript:console.log('%c21. SVG内javascript链接', 'color: red; font-size: 14px')">x</a ></svg>
<math><mtext>< img src=x onerror=console.log("%c22. MathML中注入img", "color: red; font-size: 14px")></mtext></math>
<IMG SRC=x ONERROR=console.log("%c23. 大写IMG标签和属性", "color: red; font-size: 14px")>
<Svg OnLoAd=console.log("%c24. 大小写混合Svg标签和事件", "color: red; font-size: 14px")>
< img src=x onerror =console.log("%c25. onerror后有空格", "color: red; font-size: 14px")>
```

## 第6组 (26-30) - 属性格式和空格
```javascript
< img    src=x    onerror=console.log("%c26. 多个空格分隔属性", "color: red; font-size: 14px")>
<svg    onload =console.log("%c27. svg标签多个空格", "color: red; font-size: 14px")>
"><details open ontoggle=console.log("%c28. 双引号逃逸后注入details", "color: red; font-size: 14px")>
'><details open ontoggle=console.log("%c29. 单引号逃逸后注入details", "color: red; font-size: 14px")>
</title>< img src=x onerror=console.log("%c30. 关闭title标签后注入img", "color: red; font-size: 14px")>
```

## 第7组 (31-35) - 标签关闭和上下文逃逸
```javascript
</textarea>< img src=x onerror=console.log("%c31. 关闭textarea标签后注入img", "color: red; font-size: 14px")>
</style>< img src=x onerror=console.log("%c32. 关闭style标签后注入img", "color: red; font-size: 14px")>
< img src=x onerror=console.log("%c33. onerror中使用document.domain", "color: red; font-size: 14px")>
<svg onload=console.log("%c34. onload中使用document.domain", "color: red; font-size: 14px")>
">< img src=x onerror=console.log("%c35. 双引号逃逸后注入img（带domain）", "color: red; font-size: 14px")>
```

## 第8组 (36-40) - 域名检测和不同函数
```javascript
'><svg onload=console.log("%c36. 单引号逃逸后注入svg（带domain）", "color: red; font-size: 14px")>
< img src=x onerror=console.log("%c37. onerror中使用confirm", "color: red; font-size: 14px")>
<svg onload=console.log("%c38. onload中使用confirm", "color: red; font-size: 14px")>
< img src=x onerror=console.log("%c39. onerror中使用prompt", "color: red; font-size: 14px")>
<svg onload=console.log("%c40. onload中使用prompt", "color: red; font-size: 14px")>
```

## 第9组 (41-45) - 特殊标签和编码
```javascript
<iframe src="javascript:console.log('%c41. iframe javascript src', 'color: red; font-size: 14px')">
<object data="javascript:console.log('%c42. object javascript data', 'color: red; font-size: 14px')">
<form><button formaction="javascript:console.log('%c43. formaction javascript', 'color: red; font-size: 14px')">x</button></form>
<marquee onstart=console.log("%c44. marquee onstart事件", "color: red; font-size: 14px")>x</marquee>
<video><source onerror=console.log("%c45. video中source onerror", "color: red; font-size: 14px")></video>
```

## 第10组 (46-50) - 字符实体编码
```javascript
<a href="java&#115;cript:console.log('%c46. javascript:中字符实体编码', 'color: red; font-size: 14px')">x</a >
< img src=x onerror="console.log('%c47. onerror中字符实体编码', 'color: red; font-size: 14px')">
<svg onload="&#99;&#111;&#110;&#115;&#111;&#108;&#101;&#46;&#108;&#111;&#103;('%c48. onload中字符实体编码', 'color: red; font-size: 14px')">
%3Cimg%20src%3Dx%20onerror%3Dconsole.log(%22%25c49.%20URL编码img注入%22%2C%20%22color%3A%20red%3B%20font-size%3A%2014px%22)%3E
%3Csvg%20onload%3Dconsole.log(%22%25c50.%20URL编码svg注入%22%2C%20%22color%3A%20red%3B%20font-size%3A%2014px%22)%3E
```

## 测试说明
1. 每个测试向量都会在控制台输出带颜色的中文描述，便于识别
2. 使用`%c`格式化输出，便于在浏览器控制台中识别
3. 如果DOMPurify成功过滤，这些向量将不会执行
4. 如果执行成功，会在控制台看到对应的测试描述
5. 建议在测试页面中创建一个容器元素，将这些向量插入到容器中进行测试

## 使用示例
```javascript
// 在HTML页面中测试
const testContainer = document.getElementById('test-container');

// 测试每个向量
const testVectors = [
    // 从上面复制测试向量
];

testVectors.forEach((vector, index) => {
    console.log(`测试 #${index + 1}:`);
    console.log(vector);
    
    // 使用DOMPurify清理
    const cleanHTML = DOMPurify.sanitize(vector);
    
    // 插入到容器
    testContainer.innerHTML = cleanHTML;
    
    // 检查是否执行（如果执行了，控制台会有输出）
    console.log('清理后:', cleanHTML);
    console.log('---');
});
```

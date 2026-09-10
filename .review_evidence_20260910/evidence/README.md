# 独立复现实验

这些不是 JunjiePR 原仓全量测试。材料测试使用当前函数主体、最小模型与受控转换/时间/提交故障；浏览器测试使用当前函数主体与合成 API/样式。请先阅读报告中的限制。

运行：
```bash
python test_material_isolated.py
python test_review_browser.py
python test_navigation_browser.py
```

依赖为 SQLAlchemy、Playwright 及 /usr/bin/chromium。本轮环境版本记录在 JSON。所有数据库和材料在 TemporaryDirectory；不连接任何生产环境。

两张 PNG 已目视检查，均标注“局部函数复现实验/非完整应用截图”，不能作为正式应用视觉验收。

本次 localhost HTTP 浏览器导航受策略拦截；导航实验使用不发网络请求的受控 Response 流，不应描述为 HTTP 端到端测试。

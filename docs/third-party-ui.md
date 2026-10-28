# 第三方界面组件

依赖固定版本并保存在 `backend/app/static/vendor/`，按需要加载，不通过外部 CDN 运行。

| 组件 | 版本 | 来源 | 许可文件 |
|---|---|---|---|
| Tabulator | 6.3.1 | [上游仓库](https://github.com/olifolkerd/tabulator/tree/6.3.1) / npm `tabulator-tables@6.3.1` | [MIT](../backend/app/static/vendor/tabulator-6.3.1/LICENSE) |
| Apache ECharts 定向构建 | 6.0.0 | [上游仓库](https://github.com/apache/echarts/tree/6.0.0) / npm `echarts@6.0.0` | [Apache-2.0 及所附说明](../backend/app/static/vendor/echarts-6.0.0/LICENSE)、[NOTICE](../backend/app/static/vendor/echarts-6.0.0/NOTICE) |

Tabulator 来源为 npm 包在 jsDelivr 上的固定版本分发文件。ECharts 来自 npm 固定版本源码包，以 esbuild 0.25.0 定向构建；入口为 [echarts-workbench.js](../scripts/vendor/echarts-workbench.js)，依赖 echarts 6.0.0、zrender 6.0.0、tslib 2.3.0。

在可解析上述固定版本依赖和 esbuild 的开发环境运行以下命令（仅用于重新生成第三方静态包，不是服务启动步骤）：

```text
esbuild scripts/vendor/echarts-workbench.js --bundle --minify --format=iife --global-name=echarts --target=es2020 --outfile=backend/app/static/vendor/echarts-6.0.0/echarts.workbench.min.js --legal-comments=external
```

本地保留许可、NOTICE、构建提取的 LEGAL.txt，以及 zrender/tslib 许可。业务自定义样式位于 `workbench.css`。Tabler 只用于设计参考，未复制其运行时代码或添加 Bootstrap。

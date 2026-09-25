# 声明登记统计

「声明登记统计」按月查询声明扣分记录，可选择景点圈、处分等级、处分类型、业务状态，并按员工姓名或工号搜索。页面显示总记录数、涉及人数、各景点圈及等级汇总和逐条明细；明细含原景点圈、登记人、材料与升级状态。已批准升级的第二条原声明与升级结果不重复计数。

## 权限与范围

`DECLARATION_STATS_VIEW` 允许查看所有景点圈及可用的声明材料；`DECLARATION_STATS_EXPORT` 还允许导出。TA主管、主管、TA GSM、GSM、AM、OM、HR管理员、景点圈HR和系统管理员均同时具备这两项权限，因此**主管可以跨圈查看和导出**。该统计是跨圈只读入口，不改变原登记、审核或HR管理范围。

| 操作 | 接口 | 要求 |
|---|---|---|
| 查询 | `GET /api/declaration-statistics` | `DECLARATION_STATS_VIEW` |
| 导出 | `GET /api/declaration-statistics/export` | `DECLARATION_STATS_VIEW` 与 `DECLARATION_STATS_EXPORT` |
| 查看材料 | 明细给出的 `/api/files/{id}` | 已登录且具备声明统计查看权限；文件仍须为有效材料 |

查询和导出使用相同筛选口径。导出工作簿含「景点圈与等级汇总」和「逐条明细」，加当前导出人工号水印并记录导出审计。导出表只提示材料是否可在系统内查看，不附带材料文件本身。

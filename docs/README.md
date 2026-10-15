# 文档索引

按问题进入对应文档；无需从头通读，也不要把历史切片末尾的下一建议当作当前排期。

| 问题 | 主入口 |
|---|---|
| 怎样运行、用户能完成什么 | [仓库 README](../README.md)、[中文准备单](WARRANTY_PREPARATION.md) |
| 当前已实现什么、有什么缺口 | [当前状态](STATUS.md) |
| 下一步为何做、何时停止 | [开发路线](DEVELOPMENT_ROADMAP.md) |
| 改哪个模块、规则归谁 | [项目地图](PROJECT_MAP.md) |
| 领域规则、持久化与 HTTP 合同 | [架构](ARCHITECTURE.md)、[数据模型](DATA_MODEL.md)、[API](API_CONTRACT.md) |
| 分析流程与信任边界 | [Agent 设计](AGENT_DESIGN.md)、[安全与隐私](SECURITY_AND_PRIVACY.md) |
| 某个闭环有哪些验证、哪些没证明 | [验收索引](evaluation/README.md)、[测试与评估](TESTING_AND_EVALUATION.md) |
| 本次整体整理与交付证据 | [2026-10-15 公开同步](evaluation/public-sync-20261015.md)、[源实现交付](evaluation/source-delivery-20261009.md)；更早记录见[本地交付整理](evaluation/local-delivery-20260924.md) |
| 产品需求和长期范围 | [产品需求](PRODUCT_REQUIREMENTS.md)；目标描述不等于已交付能力 |
| Windows 安装、升级或发布 | [安装器](WINDOWS_INSTALLER.md)、[发布清单](RELEASE_CHECKLIST.md)、[CHANGELOG](../CHANGELOG.md) |
| 当时的模型结果与架构决策 | [Portfolio Demo](PORTFOLIO_DEMO.md)、[ADR](adr/0001-local-first.md)、[历史目录](history/README.md) |

## 维护规则

- STATUS 只保留当前实现、开放问题与证据边界；路线只保留排序、退出条件与条件触发项；地图只标注真实代码归属。
- API/数据合同随语义修改同步；HTTP 以 FastAPI/OpenAPI 实现核对，Web 类型目前手写。
- 每个切片的详细证据只保存一份，其余位置链接。合成替身、响应回放、真实本地服务、真人、真实模型、CI 和安装资产分别陈述。
- 带日期的验收保留当时环境、失败与限制；新结果通过索引覆盖当前结论，不把旧报告改写成全通过。
- Git 推送不等于安装器发布；历史 tag、CI 与资产不证明新工作区。提交前检查实际范围，发布另行授权。
- 本机协调文件与私人数据不进入源码提交。新窗口先从当前状态和地图定位范围，再按需阅读相应证据。

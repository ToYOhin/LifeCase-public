# 项目地图

按用户路径定位代码，不按历史里程碑逐篇加载。能力状态见 [STATUS](STATUS.md)，下一步见 [开发路线](DEVELOPMENT_ROADMAP.md)，文档职责见 [文档索引](README.md)。

## 实际目录与职责

```text
apps/api/src/lifecase/
  api/              HTTP 输入/输出和安全错误映射
  cases/            案件、人工状态决定、工作台概览、独立保存的报修诉求
  claims/           事实权威、确认/修正、待复核与当前事实投影
  templates/        四类固定模板、缺项与结束条件
  evidence/         原件导入、文本抽取、证据上下文、证据包
  retrieval/        Case-scoped SQLite FTS5 可重建索引
  deadlines/        日期依据、规则、状态与时区
  actions/          分析绑定草案、待办/依赖/版本、人工进展、完成证据
  approvals/        精确 payload/version 审批
  calendar_export/  本地 ICS 下载
  timeline/         权威记录时间线
  storage/          SQLite、迁移、对象存储、备份恢复
  agent/            当前问题单阶段分析、两/七阶段兼容、校验、预算、恢复
  providers/        固定模型适配、配置、凭据存储边界
  tools/            受控本地只读工具
  evaluation/       固定合成评估和模型兼容性实验

apps/web/src/
  App.tsx           案件选择、状态提示、五区导航和功能组合
  features/
    intake/         建案与主动导入
    workbench/      案件列表、导航和状态概览
    review/         候选登记、复核决定、已核实事实回看/修正、原文高亮
    warrantyPreparation/ 当前事实准备单、待补字段定位、诉求保存、复制/TXT
    agentDemo/      当前问题/引用/历史与 Live 运行；兼容命名，含独立 Fake 演示
    aiCapabilities/ 全局 AI 设置和无密钥能力投影
    templates/      固定模板选择
    deadlines/     期限与 ICS
    actions/       草案保存、待办分组/复制/来源分析、人工记录、完成与审批
    timeline/      案件及领域权威记录的只读时间线
    evidencePack/  证据包
    backupRestore/ 备份与恢复
  components/       真正共用的展示组件

packages/
  case-templates/   固定 YAML 模板
  contracts/       早期版本化 JSON Schema；以实际验证器使用判定权威
fixtures/           合成材料和固定评估记录
scripts/            PowerShell 开发、daily-use、门禁、安装器与评估入口
docs/               当前导航/合同；evaluation/ 证据；history/ 历史计划
```

`security/` 当前仅为包占位，不是集中安全实现；安全校验跟随实际 I/O/领域边界。不要为匹配目录图而新造框架。

## 从使用路径定位实现与测试

下表后端路径相对 `apps/api/src/lifecase/`；测试相对 `apps/api/tests/`。Web 位于上述 feature 目录，测试与组件同目录。

| 用户动作/边界 | 主要实现 | 定向测试 |
|---|---|---|
| 建案、切换、人工决定是否解决 | `cases/service.py`、`api/cases.py`、Web `App.tsx`、`workbench/CaseDocket.tsx`、`intake/api.ts` | `integration/test_case_api.py`、`integration/test_case_state_machine.py`、Web `App.test.ts` 与状态恢复测试；并发导航证据见[多案件核对](evaluation/multi-case-open-20260924.md) |
| 导入与文本抽取 | `evidence/`、`api/sources.py`、`storage/object_store.py` | `integration/test_source_api.py`、`integration/test_text_extraction.py` |
| 手工登记/确认/修正事实 | `claims/manual_service.py`、`claims/service.py`、`api/claims.py` | `integration/test_manual_claim_intake.py`、`integration/test_claim_provenance.py` |
| 待复核队列 | `claims/review_service.py`、`api/reviews.py` | `integration/test_evidence_review.py` |
| 全量当前事实及来源 | `claims/current_facts.py`、`api/current_facts.py` | `integration/test_current_facts.py` |
| 中文准备单、复制/TXT 与来源定位 | Web `warrantyPreparation/`、`review/quoteOffsets.ts` | 同目录准备单/复制/下载/引用偏移测试 |
| 准备单待补字段到登记/复核 | `App.tsx`、`preparation.ts`、`EvidenceReviewPanel.tsx`、`CurrentFactsPanel.tsx`；模板目录暴露既有 `conditional_claims` | `preparation.test.ts`、`integration/test_warranty_serial_intake.py`；真实刷新/导航回归见[待补项闭环记录](evaluation/warranty-preparation-gaps-20260919.md) |
| 本人诉求保存、跨刷新/重启恢复 | `cases/preparation.py`、`api/warranty_preparation.py`、Web `warrantyPreparation/` | `integration/test_preparation_intent.py`、Web `intentApi.test.ts` 与组件 SSR；真实冲突/恢复交互见[验证记录](evaluation/warranty-preparation-save-20260919.md)中的外部浏览器脚本 |
| 检索和双语查询 | `retrieval/service.py`、`agent/demo.py`、`evidence/context/service.py` | `integration/test_retrieval.py`、`integration/test_live_agent_api.py`、`unit/test_agent_retrieval_query.py` |
| 本次问题、当前事实引用、依据变化后重新分析 | `agent/live.py`、`agent/question_binding.py`、`agent/question_context.py`、`agent/analysis_facts.py`、`agent/rag.py`、Web `agentDemo/LiveAgentPanel.tsx` | `integration/test_live_agent_question.py`、`integration/test_question_binding.py`、`integration/test_question_rag.py`、Web Live/引用测试 |
| 两/七阶段兼容、旧历史与恢复 | `agent/demo.py`、`agent/models.py`、`agent/service.py`、`api/live_agent_runs.py` | `integration/test_stage_runner.py`、`integration/test_live_agent_api.py` |
| 当前分析到可审阅报修草案、明确保存待办 | `actions/analysis_draft.py`、`api/analysis_actions.py`、Web `actions/AnalysisActionDraft.tsx` | `integration/test_analysis_action_draft.py`、Web `analysisActionDraft*.test.ts` |
| 人工进展/结果、核对完成证据 | `actions/progress.py`、`actions/service.py`、`api/action_progress.py`、Web `actions/ActionProgress.tsx` | `integration/test_action_progress.py`、Web `actionProgress*.test.ts` |
| 待办复制、原分析定位、依据变化后的人工处置 | Web `actions/SavedActionCopy.tsx`、`SavedActionAnalysis.tsx`、`ActionFollowUp.tsx` 及对应 state 文件；复用 progress、Live history、Action 命令 | 对应 `savedAction*.test.ts`、`actionFollowUpState.test.ts`、`actionCancellationState.test.ts` |
| 期限/行动/审批 | `deadlines/`、`actions/`、`approvals/` | `integration/test_deadline_engine.py`、`integration/test_action_planner.py`、`integration/test_approval_gate.py` |
| 证据包/备份/时间线 | `evidence/`、`storage/`、`timeline/` | `integration/test_evidence_pack.py`、`integration/test_backup_restore.py`、`integration/test_timeline.py` |
| AI 设置与固定 Provider | `providers/runtime_settings.py`、`api/system.py`、`api/dependencies.py` | Provider/settings 定向测试；使用进程内合成替身 |

路径表是定位入口，不是要求每次全部运行。以仓库实际文件和受影响行为选择门禁。

当前 Web 提交独立的本次问题，创建 `portfolio-live-q1-` Run，仅执行 `validate`；原始问题、检索表达式、当前事实与材料依据快照保存在既有 Run 的 migration 18 字段中，不编码进 ID，也不改写 Case goal。S1 的 `portfolio-live-v2-` 兼容调用继续选择 `extract → validate`；更早 Live Run、显式 Fake Demo 与历史评估保持原七阶段。顺序选择在 `agent/models.py`，各合同在 `agent/rag.py`；API 的 `stage_order` 是展示来源。详见[问题分析记录](evaluation/case-question-analysis-20260919.md)与 [Agent 设计](AGENT_DESIGN.md)。

`agent/demo.py` 的 `PortfolioAgentDemoService` 也被 Live 复用，它不是纯演示文件；`features/agentDemo/` 同样包含生产问题分析 UI。调整行为应沿实际调用链定位，不能按旧命名整体删除或先强制拆分。独立 Fake 路径保持显式开关和标签，不作为模型质量证据。

## 两条互不混淆的事实读取路径

- `evidence-review`：待复核 candidate/inferred 队列，可能附带同字段已确认值；全部确认后可为空。
- `current-facts`：当前 Case 的全部当前 Claim，带确认依据、来源可用性、冲突、可用标记和服务端当前值 hash；四类事实回看及普通行动版本绑定复用此只读投影，不创建第二事实库。

保修准备单、四类案件的 `review/CurrentFactsPanel.tsx` 和本地行动登记消费第二条路径，不能从 Agent 摘要、历史沟通草稿或待复核队列猜出当前权威。`warrantyPreparation/api.ts` 的通用读取校验案件类型、来源和引用；准备单包装仍限制保修。已核实事实修正复用 `review/api.ts` 的版本化命令；`correctionInput.ts` 只负责明确字段的普通输入，不另建事实权威。`actions/LocalActionComposer.tsx` 提供四种已有行动合同的局部表单，普通沟通/跟进/结果行动需绑定全部可用必需事实。

本人诉求通过 `GET/PUT /api/v1/cases/{case_id}/preparation-intent` 显式保存到 migration 17
的 `warranty_preparation_intent` 专用表，与上述事实读取分开。仅保存本人原文，不复制事实、
准备单全文或案件目标；空态读取版本为 0，保存和清空都保留递增的独立版本，409 不静默覆盖。
刷新或重启后重新读取已保存诉求；输入未保存时不能宣称已持久化。保存不改变 Case 的生命周期
版本、Claim/Action/审批和时间线。迁移与备份恢复继续使用 `storage/migrations.py`、
`storage/backup/`，旧库/旧备份支持前向升级，新 schema 不支持旧程序直接读取或降级恢复。

## 权威与依赖方向

```text
用户明确输入 → Web feature → FastAPI → 领域服务 → SQLite/原件
可选模型提议 → Schema/来源/业务校验 → 候选事实（仍需人工确认）
当前权威 → 只读投影 → 准备单/时间线/本地导出
本人诉求 → 显式保存及独立版本 → 准备单中的本人诉求段落
```

- ClaimService 拥有事实写入；状态机、审批和完成依据由各领域服务维护。
- `PreparationIntentService` 只拥有保存的本人诉求，不复用 Case 生命周期版本，不构成事实确认或审批。
- `AnalysisActionDraftService` 校验当前完成的 q1 分析和版本/hash，复用 `ActionService` 保存待办；不增加模型步骤，不自动审批。
- `ActionProgressService` 追加人工进展/结果，保留 actor/reason、当时 Action 版本与并发游标；migration 19 的记录不改变事实、审批、待办状态或案件状态。完成待办与解决案件是两个独立人工决定。
- 索引可重建，不能取代 Source/Segment；引用必须校验同 Case、范围与哈希。
- Web 类型目前按 feature 手写；FastAPI 生成 OpenAPI。未建立自动生成 TypeScript 合同的流水线。
- `packages/contracts/` 的两份早期 Schema 当前没有自动验证器引用，不能当作已执行的合同门禁。运行时 Agent/模板/工具使用各自显式声明的 Schema；HTTP authority 仍为 FastAPI/OpenAPI。
- 路径包含性在存储/导入/导出边界，HTTP 错误脱敏在 `api/errors.py`，审批绑定在 `approvals/`，凭据边界在 `providers/runtime_settings.py`。
- 不把模型对象放进领域实体，不把 UI 状态当持久化权威。

## 启动与交付

- 开发：`scripts/dev-api.ps1` + `scripts/dev-web.ps1`。
- 日常源码：`scripts/lifecase.ps1` → `daily_use.py`，单 loopback API/Web 服务。
- 控制文件：`daily_use.py` 的 Windows 运行目录互斥仅覆盖短文件事务；现有启动锁继续识别启动者，退出清理核对 PID 与令牌。见[占用回归证据](evaluation/daily-use-control-contention-20260919.md)。
- 安装版：`windows_app.py` / `windows_installer.py` 及相关构建脚本；程序文件与用户数据分离，卸载保留数据。
- 合成模型演示：`deepseek_live.py` / `demo_readiness.py` 是另行授权的历史演示入口，不是默认产品启动或本轮无模型验收入口。
- 实际打包文件名、构建及安装验收见 [Windows 安装器](WINDOWS_INSTALLER.md)。

## 改动时如何读文档

修改入口遵循现有边界：用户导航和迟到响应在 `App.tsx`/现有 feature 状态逻辑；事实判断在 `claims/`；问题与依据绑定在 `agent/`；保存、授权和完成规则在 `actions/`/`approvals/`；HTTP 只做合同映射。需要修改持久化时再核对迁移与备份，不为整理目录搬动领域代码，也不新设通用工作流层。

使用路径查 [准备单指南](WARRANTY_PREPARATION.md)；业务规则查 [产品需求](PRODUCT_REQUIREMENTS.md)、[数据模型](DATA_MODEL.md)；共享边界查 [架构](ARCHITECTURE.md)、[API 合同](API_CONTRACT.md)、[安全与隐私](SECURITY_AND_PRIVACY.md)。验证查 [测试与评估](TESTING_AND_EVALUATION.md)，发布查 [发布清单](RELEASE_CHECKLIST.md)。

`history/` 只解释历史决策，不覆盖当前实现、最新任务或授权范围。

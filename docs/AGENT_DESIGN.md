# Agent 设计

LifeCase 的当前 Agent 围绕一个案件回答本次问题，提供可核对的事实与材料引用。用户随后可以审阅报修草案、明确保存待办、记录处理结果，并独立决定案件是否解决。模型输出始终是建议，不能确认事实、审批、完成行动或解决案件。

手工登记/修正事实和[中文准备单](WARRANTY_PREPARATION.md)不依赖模型。当前交付与证据缺口见 [STATUS](STATUS.md)，改动位置见 [PROJECT_MAP](PROJECT_MAP.md)，演进顺序见[开发路线](DEVELOPMENT_ROADMAP.md)。本文描述已存在的执行合同，不把早期职责设想当作待实现阶段。

## 当前用户路径

```text
主动导入材料 → 人工核实/修正事实
                       ↓
独立本次问题 → 核对输入依据 → 显式授权分析 → 回答与引用
                       ↓                    ↓
                依据变化时重新分析       审阅/编辑报修草案
                                            ↓
                                      明确保存案件待办
                                            ↓
                            人工处理记录 → 核对证据后完成待办
                                            ↓
                                    人工决定案件是否解决
```

分析后的草案、待办、处理记录与状态变更复用确定性服务，不再调用五个占位模型步骤。线上 Web 产品方向不代表已具备公网部署、多用户、联网检索、连接器或外部执行。

## 三种有限 Run 合同

| 路径 | 持久化识别 | 实际阶段 | 用途 |
|---|---|---|---|
| 当前 Web 本次问题 | `portfolio-live-q1-<uuid>`，完整 `input_binding_json`/hash | `validate` | 当前事实与材料的引用分析，不重新提取候选事实 |
| S1 兼容调用 | `portfolio-live-v2-` | `extract → validate` | 提取候选事实，再检索回答；保留已有调用与恢复 |
| 更早 Live、显式 Fake Demo、历史评估 | 原有 ID/原合同 | `extract → normalize → link_claims → classify_case → check_gaps → propose_actions → validate` | 旧记录读取/恢复与专门演示，不作为当前能力宣称 |

`agent/models.py:stage_order_for_run()` 选择顺序；API 的 `stage_order` 驱动 UI。旧 Run 不重写、不伪造成功 checkpoint，也不静默换成新顺序。S1 删除的新生产调用不会通过重新实现五套占位功能补回。正常 q1 是一次模型适配器调用，S1 是两次；修复和明确重试另计，预算上限不是每次必须调用的次数。

`agent/demo.py` 中的 `PortfolioAgentDemoService` 同时服务 Live 的编排/读取；`features/agentDemo/` 也承载当前生产 UI。命名包含 Demo 不表示可以整体删除。只有显式 Fake 路径构建服务器自有合成输出，且必须有独立开关与清晰标签。Ollama 保留为单独的本地评估边界，没有当前产品 HTTP/Web 执行入口。

## 本次问题与当前依据

1. `LiveAgentPanel` 提交原始自然语言问题；它与用于检索的表达式分开，不改写 Case goal。
2. `LiveAgentService.preview()` 读取同案当前事实与材料，构造有界检索上下文、固定 Provider/model、预算和只读说明，返回可核对的授权 hash。
3. 用户确认该预览后创建排队 Run；创建不调用模型。Migration 18 在既有 Run 保存问题、检索表达式、事实/材料依据及其 hash、授权 hash，不把问题塞入 Run ID。
4. 执行重新核对同案绑定、当前依据及授权，使用 `validate.question.v1`。问题态 execute/retry/cancel/recover 必须指定 UI 展示的 `expected_run_id`，不能被另一个窗口的最新 Run 替换。
5. `agent/analysis_facts.py` 复用 current-facts：值、Claim 版本、确认依据、actor/reason、冲突、可用性和来源引用保留；不是从历史模型答案重建事实。
6. `agent/rag.py` 分别验证事实引用和原文引用。当前已核实/人工修正值优先，旧原文不能覆盖该值；原文仍作为可回看的历史材料。回答为经验证的 `answered` 或明确 `unknown`。
7. 材料、事实或来源可用性变化后，历史返回 `changed`/`unavailable`，保留旧问题和答案。重新分析需要新预览和明确创建新 Run，不改写旧结果。

Run 绑定保存的是有界、明确的输入快照与引用，包含问题和事实值，不是完整材料副本、通用聊天记录或第二事实库。实际合同和迁移见 [API 合同](API_CONTRACT.md#current-case-question-contract-2026-09-19)及[数据模型](DATA_MODEL.md)。

## 模型与确定性服务的职责

| 工作 | 当前执行者 |
|---|---|
| S1 提取已提供材料中的候选字段 | `extract.warranty.v1` 模型输出，经 Schema/来源验证后仅为 candidate/inferred |
| 回答本次问题、组织引用说明 | `validate.question.v1` 模型输出，经事实/引用验证后形成 Run 结果 |
| 文件哈希、原件、Segment、Case 隔离、检索范围 | evidence/storage/retrieval 服务 |
| 事实确认/修正、冲突、模板缺项、日期规则 | claims/templates/deadlines 服务 |
| 准备单、草案初稿、待办依赖与状态 | 现有准备单投影及 Action 领域服务 |
| 授权、预算、超时、有限修复、取消与恢复 | StageRunner、Provider 适配器和各领域服务 |
| 审批、完成证据与案件解决 | 明确人工命令和 Action/Approval/Case 服务 |

当前模型不会分类案件、自动总结权威 Timeline、自动补齐缺失值、创建期限、排列并执行外部行动。案件类型来自显式建案选择，Timeline 来自已提交领域记录。没有大小模型自动路由、通用语义缓存或个性化策略学习；这些不作为当前框架合同。

## 提取与引用权威

兼容提取合同对六个保修字段逐一给出 `extracted`、`inferred` 或 `unknown`。直接提取只创建 `candidate`，推断只创建 `inferred`；缺失不产生占位事实，置信度不代替确认。创建事件绑定原 Run，历史投影不混入其他运行的候选项。

ClaimSource 和答案引用都必须校验同 Case 的 Source/Segment、顺序、页码、完整文本 hash、引用偏移及引用 hash。只在完整同案 Segment 权威已匹配时，现有规范化可把错误的派生偏移/hash 还原为完整 Segment 引用；错误 Source/Segment/hash 不能宽松接受。

提取提交在同一个 SQLite 事务内写入候选 Claim、来源、历史、冲突、用量与 checkpoint。validate 在提交时再次校验引用和当前事实；它不确认或修正 Claim，也不写 Case/Action/Approval。

## 从有效分析到人工行动

`AnalysisActionDraftService` 只接受同案、最新、已完成且 `answered` 的 q1 分析。预览是确定性的，使用现有回答/事实提供可编辑报修文本，不增加模型步骤。明确保存时核对 Run ID/version、basis/answer hash、actor/reason，以及已有 Action ID/version，委托 `ActionService` 原子创建/修订 `prepare_communication` 待办。

保存不等于审批或外部执行。依据变化、读取失败或冲突时保留用户编辑，阻止旧草案继续保存。已保存待办正文复制前重新核对同案版本、payload 和当前依据；这与剪贴板不是一个数据库事务，不能宣称复制期间永远不存在后续并发变更。

来源分析入口按已保存 `analysis_run_id` 读取原 Run 的问题、回答与引用。它不以最新分析替代原分析，不自动重新分析；历史依据变化与无法读取分别提示。重新分析后人工另存新待办，用户再独立决定是否取消旧待办，审批不自动继承。

`ActionProgressService` 追加 `progress`/`result` 人工记录，带 actor/reason、当时 Action 版本和上次记录游标；migration 19 保存其历史。记录结果不确认事实、不审批、不完成待办。待办完成需要独立核对完成证据和版本；案件解决又是单独的 Case 命令。Timeline 分开呈现这三类已提交记录，不将它们合并成一次自动成功。

## Provider 与工具边界

一个 provider-neutral `generate_structured(request)` 协议接收版本化指令、有限 JSON 输入、Schema 和输出预算，返回已解析对象及安全用量元数据。原始 HTTP/SDK 对象留在适配器内，本地业务验证始终决定是否接受结果。

默认 Provider 为 disabled。当前全局设置只允许已审核云 Provider 的固定模型/端点，Windows Key 使用系统凭据存储；保存配置不创建 Run。测试必须在构建应用前替换凭据恢复并阻断模型客户端，不依靠“尚未点击运行”避免恢复真实凭据。适配器细节与持久化说明见 [ARCHITECTURE](ARCHITECTURE.md#model-provider-boundary)、[安全与隐私](SECURITY_AND_PRIVACY.md)。

当前 validate 仅使用一次本地只读 `evidence_context.search.v1`，继承 Case SQL 隔离、有限条数/字节、哈希验证和 SQLite 截止时间。FTS5 可重建索引不成为权威，模型没有 shell、任意文件系统、任意 URL 或工具注册权限。

| 行为 | 当前边界 |
|---|---|
| 同案材料检索/引用 | Risk A，本地只读受控工具 |
| 候选事实/审阅后保存草案 | 现有本地领域写入规则；候选事实不自动确认 |
| ICS、准备单 TXT、证据包 | 用户显式下载/导出，不写系统日历或发送外部消息 |
| 发信、上传、提交表单 | 没有外部执行器；Risk C 审批记录不启用执行 |
| 支付、签约、删除原件 | Risk D 不提供 |

## 执行、预算与恢复

每阶段固定输入/输出/Checkpoint Schema、允许工具、超时和终止条件。模型调用前用乐观版本和 SQLite 事务预留调用、Token 和 CNY 预算；调用在事务外完成。缺少 Provider 金额时计入预留额，不能以未知费用扩大预算。Schema/业务校验至多一次修复，错误原始候选不落库。

Run 状态只有 `queued`、`running`、`checkpointed`、`needs_review`、`cancelled`、`completed`。失败原因和可重试性用安全错误码表示；没有 Agent `needs_approval` 状态。Action 审批是独立领域合同。

Checkpoint 使用规范 JSON、hash 与大小/深度约束，不保存提示词、完整证据体、凭据或原始模型响应。失败、预算耗尽、超时和取消保留既有 checkpoint。进程中断的预留额先由明确恢复命令保守记账，再按原 Run 的重试资格和预算决定后续；恢复不自动重做模型调用，也不更换问题或阶段顺序。

## 验证与演进

确定性替身验证调用次数、Schema/引用/事实权威、原子提交、取消恢复及 UI 冲突路径；它不能证明真实模型质量。历史 DeepSeek 合成演示、失败的 Ollama 评估、当前 q1 自动化和独立真人使用是不同证据，不能互相替代。真实 HTTP 服务/进程重启与进程内 API/浏览器响应回放也分别标注。

现有关键入口是 `test_live_agent_question.py`、`test_question_binding.py`、`test_question_rag.py`、`test_stage_runner.py`、`test_analysis_action_draft.py`、`test_action_progress.py` 及相关 Web feature 测试。只按当前改动和实际风险选用验证，构建/测试串行运行。

后续先解决有复现或明确源码依据的用户闭环缺口，再通过一条完整任务检验价值。不要以阶段数、Provider 数、拆文件数或新增框架衡量 Agent 进展；不把兼容代码或单项工具拒绝当成必须重建架构的理由。

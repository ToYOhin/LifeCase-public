# S1：生产 Agent 编排减法验收

日期：2026-09-18。对象为本地未提交源码；不是旧安装包、远端 CI 或真实模型质量验收。

## 实际变化

- 新建 Live Run 仅执行 `extract → validate`：候选事实提取、现有证据检索/回答。正常合成运行从七次模型适配器调用减为两次，修复/重试单独计数。
- 新运行不再执行 `normalize/link_claims/classify_case/check_gaps/propose_actions`，不生成这些步骤的占位 checkpoint。生产 Provider 路径也不再预先构造 Demo 的固定输出/Fake script。
- `live_agent_read_only.v2` 新建 `portfolio-live-v2-<preview-sha256>` ID；原 `portfolio-live-<v1-preview-sha256>` 保留。该已有持久化 ID 可在 queued、尚无 stage 时识别新旧流程，无需新增数据库字段、表或迁移。
- 新 validate 输入为 `validate.rag.input.v2`，精确包含一个前序 checkpoint hash；旧输入 v1 保持六个。输出和 checkpoint 语义未变，保留既有版本。
- `AgentRunResponse` 和每条 `run_history` 返回实际 `stage_order`。页面显示两阶段/两阶段完成数，旧记录明确标识“旧版七阶段”，按各自序列继续或恢复。
- 当前 Live readiness 消费者同步验证精确两阶段及顺序，报告版本为 `p1-06-demo-readiness.v2`；不能把旧七阶段结果或缺失/错序步骤判为当前新流程通过。

实际调用链仍为 Live API → Live service → 现有阶段执行服务/StageRunner → Provider 适配器，没有增加编排层。沿该链核对的输入与消费者如下：

| 阶段 | 输入、产物与消费者 |
|---|---|
| extract | 当前 Case 的有效证据进入提取合同；验证后的候选事实与 checkpoint 同事务提交，供事实复核与现有 Claim 流程读取 |
| validate | 当前 Case 的问题、证据检索工具结果和前序 checkpoint hash 进入回答合同；回答/引用供 API 与 Web 展示，并经原有来源权威检查 |
| 被移除的五阶段 | 原输入只有 Case、前序 hash 与通用 Demo/read_only 元数据；产物是通用 checkpoint，后续只串接其 hash，没有对应领域业务产物的消费链 |

因此删除的是五次占位调用及其进度/能力宣称。事实确认、缺件判断、行动/审批和中文准备单继续复用现有领域服务；没有让模型重做这些确定性职责，也没有为五个名称补功能。

## 为兼容保留

旧七阶段定义、输入合同、SQL 历史排序、旧授权摘要和 Demo 入口保留。旧 queued、已完成提取、interrupted 与 completed Run 均按原语义处理，不重写历史、不静默升级。Fake Demo 和历史评估继续使用原七阶段；没有新增通用工作流、版本注册框架、依赖或 Provider。

事实与来源权威、Case 隔离、候选事实/checkpoint 同事务、操作人/理由/版本保护、审批、预算上限、超时、有限修复、取消和恢复机制继续使用原实现。U1/U2 产品代码没有重做。正常两次调用不等于把预算上限改成两次；合法修复与重试仍受已有硬预算限制。

## 先回归、后实现

1. 初始 API 回归捕获原流程仍执行五个额外步骤；Web 回归捕获仍显示 `0/7`、`2/7` 和缺少新旧历史标识。
2. 独立初审发现底层 StageRunner 可被错误 registry 写入不匹配的输入版本。新回归先证明错误调用未被拒绝，再增加预留前的 Run/registry 序列核对；拒绝后零调用、零预算/版本/阶段变更，运行仍可读。
3. readiness 回归先捕获新两阶段被判失败、旧七阶段被误判通过，再修复当前消费者。没有改写历史报告来制造通过。

定向 S1/消费者回归合计 21 项通过，覆盖新运行两次调用与重启、网络失败/超时只重试 validate、queued 取消、候选提交中断的事务回滚及恢复、旧 completed 历史不变、旧 queued/interrupted 继续、旧 extract checkpoint 后续跑、错误 registry/错误 hash/链长度拒绝、readiness 精确顺序。

## 综合门禁

按仓库 `scripts/verify.ps1` 串行执行，pytest 导入前安装外部隔离/凭据阻断守卫：

- Ruff 通过；188 个 Python 文件格式通过；133 个源文件 mypy 通过。
- **586 pytest passed**；保留 1 条既有 Starlette/httpx 弃用警告，未升级依赖。
- Web TypeScript 检查通过；**30 文件、167 tests passed**；Vite 生产构建通过。

这些结果包含原有工程回归，不代替真实模型输出、真人首单或发行验收。

## 真实本地 API / Web

使用独立合成目录、单进程 loopback API 和本轮构建的生产 Web。模型边界使用内存 Responses transport，通过现有真实 Provider 适配器；业务 API、SQLite、材料导入、Claim、引用、页面与剪贴板没有伪造响应。真实客户端构建、凭据存储和非 loopback 网络被阻断；所有守卫记录中真实 Provider 尝试为 0。

| 场景 | 结果 |
|---|---|
| 页面授权、queued 刷新、显式执行 | 新 Run 只有 extract/validate 两个真实 checkpoint、两次适配器调用；产生候选事实与有效引用 |
| 旧 extract checkpoint 续跑 | 页面显示旧七阶段；从 normalize 继续，旧提取记录完全不变 |
| 旧案件重新分析 | 新 Run 两阶段；旧七阶段保留在独立历史项，分别显示正确流程 |
| validate 传输失败 | 页面显示安全停止；刷新后显式重试，仅重复 validate，提取与候选事实不重复 |
| validate 等待期间取消 | 保留 extract；validate 取消，不提交回答 |
| 实际进程中断/重启 | validate 已预留时停止进程；重启后页面恢复预留再显式重试，新进程仅一次 validate；总记账三次，extract 记录和候选事实不变 |
| U2 回归 | 实际页面登记/确认/文本与日期修正、准备单更新、实际剪贴板复制、拒绝剪贴板后的替代反馈通过 |
| 刷新及服务重启后的 U2 | 修正值、来源/历史、准备单与复制均保持；本人诉求仍遵守既有页面状态边界 |
| 视口 | 1440×900、1024×768、390×844 无横向溢出；新流程只显示两步，窄屏截图已做视觉检查 |

浏览器使用隔离的 Edge headless context，版本 `153.0.4234.32`。它不是实际手机、所有浏览器或真人无人指导首单验证。

验收脚本初次两次失败均为脚本未等待授权后的异步页面重读，保留失败 JSON/log；修正等待及刷新检查后，完整首轮与重启轮通过。传输失败与取消场景产生的预期 HTTP 503 被单独记录；最终重启轮无浏览器异常。未放宽业务断言或修改产品来掩盖脚本竞态。

## 停服与保留审计

- 本轮三个启动周期的 launcher/worker 六个进程均已退出；端口 8916 无监听。浏览器 context 已关闭。
- 停服后以只读 SQLite 核对：旧 extract checkpoint JSON/hash 与种子记录一致；新 v2 Run 没有五个占位阶段行；新 validate 输入版本一致；中断样例 completed、总调用记账为 3。
- 开始时的 7 个 U2 tracked 源码/测试 diff 与当前完全一致，6 个已有 untracked 文件逐字节未变。既有文档修改在局部更新中保留。
- 独立最终审查已完成，未发现 S1 引入的可行动缺陷。审查覆盖新旧流程/授权摘要、恢复、输入链、事务、API/Web 消费者及既有验证证据；仅静态读取，没有另行启动服务或重跑测试。

本机详细日志、合成运行目录、浏览器脚本与截图保留在 `H:\codex-output\LifeCase-S1-20260918`。主证据文件：`verify.log`、`api-red.log`、`api-registry-red.log`、`api-s1-green.log`、`readiness-red.log`、`web-red.log`、`browser-s1-first-r3.log`、`browser-s1-restart.log`、`browser-u2-first.log`、`browser-u2-restart.log`、`guard-before-interruption.json`、`guard-final.json`、`service-stopped.json`、`preservation-and-history-audit.json`、`independent-review.md`。

## 未验证与授权边界

没有真实/付费模型调用、私人材料、Provider Key、真人首单/长期 dogfood、远端 CI、新安装包或发布验证。旧 M7 27/30、Ollama FAIL 等历史结果保持原含义。两次合成调用只证明编排减法和边界正确，不证明真实模型质量、成本或时延收益。

本次不提交、推送、打 tag、创建 Release、发布安装器或外部发送；切片结束后不自动进入 U3 或新增企业能力。

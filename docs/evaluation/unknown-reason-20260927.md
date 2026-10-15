# 当前与历史 unknown 具体原因展示

日期：2026-09-27。**当前和历史 unknown 已展示各自保存的具体原因，空白/无理由旧记录保持原提示。** 本轮仅局部 Web 修复，无 Key、无模型，保留全部现场修改；未提交、推送、打安装包、部署或发布。

起点为 `main / e2720b0cbbce55d762ff0fdf902dd8f0edccaa0f`，含此前未提交的 `App.tsx` 新建切案修改及 q1 方案/预检文档。起点 680 个文件的 hash 和完整 dirty diff 保存在 `H:/codex-output/LifeCase-unknown-reason-20260927`。

## 问题与局部修复

[q1 预检](q1-preflight-20260927.md)已观察到：API/checkpoint 有 `uncertainty_reason`，但 Web 当前与历史 unknown 都只渲染固定提示。

- [LiveAgentDossier](../../apps/web/src/features/agentDemo/LiveAgentDossier.tsx) 两处直接使用各自的 `uncertainty_reason?.trim()`；空值、空串或纯空白仍分别显示“当前材料不足以回答这个问题。”和“当时材料不足以回答。”。
- `HistoryAnswerContent` 也被待办的来源分析复用，因此同样显示原 Run 的理由；仍保留原问题、changed/unavailable/legacy 警示及引用，不用当前理由替换旧版本。
- 文本通过正常 JSX 转义，无 HTML 注入。answered、无 answer、事实权威、版本/状态、审批、来源及后端合同均未改变，没有历史数据写入。
- 浏览器发现连续长字符串会撑开当前结论；只在 `.evidence-conclusion-unknown .conclusion-copy` 增加 `overflow-wrap: anywhere`。历史区域沿用既有换行规则；没有重做布局或引入依赖/框架。

## 先回归、再验证

| 检查 | 本轮实际结果 |
|---|---|
| 修复前定向回归 | 25 项中 4 失败、21 通过：当前理由、历史 changed/unavailable 理由与文本转义缺失；旧回退保持 |
| 修复后串行 Vitest | 4 文件 37/37：Dossier 25、AnswerCitations 4、SavedActionAnalysis 2、savedActionAnalysisState 6；单 worker、关闭文件并行 |
| 类型/生产构建 | `npm run check:web`、`npm run build:web` 通过；补换行后重新构建并验证最终资源。没有后端/共享合同改动，不重跑全量门禁 |
| 最终真实 Edge 回放 | 5 场景 × 1440/390 宽度，共 10/10；当前具体理由直接可见、精确历史旧版理由不同、null/空白两向回退、answered、长文本/HTML安全均通过 |
| 视口与图像 | 两宽度全部 document/body 宽度等于 client；检查了窄屏具体理由和桌面长文本截图，未截断正文、未出现页面横向溢出 |
| 独立审查 | 两名协作代理只读核对最终代码、红绿结果、浏览器隔离与清理；无必须修复项。代理审查不是独立真人验收 |

首次浏览器在桌面长字符串场景发现 `1440 → 4789` 横向溢出，当时前四项已通过；随后补上述局部 CSS。首次浏览器关闭超出外部脚本原 5 秒上限，保留 FAIL；其进程随后自然退出，Job 后代计数为 0，5537 端口复核关闭，没有强制杀进程。保留 `first-browser-report.json`、`first-browser-process-report.json`、日志与 `first-cleanup-verification.json`；外部脚本将关闭等待设为 10 秒，并在 finally 核对端口后，再验证最终版本通过。该收尾调整不涉及产品超时或模型失败处理。

## 隔离、收尾与证据边界

浏览器使用本轮独立合成响应文件：只读取前次预检 Q05/Q09 的合成 GET 作为起点，显式构造不同历史理由和回退场景。原产物不变，Source/Claim/Run 数据库没有导入或写入，也没有重跑前次 13 个 Run。

运行前净化环境；使用新的 Edge 上下文，阻断非本次 origin、非 GET、Service Worker 与后台网络。静态服务仅监听动态分配的 `127.0.0.1`，API 必须命中明确回放，否则拒绝。**本轮未导入/启动 FastAPI 后端，凭据恢复与模型客户端路径未运行。** 前端加载前隔离已设置，没有恢复 Key、读取私人材料或调用真实模型。

最终 70 条浏览器请求均为 `127.0.0.1:8262` GET，未有 API 请求落到静态文件服务器；没有业务 POST。最终 owner 为 Node PID 5732，浏览器、静态服务正常关闭，所属 Job 已退出且后代为 0，无清理错误，8262 端口关闭。服务全部停止后才做文档收口。

| 层级 | 结论 |
|---|---|
| 确定性 UI 证据 | SSR 回归 + 实际生产 Web/Edge + 合成 API 响应回放，通过本次展示行为 |
| 本轮真实 FastAPI/持久化/进程重启 | NOT_RUN；没有把静态监听 HTTP 或前次真实 API 证据写成本轮后端验收 |
| 真实模型/费用/时延 | NOT_RUN；固定回复及历史合成 usage 不代表模型质量或收费 |
| 独立真人 | NOT_RUN；自动浏览器和代理图像检查不代替真人操作 |

仅改三个 Web 文件（组件、其现有测试、局部 CSS），新增本记录并更新必要路由/交接。既有 `App.tsx`、后端、依赖、冻结 q1 材料与空结果模板、原预检及其它验收记录保持起点字节；暂存区为空，本机协调文件继续 ignored。

原始证据在 `H:/codex-output/LifeCase-unknown-reason-20260927`：`regression-red.json`、`regression-green.json`、`check-web.log`、`build-web-final.log`、`browser-fixtures.json`、`browser-report.json`、`browser-process-report.json`、四份截图、首次失败、`independent-review.md`、`final-checks.json`。browser fixtures、实际加载 index/JS/CSS 的 SHA256 均留存，可区别前后构建。

下一建议：按 [q1 方案](q1-live-model-plan-20260927.md)只核对 G02～G07 对应当前代码的证据，已有充分证据直接引用，仅补具体缺口。真实模型留给明确 Provider/model/总预算后的任务，不再重复备料或本次展示修复；本轮完成即停止。

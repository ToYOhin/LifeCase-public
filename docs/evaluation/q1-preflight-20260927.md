# q1 无 Key、无模型备料与记录流程预检

日期：2026-09-27。**确定性备料与记录流程通过；真实模型、独立真人保持 NOT_RUN。** 12 题一轮加 Q09 的 H9 setup，共 13 个 Run，均完成；实际 HTTP/Web 回看四题。发现 unknown 具体理由未在 Web 主结论展示的现有限制，未改产品或评分门槛。

协议：[q1 模型验收方案](q1-live-model-plan-20260927.md)。输入：[q1-v1](../../fixtures/synthetic-cases/q1-v1/README.md)。[真实模型空模板](q1-live-model-results-template.json)保持原字节，24 项均为 NOT_RUN/null；本报告单列 D 层结果，不转填模板中的任何成绩。

## 范围、现场与隔离

- 现场为 `main / e2720b0cbbce55d762ff0fdf902dd8f0edccaa0f`，保留新建切案的 `App.tsx` 及全部既有修改。没有产品、依赖、测试、旧 M7 或冻结的 cases.json/TXT 修改，没有提交、推送、打包、部署、发布或远端/CI 核查。
- 全新仓库外合成根：`H:/codex-output/LifeCase-q1-preflight-20260927/runtime`。脚本、实际请求/响应、失败、数据库和截图仅保存在该产物目录，不新增项目 runner 或通用框架，也不运行旧 M7 CLI。
- 先清空继承环境，仅保留 Windows 系统路径及临时目录所需字段；应用导入前固定隔离配置，禁用凭据恢复与持久设置，阻断凭据存取、真实客户端/生成和 Python 非回环网络。只允许精确绑定的内存确定性传输通过现有 OpenAI 适配器。没有真实 Key、模型或私人材料。
- controller 禁止代理及 HTTP redirect，只允许回环；浏览器为全新 Edge 上下文，禁后台网络、Service Worker 与非本次 origin 请求，页面只允许 GET/HEAD。它们是本次运行守卫，不是产品的操作系统级沙箱证明。
- 串行运行；沿用现有 Edge/Playwright、Python 和已验证 Web 包。136 个 Web 源码/配置文件与上次验证快照相同，资产名/大小匹配并另存 SHA256，没有重新构建或安装依赖。

## 实际执行与逐题结果

通过正常 HTTP 创建 12 案、导入 14 个 Source；Q05/Q07/Q09 经现有 Claim 创建、确认、修正命令备料。原文与预期分离，案件 goal 不含答案。替身仅从实际 adapter 输入的检索上下文/当前事实构造响应；必要依据缺失会报 CONTEXT_MISS，未手塞来源或改检索式。本次所有可答题的所需依据均进入实际上下文。

| 题目 | 实际终态/答案 | 记录核对 |
|---|---|---|
| Q01 | completed / answered | M7 原文商家、订单，2 个原文引用 |
| Q02 | completed / answered | 期限及申请材料，2 份实际检索来源、2 个引用 |
| Q03 / Q04 | completed / unknown | 缺序列号/起算依据；无伪造引用 |
| Q05 | completed / unknown | 两个独立原文确认的 H8/H9 Claim 均冲突且不可用；HTTP 理由明确冲突 |
| Q06 | completed / answered | 两份期限政策，3 个原文引用，无擅定优先级 |
| Q07 | completed / answered | H9 用当前 fact_ref，24 个月用与旧型号区间不重叠的 source_ref |
| Q08 | completed / answered | 无 Source、检索片段为 0；SYN-001 只用 1 个有效人工确认 fact_ref |
| Q09 | setup H9 + 主样本 H10 均 completed / answered | 新预览/授权/新 Run；旧 Run 显示 changed，旧答案与数据库 Run/stage 整行摘要不变 |
| Q10 | completed / answered | 条件性维修原文，1 个引用；不保证免费 |
| Q11 / Q12 | completed / unknown | 不承诺赔偿、不冒称发送/受理；unknown 不是调用失败 |

每个 Run 都是独立问题 q1、单 `validate`、一次确定性传输，共 13 次，无 repair、重试或新增分析诊断。记录实际 preview hash、Run/Case 身份、input binding、transport input/output、stage checkpoint/hash、HTTP 终态、原文坐标/hash及事实版本/value_sha256；checkpoint 与持久化记录逐项相符。

每次分析前后只比较 **Case、Source Segment、current_facts、Actions、Approvals 五种 API 投影**，均相同。不能据此外推未逐项比较的 Timeline、transitions 或 deadlines；备料本身通过领域命令生成自己的审计记录。

### Q08 停服预置

第一服务完全退出、所属进程 Job 清空、监听关闭后，单独守卫进程使用既有 `ClaimService.create_claim/confirm_claim` 创建 `serial_number=SYN-001`、`sources=()`，以 `USER` 确认并保留 actor/reason/版本。没有直接 SQL 写入或虚构 Source/Segment。

然后第二服务重启同一绝对数据根；q1 预览、授权、执行走实际 HTTP，Web 实际回读 SYN-001 与人工确认说明。**这是领域服务合成预置，不证明当前 UI/API 已支持无来源创建 Claim。**

### 记录流程中的费用与时延

实际采集了每次 HTTP 端到端耗时，以及隔离库 stage 的 adapter latency。13 个合成请求端到端为 22.459～141.425 ms，adapter 为 1～6 ms；这些数值只检查采集通道，不是模型时延、SLA 或方案时延门槛通过证据。

替身固定返回每次 20 输入/10 输出 token；应用每次按 7 分记账，13 次合计 91 分。这是合成 usage 与应用预留结算，不是供应商用量/收费。真实模型分数、估算价目、供应商账单与真人结果均未填写，未知值保持 null。

## 浏览器回读、失败保留与展示缺口

最终 Edge 通过实际监听 HTTP 回读 Q05、Q07、Q08、Q09，精确核对 Case/Run、问题及结果；Q09 按旧版本定位并核对 H9/changed，Q08 留有截图。没有浏览器响应回放，没有业务 POST 或额外传输调用。

失败均保留，未用最终通过覆盖首个观察：

1. 运行前独立审查修复外部脚本的启动/超时收尾、重定向、历史版本选择和 unknown 替身过严条件。纯进程自检曾因 Windows Job 计数晚于进程退出而误判强制清理，修正有界等待后两项自检通过；这不是产品失败矩阵测试。
2. 13 个 Run 完成后，首次浏览器因净化环境导致 `channel=msedge` 自动定位失败。核对已安装 exe 后改用绝对路径；没有安装浏览器，也没有重跑分析。保存 `first-preflight-report.json`、`first-browser-report.json`。
3. 第二次只读回看在 Q05 断言失败：脚本误以为主结论直接显示具体冲突理由。现有 Web 实际只显示通用 unknown，冲突事实需展开。保存 `second-preflight-report.json`、`second-browser-report.json`；修正为核对 HTTP 理由、通用提示及展开后的 H8/H9/冲突。
4. 停服检查曾过严地要求 WAL/SHM 不存在；只读恢复前又误要求 WAL 必须存在且为 0，后者在启动前停止。改为 WAL 不存在或为 0 才允许 immutable 核对，未删除任何 sidecar、未修改数据库；最终二者均不存在。这两项是外部检查条件修正。

两次补回看服务都只开放业务 GET/HEAD、阻断生成，新增确定性调用为 0，数据库主文件字节保持；13 个原 Run 始终未重做。

**待解决展示缺口：** `LiveAgentDossier` 的当前/历史 unknown 都不展示 `uncertainty_reason`。Q05 的冲突可在事实区查看，但不能把 HTTP 中完整原因当作 Web 已展示。后续真实模型验收必须分别记录 API/checkpoint 的完整答复和 Web 可见内容；不可见的必要说明不得自动判通过。此次“流程通过”不关闭该问题，不代表真实模型质量通过。

## 收尾、审查与证据边界

| 实例 | PID / 监听端口 | 用途 | 确定性调用 | 收尾 |
|---|---|---|---:|---|
| service-1 | 9028 / 8126 | 12 案备料 | 0 | 正常退出，Job 空，端口关闭 |
| service-2 | 39720 / 8126 | Q08 重启后 13 Run | 13 | 同上 |
| service-3-readback | 27540 / 11228 | 首次只读补回看 | 0 | 同上 |
| service-4-readback | 23240 / 13162 | 最终四题回看 | 0 | 同上 |

另有 Q08 seed 与三次浏览器控制进程，全部本轮 Job 已关闭、后代计数为 0，无强制收尾或清理错误。五份最终 guard（四服务加 seed）的凭据、真实客户端/生成、非回环与越界业务写入尝试均为 0。最终数据库 `integrity_check=ok`、外键检查为空，14 条 Source 对应对象逐条校验大小/hash一致（内容寻址复用，共 6 个不同对象路径）；immutable 只读核对不改变主文件字节。

文档更新前全部 679 个起点文件保持原字节；本轮只新增本记录、增量更新七份必要路由/交接文档。冻结输入/预期、真实模型空模板、产品代码、旧语料与原有验收记录不变。暂存为空，四个本机协调文件继续 ignored。

两名独立协作代理分别审查引用/记录和守卫/清理，运行前意见已处理；运行后核对实际记录及上述限制。它们不是独立真人操作者验收。详细审查与最终文档检查见产物目录。

| 证据层 | 本轮结论 |
|---|---|
| D：确定性预检 | 本方案备料/记录流程与 G01 的 Q09 顺序变化通过；实际 localhost API/Web + 内存替身。未重新执行 G02～G07 完整故障矩阵 |
| M：真实模型 | NOT_RUN；不填写质量、费用、时延或实际模型调用成绩 |
| H：独立真人 | NOT_RUN；自动浏览器与代理审查不替代真人 |

原始产物位于 `H:/codex-output/LifeCase-q1-preflight-20260927`：`baseline-files.json`、`baseline.diff`、`web-build-provenance.json`、`material-manifest.json`、`preflight-report.json`、`Q*-record.json`、`transport-*.json`、`q08-domain-seed.json`、`q09-history-check.json`、`browser-report.json`、`closeout-verification.json`、`independent-review.md`、`final-document-checks.json`。历史失败与脚本修正记录一并保留，不能直接重复执行已占用数据根的脚本。

下一建议：单独处理 Web 当前/历史 unknown 具体原因展示，先补有意义的回归再局部修复，沿用现有字段与只读语义；之后再核对真实模型验收所需 G02～G07 证据及明确的 Provider/model/总预算。本轮到此停止。

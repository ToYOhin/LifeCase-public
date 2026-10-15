# 本人诉求到期望结果 · 2026-09-30

已解决“诉求已保存，却一直缺期望结果”的入口问题。概览直接定位登记入口；用户明确登记已保存诉求为 USER 待核实候选，再通过已有“明确用户确认”成为事实。保存诉求本身仍不创建或确认事实。按用户新要求，本轮及当前下一步骤不安排真人验收，不把历史 NOT_RUN 改成 PASS。

## 实现和边界

- 新增窄接口 `POST /api/v1/cases/{case_id}/desired-outcome`，仅接收严格正整数 `expected_intent_version` 和 actor/reason。值取自同案服务器已保存诉求，禁止请求替换 value/field/case_id。
- 沿用现有事务和 Claim 合同：核对案件未终结、保存版本、非空诉求及所有非 rejected 旧期望结果；单事务创建 USER candidate v1 和创建历史。明确 USER 确认后才是 v2 和可用事实。无来源时不伪造原文；不增状态、数据库结构、依赖或通用框架。
- candidate/inferred 已存在时进入复核，confirmed/corrected 进入当前事实；不覆盖或重复登记。仅 rejected 历史允许新候选，旧记录保留。
- 未保存、脏值、冲突、待重新读取、处理中及终结案件禁登记；同步 busyRef 防双击。结果不明先 GET，读取失败保持保护，不自动 POST。切案/卸载后的 success/catch/finally 不影响当前案件。
- 修复读回旧闭包覆盖新输入的问题：按最新编辑状态比较，保留等待登记期间的新输入并要求明确选择。
- 保留 Claim 既有规范化/4096 字节边界。长诉求不截断，提示缩短后重新保存；常见冲突和无效条件提供中文恢复指引。

## 实际验证

| 检查 | 结果与范围 |
|---|---|
| 先回归 | Web 导航 3失败/27通过；新增后端 30失败/1通过，均因新接口尚不存在，0收集错误 |
| 后端新增回归 | 31/31 在扩大验证中通过；覆盖保存与确认分离、旧值、版本/请求限制、并发、事务回滚、无关领域记录保留 |
| 后端静态 | Ruff、格式、mypy通过，142源文件 |
| 扩大后端检查 | 守卫下728项：722通过，6项失败因脚本删除USERPROFILE导致Path.home不可用。仅保留路径变量后，受影响文件8/8通过；未重跑728项，也不声称完整主机门禁通过 |
| 主机边界 | 可启动不继承守卫子进程的daily_use_package/mutex两文件未运行；未原样执行verify.ps1 |
| Web | 50文件335项通过；随后修复最新输入与长度提示，仅补7文件100项、TypeScript及Web资产检查，均通过；未重复全量 |
| 真实本地API/Web | 实际监听服务、SQLite写入和浏览器交互；8组保护断言跨保留阶段完成，10张有效截图，见下表 |
| 独立审查 | 先方案审查，后代码复审；新输入覆盖和长度提示问题已修，最终无必修项。审查代理不运行测试/服务 |

测试、资产检查、服务和浏览器串行，单核、BelowNormal；Vitest参数实际传给workspace，maxWorkers=1、no-file-parallelism。实际服务PID34504的优先级和affinity已读回。只构建验证所用Web资产，没有安装包、打包或发布。

实际API主案复用上一自动检查的已停止合成副本，原件不改。不重新导入、修正或核实六项旧事实。新增期望结果候选后，台账仍缺desired_outcome；明确USER确认后仅消除该缺项，defect_media继续待补，不能写成所有材料齐全。六项旧事实（包括人工修正日期与旧原文）、Source、Case状态/版本均保持。之后为验证后续保存状态，主案诉求经两次明确合成PUT成为v3，已确认期望结果仍保留原v2，不自动同步覆盖。

实际交错覆盖：脏值不能登记、双击一次POST、等待登记期间编辑保留；真实POST已提交后丢响应，注入一次GET失败仍不重发，随后实际GET恢复；迟到成功与失败刻意忽略AbortSignal，在切案且当前案件新PUT仍等待时完成，当前案件/hash/输入/保存中状态保持，只有新PUT自己的响应解除等待。

## 工具失败和证据分类

扩大后端中的6项路径失败属于外置脚本，未据此改产品。首个浏览器延迟路由释放异常、字段名称期望写错、仅改hash未触发应用重新打开、单核截图超时均保留日志与阶段记录。已创建候选只GET找回，已确认记录不再POST确认；已通过编辑保护断言不重跑。最后只增加一个新的丢响应合成案补完整路径，所有副本中的候选均保留。

`browser-confirmed-stage.json`保留真实确认阶段；`browser-interleaving-stage.json`保留编辑保护阶段；`browser-report.json`的previous_stage显式链接已通过组，最终pass=true不是一次从空数据开始的完整无失败首单。截图改用当前视口后继续交错检查，没有放宽CPU限制。

本轮真实证据是本地业务API/数据库及自动浏览器。延迟、丢响应和GET失败由浏览器传输注入，只证明对应客户端恢复，不能外推为真实磁盘或公网故障。没有真实模型、私人材料、长期使用、真人或原生安装验收；q1原质量FAIL和Q07定向结果均未改写。移动图记录390px进入当前事实的加载状态及无横向溢出，不作为已加载事实卡完整视觉验收。

应用main导入前阻断Windows凭据恢复及真实客户端/模型，Provider disabled，非回环连接守卫已安装。runtime前置计数model/client=0，服务provider_attempts/blocked_mutations=0；旧守卫没有独立网络计数，不概括所有网络尝试为零。后端业务检查允许测试内合成适配器，真实HTTP传输/非回环/子进程计数0。浏览器禁止请求和pageErrors均0。只读设置快照误查旧路由返回404，没有设置写入或Key访问。

## 截图

历史本机产物根：`H:/codex-output/LifeCase-desired-outcome-20260930`。下表仅列原证据文件名，PNG 未纳入仓库，不是公开可访问的图片链接。

| 图片 | 内容 |
|---|---|
| `screenshots/01-exact-intake-entry.png` | 首次入口：缺期望结果直接定位个人登记 |
| `screenshots/02-unsaved-input-protected.png` | 未保存保护：脏值不能登记 |
| `screenshots/final/01-recovered-candidate.png` | 只读找回候选：首次工具失败后不重发 |
| `screenshots/final/03-pending-edit-retained.png` | 新编辑保留：当前输入与已保存内容分别保留 |
| `screenshots/final/04-explicit-user-confirmation.png` | 独立确认：无伪造原文，明确用户确认 |
| `screenshots/final/05-existing-outcome-mobile.png` | 390px入口：当前事实入口加载状态，无横向溢出 |
| `screenshots/final/06-unknown-write-read-failed.png` | 失读保护：结果不明且读取失败时不能登记 |
| `screenshots/final/07-unknown-write-recovered-by-get.png` | GET恢复：已保存候选回读，不重复POST |
| `screenshots/final/08-late-success-preserves-current-request.png` | 迟到成功：当前案件后续PUT仍等待 |
| `screenshots/final/08-late-failure-preserves-current-request.png` | 迟到失败：当前hash、编辑及保存状态保持 |

## 停止与保全

PID34504正常停服exit0，8929无监听；最终浏览器已关闭，无本轮Playwright浏览器owner。689仓库基线只在本次源码/入口允许范围变化，旧合成runtime六文件hash相同，既有修改保留。只读SQLite完整性ok、外键0、读取前后主文件hash相同；最终6Case、2Source、12Claim、7Confirmation，0Action/Approval/AgentRun。六条新增desired_outcome为一条confirmed及五条candidate（含保留工具失败案）。

未提交、推送、打包、部署或发布。原始记录见上述产物根的api-red.xml、api-business.xml、api-harness-recheck.xml、web日志、阶段browser记录、preimport-guard.json、runtime-guard-results.json、process-cleanup.json、closeout-verification.json及independent-review.md；详细解释只留本页。

## 下一步建议

建议局部核对“问题图片或视频”缺件提示与现有导入格式的对应关系，避免把媒体缺件一概提示为从文本原文登记。先追踪模板、缺件计算和当前入口，再决定最小提示修正；不扩展OCR、媒体解析、状态或模型。

```text
在仓库根目录，读取 docs/STATUS.md 和 docs/evaluation/desired-outcome-20260930.md，保留全部修改。
仅核对并修正保修概览“问题图片或视频”缺件提示与当前导入/登记能力不一致的地方；复用已有合成案，先明确实际合同，再补对应回归和最小实现。
无Key、无模型，应用导入前阻断凭据恢复与非回环网络；不新增OCR、媒体解析、状态、依赖或通用框架，保留引用、旧值保护、审批和历史。
低资源串行，只补受影响检查并独立审查；不安排真人验收门槛。详细证据仅留一份，结束停服，不提交或发布。
```

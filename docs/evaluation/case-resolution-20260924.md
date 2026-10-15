# 处理结果与人工案件解决核对（2026-09-24）

最新补证：同日 19:19–19:21 已完成同一隔离数据副本上的真实进程重启、实际 HTTP API/Web 回读与停服，详见本文末尾“同日补证：真实进程重启回读通过”。下文早先的拒绝及未验证结论保留为历史，不覆盖这次新证据。

本轮复用 Case、Action 和 Timeline，仅修复已复现的前端状态操作保护缺口。待办完成仍不代表案件解决，案件是否解决由用户明确决定。未新增后端规则、存储、依赖或框架。

## 现场与原有能力

- main / c28fd28cea7c19b95f3df846a899adf110351d5d，暂存为空；全部此前修改保留，不要求清理工作区。
- 现有 Case 状态服务以 expected_version、actor、reason 提交状态与不可变历史；in_progress / waiting_external 可人工转 resolved，resolved 只允许归档。本轮不新增重开或“全部待办完成”条件。
- 现有结果记录不改变 Case/Action/Approval；完成 Action 需要其既有完成证据合同。Case 状态更新不继承或变更行动审批。
- 已有“案件状态与高级操作 → 状态变更理由 → 标记已解决”入口、closed_at 和 Timeline 足够，无需新建结案功能。

## 复现与局部修复

真实 Web 在修改前复现三项：切案沿用上一案理由；A 保存响应迟到覆盖正在查看的 B 并清空编辑；状态已保存但回读失败后仍可重复提交。

先增加 `caseTransitionRecovery.test.ts`，记录 3 项失败，再实现：

- App 内理由按案件保存，异步结果同时核对导航序号与当前案件；成功仅清空本次提交的原理由，等待期间新编辑保留。
- 状态响应核对同案、前后版本、目标状态及 actor/reason。
- 保存未确认、版本冲突或回读失败时阻断重复命令，提供“重新读取案件状态”。此入口仅 GET、不使用缓存，已确认保存的版本不可倒退；编辑保留，重新读取不会代替人工决定。
- 仅在既有折叠操作区增加一句“待办完成后，由你核对处理结果并决定案件是否解决。”

本轮产品修改限 `apps/web/src/App.tsx` 与 `features/intake/api.ts`，增加上述回归。既有材料导入恢复逻辑保留；没有修改状态机、事实权威、来源、审批或历史。

## 实际验证

原始产物：`H:\codex-output\LifeCase-case-resolution-20260924`。`baseline-files.json` / `baseline-web-src` 保留任务前状态；`baseline-probe.json` 与截图保留真实缺陷，`regression-red.log` 保留先红证据。

串行执行：

- 定向 Vitest：caseTransitionRecovery、intake/api、importRecovery、App、CaseOverviewPanel，5 文件共 32 项通过。
- `npm run check:web`、`npm run build:web` 通过。
- 实际 FastAPI/SQLite + 生产 Web + Edge，数据根为本轮合成副本，凭据恢复在导入应用前禁用；真实和 Fake 模型生成均阻断，非回环网络阻断。
- API：记录合成结果、人工核对 Source/hash/文本并完成 running Action 后，Case 完整响应未变，事实和审批未变。随后通过原有合法状态路径进入进行中，再由 Web 明确标记解决。
- Web：切案理由隔离与返回保留；保存响应迟到期间切 B、返回 A 后只读恢复；真实版本冲突保留理由、读取后重新人工决定；保存后 503 回读失败与再次读取失败均不重复 POST；保存期间新编辑不清空。
- 明确解决后 Case 版本推进、closed_at 存在、actor/reason 写入 Timeline，Action、完成证据、事实、审批和 Source 保持不变。刷新后案件和记录可回看。
- 1440 / 390 / 320 视口无横向溢出；状态输入焦点可见，新增提示与读取入口可读。截图已检查；未执行真人易用性验收。
- 只读独立代码审查未发现可行动问题，关注过的切案、等待编辑、GET 恢复均有实际 Web 证据。

故障注入只作用于合成浏览器响应，不能解释为真实磁盘或网络故障已通过。脚本首次将状态接口正常 200 错写为 201，已保留 `verification-first-script-failure.json`，修正脚本后继续验证；此前完成的结果记录/完成 Action 核对不重复写入。其余证据在 `verification.json`。

## 清理与未验证

本次服务 PID 8748 / 9408 / 34468 经过命令行、创建时间、父进程和监听归属核对后，于 16:08:44 +08:00 停止。16:10:24 再次只读确认这三个进程均不存在，8969 无监听且可绑定，见 `pre-restart-cleanup.json`、`final-cleanup.json`。guard 中 provider_attempts=0、blocked_mutations=0；原始合成数据库 SHA256 未变。

随后启动真实重启验证的命令被平台策略拒绝（`CreateProcess ... rejected: blocked by policy`），未换工具或路径重试。**真实进程重启恢复未验证**；刷新通过不替代进程重启。`verification.json` 已保存 Case、状态历史、Timeline、Action、完成证据、审批、事实和结果记录快照，后续只需只读对照。

没有读取私人材料或 Provider Key，没有模型质量、真人、跨浏览器或安装器验收；未提交、推送、部署、发布或外部发送。本轮停止，不扩展功能。

## 同日续验：平台仍拒绝启动

用户再次授权只补真实进程重启验证与清理。重读 AGENTS/HANDOFF、核对现场 Git 后，只尝试一次相同受保护的 PowerShell `Start-Process -WindowStyle Hidden` 启动，工具仍在 CreateProcess 阶段返回 `rejected: blocked by policy`。没有更具体的拒绝原因，不推断为产品代码问题；没有换工具、路径、端口或启动方式重试。

本次产物保存在原目录的 `restart-verification-20260924` 子目录，保留 `launch-rejection.md`、`preflight.json`、`input-hashes.json`、`repository-before.json` 和 `closeout.json`。16:15:28 +08:00 只读复核确认：本轮 launcher PID、进程身份和服务日志均未生成，旧三个 PID 均不存在，8969 无监听；原合成数据库、verification.json、safe_runtime.py 哈希未变，文档更新前仓库文件哈希均未变。没有本次已启动服务需要停止。

独立预审已核对启动前凭据与模型阻断，以及 Case、状态历史、Timeline、Action/完成证据、审批、当前事实、处理结果快照范围。**实际 API/Web 重启对照本轮未执行，真实进程重启恢复仍未验证**。不重跑已完成的回归、构建或刷新验收。仅更新本证据与必要交接；下一建议先只读查明平台启动限制，再决定何时恢复验收，不继续重复启动尝试。

独立代理随后只读复核新拒绝记录与 closeout，未发现可行动问题；确认本轮没有已启动服务需要停止，不把“未产生进程”表述为“重启/清理验证通过”。文档检查与 ignored 边界检查通过；除本证据及本机 HANDOFF/NEXT_PROMPT 外，产品文件和原有修改均未改变。

## 同日继续诊断：收窄阻断范围

用户明确要求继续检查、放宽自加约束，不能根据历史 Blocked 标签推定整个开发永久受阻。以下为新的实际证据，不覆盖上面的失败历史：

- 当前正在使用的桌面内置执行器为 `C:\Users\HP\AppData\Local\OpenAI\Codex\bin\13995fba801849b0\codex.exe`，`--version` 实际返回 `codex-cli 0.155.0-alpha.16.4`。此前读取到的全局 npm 包 0.154.0 不能代替桌面内置版本；版本不同本身不是故障证据。
- 内置执行器的 `execpolicy check --help` 可用。按[官方规则文档](https://learn.chatgpt.com/docs/agent-configuration/rules)尝试仅对原命令做规则匹配，原命令放在单引号 here-string 中、仅作为检查器输入，不执行服务；该只读检查提交也在 CreateProcess 阶段被 `blocked by policy` 拒绝。检查器没有实际运行，不能宣称得到了本机规则匹配结论，更不能推断 `Start-Process` 这个词本身就是触发条件。未改写、编码或换工具再次提交该检查。
- 普通运行正常：项目 Python `--version` 返回 3.12.13，现有 Node 返回 v24.13.0；2026-09-24 17:07:51 串行运行 `caseTransitionRecovery.test.ts --maxWorkers=1`，3 项通过。验证目的为确认当前独立测试能力，不代替服务重启验收。
- 所查常见应用日志目录不存在；现有 `.codex/.sandbox/sandbox.log` 的最后写入时间是 2026-05-30，未读取其内容，不用旧日志解释 9 月拒绝。
- 独立审查确认：`blocked_before_start` 是单次验证记录，不是项目级禁令。HANDOFF 中无具体解除条件的“待明确允许后”和旧 NEXT_PROMPT 的“只读调查”不能永久约束用户新任务。

当前可支持的结论：本次诊断中的拒绝发生在工具执行准入环节，未进入 LifeCase 运行；具体策略来源仍未知。源码读取、普通本地进程和已验证的局部测试正常，后续功能工作不必等待旧 Blocked 消失。保留数据隔离、凭据/模型阻断、版本/审批合同和发布授权边界；不通过变换命令绕过具体拒绝。只更新证据及交接，没有修改产品或平台权限配置，没有启动新服务。

## 同日补证：真实进程重启回读通过

用户新任务仅要求补足人工解决案件后的真实进程重启回读。现场为 `main / e2720b0cbbce55d762ff0fdf902dd8f0edccaa0f`，已有新建切案的 App.tsx、状态/路线/索引及验收文档修改全部保留。本次不修改产品代码、业务状态、审批或历史，也不重新执行旧结果记录、完成待办、解决案件的 POST。

本次唯一产物目录：`H:/codex-output/LifeCase-resolution-restart-20260924`。以原 `LifeCase-case-resolution-20260924/synthetic-runtime` 为来源，复制数据库与三份材料对象到本次 `synthetic-runtime`；以原 `verification.json` 的七项持久化响应为预期。原件、旧快照与独立副本的 provenance 记录在 `fixture-provenance.json`，后续两个服务实例始终使用同一副本绝对路径。

### 实际进程与传输

| 阶段（北京时间） | 实际服务 PID | 已记录事实 |
|---|---|---|
| 19:19:24 启动并回读 | `42764` | launcher `46160`；关联父子关系、创建时间、8974 监听与 guard instance，API/Web 通过 |
| 19:20:21 停止第一实例 | `42764` | 服务、launcher 与本次 conhost 均退出，8974 无监听 |
| 19:20:44 在同一数据根重新启动 | `26664` | launcher `48160`；不同 PID/instance，启动晚于第一实例停止，API/Web 再次通过 |
| 19:21:33 最终停止 | `26664` | 服务、launcher 与本次 conhost 均退出，8974 无监听；两轮浏览器均关闭 |

证据为 `before/after-process.json`、`before/after-cleanup.json` 和 `closeout-verification.json`。不是两个并行服务、同一服务的页面刷新或 TestClient 重建。停止在只读回读结束后进行，随后使用全新 Python 进程启动；没有模拟写入中断或 Windows 整机重启。

运行包装基于前一任务已经实际使用的导入前守卫，收窄为 GET-only，并固定到本次独立合成副本；没有重新提交历史原 launcher/原数据根/8969 的受拒请求，也没有修改平台规则。本轮启动和重启均成功，不能据此解释此前具体拒绝原因或宣称平台全局限制解除。

### 回读结果

- `before/after-api-verification.json`：通过监听 HTTP 获取每轮 25 个响应；其中 Case、transitions、Timeline、Actions、Approvals、current-facts、Action progress 七项与旧持久化快照完全一致。23 项稳定业务响应在重启前后相同；guard 身份与系统能力响应不用于这一稳定响应比较。
- Case 保持 `resolved v4`、原 `closed_at` 与明确人工 `actor/reason`；列表仍可从其他案件查到。Action 保持 `done v4`，8 条处理记录的正文、ID、顺序、actor/reason 和历史版本不变；35 条 Timeline 保留独立的待办完成事件和案件解决事件。
- 完成证据仍对应同案 `synthetic-purchase.txt`，本轮另核对实际对象文件字节，SHA256 为 `aed3de66227b1c51d3e5ea9677eeeb820ded02f6f52096669b3e4edff486105a`，大小 231 字节；来源分段、当前事实及引用归属保持一致。
- `before/after-browser-verification.json`：两个全新隔离 Edge 上下文直接访问实际监听服务；从其他案件打开目标，回看全部处理结果、完成证据及 Timeline，前后实际页面内容一致。没有响应回放、模拟成功或故障注入；第二实例额外页面刷新通过，单独记录而不当作重启证明。
- `safe_runtime.py` 在导入 `lifecase.main` 前阻断凭据恢复/存取、Provider 客户端与全部模型生成，强制隔离配置并拒绝非回环网络；只放行 GET。两轮终态 guard 的凭据/模型/非回环/业务写入尝试均为 0。

### 保全、脚本修正与独立审查

`closeout-verification.json` 确认原数据库、三份材料对象与旧 verification 快照前后 SHA256 不变；副本数据库/材料字节也与复制时一致，SQLite integrity_check 为 ok，数据库逻辑摘要仍为 `774e35c6ebc012073418a38f479da9052b37c0d07aa6c42ae5c667cccd06dccd`。最终副本没有遗留 WAL/SHM。

首次准备脚本使用 SQLite `mode=ro` 做摘要时生成了共享内存和空 WAL，导致副本文件清单断言失败。原数据库 SHA256 未变；失败副本已保留，归档并清理本次生成的空 WAL/SHM 后，改用 `immutable=1` 且显式关闭连接完成离线摘要。详见 `prepare-first-failure.json`，这是验证脚本问题，没有改产品或重写业务数据。

本轮文档更新前对 670 个仓库/已有本机协调文件核对，全部与任务起点一致；前一任务的未提交修改完整保留。当前生产包晚于全部 Web 源码，App 哈希与前轮已验证构建一致，见 `web-build-provenance.json`。本次没有源码变更，不重新构建或重复已通过单测/全量门禁。

独立只读审查覆盖安全包装、真实 HTTP/浏览器脚本、进程身份及先停后启关系、最终保全与清理证据，未发现必须修复的问题。本次仅追加验收事实并更新必要状态/索引/交接；暂存为空，未提交、推送、打包、部署或发布。

此处关闭的是已有人工解决合成样本的真实服务进程重启回读缺口。真人首单、真实模型质量、长期使用、跨浏览器、安装器与 Windows 整机重启不在本轮结论内。下一建议仅为更新既有 U3 首单任务材料，保持真人结果 NOT_RUN；本轮不自动推进。

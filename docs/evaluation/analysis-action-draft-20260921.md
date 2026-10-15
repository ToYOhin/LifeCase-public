# 当前分析到报修待办（2026-09-21）

范围：当前有效 q1 分析 → 本地整理的可审阅短草案 → 用户明确保存为案件待办。仅合成数据和确定性模型传输；不代表真实模型质量、真人验收或线上部署。

实现复用 `prepare_communication / Risk B`、Claim requirement、Action 版本事件与 Approval。草案正文原样进入现有 payload，绑定 Run、依据和已验证答案的哈希；正文最多 500 字，整个 payload 保持 2048 UTF-8 字节上限。无新表、迁移、依赖、模型步骤或通用框架。独立准备单诉求不自动消费，不改 Case goal，不自动确认事实、审批或执行行动。

读取草案不落库、不调用模型。显式保存用一个 `BEGIN IMMEDIATE` 复核当前 Run、完整依据及已提交引用，再原子写 Action 和审计事件。同 Run 的首次网络回放只在正文、原 actor/reason 与版本 1 都匹配时返回原记录；其余保存需要精确 Action ID/version。修订沿用原审批失效规则。

旧两键沟通 payload 保持兼容。新草案历史从不可变 Run 核验原 Claim 版本，正文不随事实修正重写；依据 changed/unavailable 时仍可读与取消，但不可保存、审批或推进，也不再推荐为案件下一行动。浏览器保留编辑，冲突后显式比较；未保存正文仅在当前标签页暂存，不能当作服务端权威。新 Run 后，当前案件已知旧 Run 的编辑只读回看；存储不可用时仅保留同页内存编辑，不承诺关闭或刷新后的未保存内容恢复。

证据目录：`H:\codex-output\LifeCase-analysis-action-20260921`。

- 先回归：`backend-red.log` 为新入口未实现时的 7 项 404；Web 初始入口/状态门禁也先确认 RED。
- 保存事务接通时发现原 EvidenceContext 服务要求 idle，`debug-preview-existing.log` 精确定位；改用原 RAG 引用验证器核验已提交引用，不在事务内重放检索。没有放宽依据校验或增加重试。
- 独立审查发现并修复首次回放忽略审计元数据、同字段同值多个 Claim 阻断保存、工作台推荐过期行动。`backend-focused-second.log` 留存前两项的准确失败，随后完整后端 **685 passed**；Ruff、格式检查、Mypy 通过。
- 第一轮 Web 类型检查、单 worker **218 passed**、生产构建通过。`action-green-report.json` 的通过记录覆盖真实本地 API/Web：人工修正值、起草无新增模型调用、明确保存、重复/冲突、原样正文、标签页刷新、读取 503、版本 409、保存响应丢失后的同一 Action 回读、父分析读取失败保留编辑、跨案迟到 GET 隔离。
- 浏览器脚本早期失败分别为嵌套 textarea 标签定位、导入前后 Case snapshot 比较、未等待起草按钮出现；调整测试驱动后主链通过。失败日志单独保留，未据此修改业务成功条件。
- 真实 Web 另发现新 Run 后旧未保存编辑虽然仍在 sessionStorage，却无可见回看入口；`action-retained-red-report.json` 及 `web-retained-unit-red.log` 留存失败。补当前案件已知历史 Run 的只读回看，旧正文不进入新草案或复核授权。独立审查再发现存储拒绝时同页内存编辑也应保留，`web-memory-fallback-red.log` 先确认失败后实现局部回退。最终 Web 类型检查、单 worker **221 passed**、生产构建通过，见 `web-final-*`。
- `action-retained-green-report.json`：真实新建分析后刷新，旧编辑仍可只读回看，新正文与复核权限不继承。
- `action-restart-report.json`：实际停止并重启 API 后，原待办 ID、版本 5、正文和分析完整恢复，无 Approval；随后人工修正型号 H9→H10，旧正文保持不变且标记 changed，旧草案保存和待办 start 都返回 409，未保存编辑仍保留。
- `action-memory-fallback-report.json`：真实浏览器拒绝本功能 sessionStorage 读写，再通过 Web 创建新 Run，同页旧编辑仍只读回看；新 Run 不接管旧输入，未执行的队列显式取消。该检查首次误断言创建返回 200（实际正确为 201），原失败报告另存 `action-memory-fallback-driver-status-report.json`，修正测试驱动后通过。
- 上述浏览器报告均为 `pageErrors=[]`、`forbidden=[]`。已检查 1440×900、390×844 截图，无横向溢出；最终重启截图及存储拒绝截图已查看（开发代理视觉检查，非真人易用性验收）。

按 `scripts/verify.ps1` 的七项门禁串行执行等价命令：现有虚拟环境 Ruff/格式/Mypy、完整 pytest、Web check/完整测试/build。pytest 通过外部 `pytest_no_credentials.py` 在导入应用前阻断凭据并限定合成数据根；Web 单 worker。没有重复运行裸启动的整套脚本，以免绕开本轮隔离启动约束。

两轮服务 launcher/server **37208/37264、24816/35932** 均已停止，8960 已释放，见 `cleanup-1.json`、`cleanup-2.json`。启动前阻断系统凭据恢复与非回环连接；两份 `prestop-*-guard.json` 的真实客户端工厂尝试均为 0。适配器累计 11 + 2 次只用于多组隔离分析输入与测试驱动恢复的确定性传输，草案起草/保存没有新增调用。无真实模型、私人材料或 Provider Key 读取。

独立只读代码复审覆盖 API/领域事务、通用入口绕过、旧 Action 读取/推进、UI 权威与迟到响应、当前案件旧编辑白名单及存储拒绝回退；发现的问题均已局部修复，最终未发现剩余可复现问题。最终运行报告及服务停止证据也已独立核对，审查不替代真人或模型质量验收。

本轮保留全部既有未提交修改；`baseline-preservation.json` 确认未删除原文件，既有修改仅在必要入口与合同处叠加。main / HEAD `c28fd28cea7c19b95f3df846a899adf110351d5d` 仅记录，暂存区为空。未提交、推送、部署、发布或外部发送。未验证真人首单、真实模型草案质量、公网/多用户或安装器；短草案不承诺完整长文导出，审批与外部行动仍需既有独立流程。

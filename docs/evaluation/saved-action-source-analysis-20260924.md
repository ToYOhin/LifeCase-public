# 已保存待办回看来源分析与引用

2026-09-24，本地未提交切片。仅补待办到原分析的定位，不扩展 Agent 能力。

## 结果与边界

原待办卡片只有分析标识，没有问题/回答/引用的直接入口。本轮增加默认收起的“查看来源分析与引用”，在卡片内回看保存时的分析；现有编辑组件保持挂载，审批与历史不变。

复用已有同案 progress GET 核对待办版本及 payload，再用 live-agent GET 的 run_history 精确选择唯一原 Run。两次读取均 no-store；不匹配、原 Run 缺失/重复、读取失败时清空来源展示，不回退最新分析。取消请求与展示身份检查阻止迟到内容跨案或覆盖新读取。重试仍重新核对，导航不创建、执行或恢复 Run。

复用 LiveAgentDossier 的历史正文与 AnswerCitations。分别提示“已有后续分析”和事实/材料的依据变化；无法核对时保留相应提示。引用仍通过同案来源、段落、偏移及哈希核对，失败不显示未经核对的原文。技术标识继续折叠。没有新后端、存储、依赖或框架。

独立审查发现复用引用组件时缺少 CSS 变量作用域。浏览器先复现 focus-visible 为 true 但 outlineStyle 为 none，再将原有颜色变量共享给新内容容器；原分析页布局不变。

## 实际验证

原始证据仅保存在 `H:\codex-output\LifeCase-action-source-analysis-20260924`。

- 先新增读取合同及入口回归，首次因实现缺失失败（`regression-red.log`）；实现后 Action 与历史/引用相关 Vitest 101 项通过（`regression-final.log`）。中间 TypeScript 发现测试夹具字段不完整，补齐后检查与生产构建通过。未修改后端或共享 API 合同，按局部 Web 门禁验证。
- 复用 `LifeCase-ui-density-20260922/synthetic-runtime` 的独立副本。原库哈希保持一致，没有制造新 Run/checkpoint。Case A 已有七版分析，已取消旧待办绑定首版，另有未结束待办用来验证编辑保留。
- 真实 FastAPI、SQLite、构建后的 Web 与隔离 Edge 验证原 Run 定位、历史变化提示、原回答、引用高亮、未保存编辑保留及刷新后重新显式读取。初次浏览器脚本误选已取消待办作为编辑样本，失败另存 `source-web-fixture-selection-failure.json`；改选已有 ready 待办后完整脚本通过，没有改夹具状态。
- 浏览器定向注入 503、错案结果、原 Run 缺失/重复、Action 版本冲突、引用读取失败。均不显示其他分析；重读恢复。延迟原案读取后通过实际界面切到另一案，没有迟到串案。故障注入不等于后端真实发生这些故障。
- 1440、390、320 视口实际 Tab/Enter 操作通过，无横向溢出；引用 summary 修复后焦点框为 solid 2px。截图与 `focus-before.json`、`source-web-report.json` 留档。
- 真实服务从 PID 35492 重启为 20528，同一隔离数据库。两案 Actions、Approvals、current-facts、Timeline、live-agent 全量读取结果逐项一致；原问题、回答及引用再次通过 Web 核对（`restart-web-report.json`）。
- 启动前隔离配置，阻断 Windows 凭据恢复、持久化 AI 设置、所有模型适配器/客户端创建及非回环连接。两次运行 provider_attempts / blocked_mutations 均为 0，浏览器写请求与外连为 0；没有读取私人材料或 Provider Key。
- 核对 PID、父进程、命令与创建时间后，仅清理本轮进程。首轮 34660/29380/35492、重启后 19272/13972/20528 均结束。15:21:13 +08:00 确认 8967 无监听且可绑定；未删除其他运行数据。

独立代码及证据复核通过，CSS 问题已修复。基线哈希核对仅本轮相关文件改变，其余既有修改保留。Git 仍为 main，HEAD `c28fd28cea7c19b95f3df846a899adf110351d5d`，暂存为空；未提交、推送、部署或发布。

## 未验证与后续

未调用模型，本次使用既有确定性合成 Run，不证明模型质量；未执行真人或跨浏览器验收。未知/缺失历史字段与依据 unavailable 分支由回归覆盖，实际重启样本为完整但依据已变化的历史分析。

建议下一轮只核对材料导入失败后的已有重试入口，发现明确阻断才局部修复；本轮未执行。

# 已解决案件记录回看核对（2026-09-24）

结论：本轮范围内已有能力完整，未发现需要修复的产品缺陷，未修改产品代码或新增回归。已解决案件可从“其他案件”打开，处理结果、保存的完成证据和 Timeline 一致。核对后停止，不扩展功能。

## 范围与现场

- 现场 `main / c28fd28cea7c19b95f3df846a899adf110351d5d`，保留全部已有修改；暂存为空，无提交、推送、部署或发布。
- 只核对既有 Case、Action、人工处理记录和 Timeline 的读取。待办完成与案件解决仍是不同事件，未改变状态、事实、审批、编辑或历史。
- 产物：`H:\codex-output\LifeCase-resolved-records-20260924`。使用上次合成运行数据的独立副本；原数据库与原验证快照的 SHA256 前后不变。

## 本轮实际验证

1. **进程内实际 API**：`verify_read_api.py` 使用真实 FastAPI 路由和 `TestClient`，只允许 GET。已解决 Case 为 `resolved v4`，同案 Action 为 `done v4`；Case、状态迁移、Actions、Approvals、current-facts、Timeline、progress 共七项响应与此前持久化快照完全一致。列表仍返回该 Case，仅不进入 Today。
2. **记录对应**：8 条人工处理记录与 Timeline 的记录 ID、顺序、actor、reason、历史 Action 版本一致；完成事件与案件解决事件分开保留。完成证据的 Source ID、记录 hash 与同案来源一致。本轮没有重新散列原材料文件字节。
3. **实际浏览器交互**：Edge 加载现有生产包，回放上述 API 返回值。验证从列表打开、处理结果正文、完成证据、Timeline 详情、刷新恢复、切换案件不串案、503 读取失败后人工重新读取。390px 视口无页面横向溢出。生产包晚于全部相关前端源码；没有为本轮重建。
4. **既有相关回归**：CaseDocket、ActionProgress、处理记录 API/状态及 Timeline 前端测试 23 项通过；后端 Case list 与 Timeline 测试 22 项通过。串行运行，没有产品变更，未重复全量门禁。pytest 提示既存 TestClient/httpx 弃用警告，未改依赖。
5. **保护与清理**：应用导入前指定合成数据根并阻断系统凭据恢复，Provider 默认关闭且生成方法拒绝执行。API 与 pytest 模型尝试计数均为 0；API 非 GET 尝试为 0，副本数据库前后逻辑摘要相同。浏览器仅允许本轮回放请求，最终 unexpected/errors 均为空。脚本结束关闭浏览器；本轮未启动监听服务，现场核对无本轮脚本或自动化浏览器残留，8969/8970 无监听。

API、浏览器结果及截图分别见产物目录中的 `api-verification.json`、`browser-verification.json`、`resolved-*.png`；回归输出见 `api-tests.log`、`web-tests.log`。`repository-before.json`、`original-hashes.json` 和 `cleanup-verification.json` 留存保全及清理证据。

## 解释与边界

- Timeline 按既有合同投影结果记录的身份、类型、actor/reason 和版本，正文从人工处理记录回看。它不复制正文，也不把备注视为行动完成或案件解决；这不是缺陷。
- 浏览器采用 API 响应回放，没有验证浏览器到监听 HTTP 服务的真实传输，更不证明真实服务进程重启恢复。此前人工案件解决的重启缺口仍单独保留，不阻断其他独立开发或验证。本轮未重试曾被拒绝的启动命令，也没有换路径启动服务。
- 首次 API 核对脚本误写完成证据 hash 字段名；改为合同中的 `authority_sha256` 后通过。首次浏览器脚本遗漏 `preparation-intent` 响应并使用旧空列表文案；修正验证脚本后通过。原浏览器失败记录 `browser-failure.json` 保留，这些是验证脚本问题，未据此改产品。
- 无真人、真实模型质量、跨浏览器或新进程重启验收。

## 独立审查

独立代理只读复核后端读取链路、既有合同、外部验证脚本、最终结果与窄屏截图，未发现可行动产品缺陷。特别核对只读数据库摘要、凭据导入保护和回放证据边界。全部既有产品修改保留。

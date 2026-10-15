# Windows 备份长路径回读修复

日期：2026-09-19。本轮仅处理已留档的“备份创建成功，但回读失败”，未提交、未发布。

## 结果与原因

原合成备份已经在原路径通过 `get_latest` 和独立校验，前后 SHA-256 都是 `bcde6a76b4c33f7a53c0f385c5bfc8c6df8b6d5ad76ecc99232f286c5d842068`。原件保留，没有重新生成它或缩短它的目录。

失败原因是 Windows 传统路径长度限制：同一个成员在创建时的校验路径为 252 字符，`get_latest → get_download` 使用的临时目录前缀多 9 字符，解压路径达到 261。完整成员解压时 `destination.open("xb")` 报 `FileNotFoundError`，最终转换为安全的备份校验错误，并不说明 ZIP 校验和损坏。

历史日志与原备份位置见[上轮控制文件记录](daily-use-control-contention-20260919.md)。本轮原始证据统一保留在 `H:\codex-output\LifeCase-backup-path-20260919`，旧记录不回写。

## 局部实现

产品改动限于 `apps/api/src/lifecase/storage/backup/service.py`：

- 备份和恢复内部工作根统一为 Windows 扩展路径；先规范化为绝对路径，再加前缀。临时目录、归档解压、校验票据、恢复 staging 及清理沿用同一表示，原目录未迁移。
- `verify_backup` 与 `validate` 接受普通长路径，模块内部转换；调用者无需自行添加前缀。
- 只读快照 URI 对原生路径编码，避免扩展前缀的 `?` 和目录中的 `#`、`%` 被解释成 URI 语法；仍使用 `mode=ro`，不存在的数据库不会被创建。
- 没有缩短生产临时前缀、修改系统长路径策略、扩大重试、搬到系统 TEMP 或新增通用文件框架。
- 哈希、成员大小/数量、manifest、SQLite integrity/外键、来源与 Case 成员校验不变；`os.link` 内容寻址且不覆盖的发布、同目录 staging 后 `os.rename` 恢复发布、现有目标拒绝都保留。
- 备份格式、HTTP 返回、数据库表和依赖不变。已有准备单诉求恢复校验与上轮日用控制文件修改保留。

选择扩展路径依据 [Microsoft 长路径规则](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)，URI 编码与只读参数依据 [SQLite URI 规则](https://sqlite.org/uri.html)。本机 SQLite 路径实验也确认编码后读取原值且拒绝写入，见 `sqlite-path-probe.json`；该初始实验路径 255 字符，超过 260 的依据来自下述完整回归，不能混为一项证据。

## 回归与实际运行

新增行为回归位于 `apps/api/tests/integration/test_backup_restore.py`，复用原合成家电权威 fixture。先 RED，再改产品代码：

| 证据 | 实际结果 |
|---|---|
| `regression-red.log` | 3 失败：126 字符备份根创建成功、261 字符回读失败；190 字符工作根创建校验失败；扩展路径/特殊字符只读 URI 失败 |
| `regression-green.log` | 初步修复后 3 通过 |
| `native-input-red.log` | 独立审查发现普通长路径输入仍可能在 validate 后半段保留时失败；改用普通路径输入后 1 失败，保留证据 |
| `backup-final-targeted.log` | validate 入口统一转换后，全部 23 项备份/恢复回归通过；包含原完整性/路径拒绝与新长路径、原件不变、损坏备份拒绝、已有恢复目标拒绝、临时目录清理、只读及缺失文件不创建 |
| `api-acceptance.json` | 实际 PowerShell 日用启动和真实 loopback API 通过；具体行为见下文；finally 停服成功 |
| `verify.log`、`mypy-linux.log` | 综合门禁 633 后端、189 Web 通过；Ruff check/format、主机和 Linux 平台 mypy、Web 类型及生产构建通过。仅既有 Starlette/httpx 弃用警告 |

真实运行使用长度为 118 字符、包含空格及 `#100%` 的新隔离数据根，服务端口 8957。启动前已建立凭据与网络守卫；运行目录通过既有函数初始化，没有重装依赖或执行安装器。

通过实际 API 创建合成案件、导入 TXT 并生成证据包，然后创建备份。最近备份、能力提示和下载均返回同一 ID，ZIP 下载 SHA-256 一致；按旧路径拼接计算成员长度仍是 261，没有缩短目录绕过故障。

实际上传此 ZIP 验证并恢复到新目录后，用现有领域服务重读案件与证据包；已存在目标被拒绝，损坏 ZIP 被拒绝，原下载内容不变。实际停止并重新启动同一数据根后，再次读取同一备份与案件通过。生产 Web 页面能读取，本轮没有 Web 交互改动，未重复点击/复制或宣称真人验收。

综合门禁的 basetemp 与上轮失败 basetemp **长度相同**，路径和长度记录于 `full-basetemp.json`；新增回归又固定构造 126/190 字符工作根，不依赖把测试目录改短。测试、检查、构建与实际服务验证均串行运行；只读审查并行。

## 数据保护、停止与审查

外部 `safe_runtime.py` / `pytest_no_credentials.py` 在 eager 应用导入前隔离配置，阻断 Windows 凭据访问和非 loopback 连接；真实日用父/子进程由 `sitecustomize.py` 在导入前关闭设置恢复并禁止生成，守卫失败直接退出。产品默认值未修改。确定性测试不等于真实模型质量。

独立代码审查 `code-review.md` 保留首次发现的普通长路径入口问题及修复后的 23 项通过证据，最终无剩余可行动问题。随后只格式化了新测试，最终版本通过综合门禁，指纹见 `final-source-hashes.json`。

`preservation-audit.json` 确认原有准备单恢复函数完整未变，其他既有产品改动逐字节保留；本轮相关文档按新事实更新，没有触碰旧备份或历史记录内容。`final-process-audit.json` 记录本轮服务退出、8957 无监听、runtime 仅剩日志，16 份守卫未记录模型尝试，系统 LongPathsEnabled 仍为 0。

最终独立证据审查 `independent-review.md` 未发现剩余可行动问题，独立重算最终源码/测试与历史 ZIP 哈希、原文件保留情况及现场停止状态，结论一致。`final-document-git-audit.json` 记录本地链接与 diff 检查通过，main/HEAD 不变，暂存区为空，协调文件继续 ignored。

## 未验证与下一建议

- 本机本地磁盘范围内验证；实际 UNC 网络共享、Windows 极限路径长度、整个应用所有模块的长路径、安装版 EXE 未验收。
- 未改变系统策略，未调用真实模型、读取私人材料/Provider Key；真人 U3、私人长期使用和远端 CI 未执行。
- 原有 Starlette/httpx 弃用警告保留，没有升级依赖。

建议下一次只提供“中文准备单保存为本地 TXT”，复用已有复制格式和复制前当前事实核对，不新增后端存储、不代表发送或行动完成。本次不自动执行，完整提示见本机 ignored 的 `docs/NEXT_PROMPT.md`。

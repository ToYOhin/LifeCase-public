# Windows 日用控制文件占用：调查与局部修复

日期：2026-09-19。源码未提交、未发布。唯一范围是日用启动控制文件的 Windows 占用；没有扩展准备单或 Agent 能力。

## 结论与边界

已确定复现并修复本程序内部控制文件读取与替换/删除交叠。Windows 同一运行目录的控制文件访问通过一个命名互斥量串行化；保留原子替换、已有启动锁和进程归属检查，没有增加文件重试或修改权限。

旧日志证明当时 `os.replace` 报 WinError5、清理 `unlink` 报 WinError32，但没有占用句柄归属信息。本轮回归证明内部读取足以触发同类失败，不能据此认定历史瞬间一定由内部读取造成，也不能排除外部扫描程序。

原始证据保留于 `H:\codex-output\LifeCase-daily-control-20260919`。没有读取私人材料或 Provider Key，没有真实模型调用、真人验收、提交、推送、发布或外部发送。

## 调用链与实验证据

原始日志来自上一轮 `LifeCase-preparation-save-20260919/daily-use-first-failure.log`，副本为本轮 `historical-failure.log`；原[诉求保存记录](warranty-preparation-save-20260919.md)不回写。

| 调用方 | 实际访问 | 竞争结果 |
|---|---|---|
| 启动父进程 `_wait_until_ready → _read_state` | 读取并关闭 state JSON | 原来不与写入方协调 |
| 服务 `_serve_and_watch.watch → _atomic_json` | starting → running；临时文件 flush/fsync 后 `os.replace` | 目标仍有普通读句柄时 WinError5 |
| 服务退出清理 / stop / status | 读取归属，删除 state/stop | 未关闭读句柄时 WinError32 |
| 启动锁与错误记录 | 读、独占创建、替换、删除 | 相同短文件竞争，也需协调 |

行为回归使用生产读取函数、真实 Windows 文件句柄和事件握手控制交叠，不依赖高频压力循环。基线记录 `control-red.log`：10 失败、2 通过。曾试用带 `FILE_SHARE_DELETE` 的读取句柄，但本机目标文件替换仍失败，`control-green.log` 保留 4 失败、8 通过；该方案已撤回，未进入最终实现。

原生实验中 ReplaceFileW 能替换被共享删除句柄打开的目标，但其文档列出的部分失败会移动或丢失旧目标路径，因此未用它替换原子发布合同。最终选择协调本程序的访问，相关官方语义见 [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[ReplaceFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew)。实验仅支持本机句柄行为，不等于历史占用者溯源。

## 最终实现

产品改动仅在 `apps/api/src/lifecase/daily_use.py`：

- `_control_access` 根据解析、大小写规范化后的 runtime 目录生成 `Global\LifeCase.DailyUse.Control.<hash>`。state、stop、error 和启动锁使用同一个 Windows 互斥量；不同目录独立。非 Windows 路径维持原行为，没有新增磁盘锁文件。
- 只锁短事务：完整读取直至关闭、独占创建、临时文件写入/替换及清理、读归属后删除。启动进程、HTTP 就绪检查、进程等待、sleep/await 都在锁外。
- 最多一次 5 秒互斥等待；超时返回 `runtime_control_busy`，持锁者异常退出返回 `runtime_control_interrupted`。当次失败不写文件，不自动重跑命令；获得的所有权和句柄在 finally 释放。
- 原 `_atomic_json` 的临时文件独占创建、flush、fsync、`os.replace` 和失败临时文件清理保留；外部不合作读句柄仍立即报错，测试明确检查仅一次替换且旧内容完整。
- 现有启动锁的 PID、令牌、独占创建和一次僵死锁回收保留。未让命名互斥量替代整个服务生命周期的多实例识别。
- 可选 state/error 在真正打开前消失视作不存在；损坏/无效状态仍失败关闭。
- 停止或启动失败后的退出清理，在同一短事务内重读并核对 PID 与 token，再删除本运行控制文件。即使 PID 相同而 token 不同，也保留新运行的 state/stop。
- 读取/写入控制文件报错时，仍继续终止本次 Popen 返回的已知子进程 PID；不通过损坏文件推断其他 PID，不清理其他运行文件。

互斥量句柄生命周期、同线程重入和弃用持有者行为按 [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw)、[WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject) 实现；Global 名称的用途见 [Windows kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces)。没有通用锁/工作流框架、新依赖、表或迁移。

## 实际验证

所有产品检查、测试、构建及运行验收串行执行。独立代理仅并行进行只读审查。

| 证据 | 结果 |
|---|---|
| `serialization-red.log` → `serialization-green.log` | 最终串行化回归先 10 失败/2 通过，修复后 12 通过 |
| `owner-red.log` → `owner-green.log` | 替换所有者清理回归先 2 失败，修复后文件内 14 通过 |
| `daily-targeted.log` | 控制文件、真实跨进程 mutex、已有日用包回归共 43 通过；之后增加的已知子进程错误分支 2 项见 `known-child.log`，均通过 |
| 跨进程边界 | 真实隐藏子进程持锁；同目录阻塞、不同目录独立、超时保留文件、异常释放、持锁进程退出后的 WAIT_ABANDONED 和下一操作恢复均通过 |
| `mypy-linux.log` | Linux 平台静态类型检查通过，135 源文件；不是 Linux 运行测试 |
| `verify-short-root.log` 综合门禁 | 630 后端、189 Web 通过；Ruff check/format、主机 mypy、Web 类型检查和生产构建通过。1 项既有 Starlette/httpx 弃用警告 |
| `cli-acceptance.json` | 实际 `scripts/lifecase.ps1 start/status/stop`：端口冲突失败、解除冲突后启动、重复启动拒绝且原 PID 保留、正常停止与同根重启均通过 |
| 实际 API / Web | 8956 loopback 的健康/能力接口和生产 Web 静态页可读；创建合成案件，实际进程重启后按 ID 读取内容一致，Provider 状态 disabled |

PowerShell 验证没有执行会恢复依赖的 setup，而是调用已有运行目录初始化函数；start/status/stop 均通过实际脚本。此轮无 UI 行为改动，未重复上一切片浏览器点击/复制检查，未宣称真人结果。

### 门禁中保留的两次失败

1. `verify.log`：校验参数中的反斜杠被 pytest 参数解析转义，临时目录误落入代码目录；安全校验正确拒绝，10 失败/620 通过。所有该次合成产物经解析路径检查后移至 `pytest-path-error`，没有删除其他数据。修正外部参数为正斜杠，并在测试收集前断言 basetemp 位于本轮输出根；产品代码未改。
2. `verify-final.log`：较长且正确隔离的目录下，629 通过/1 失败，备份创建成功但能力接口没有返回最近备份。只读审查确认 ZIP/成员校验和 SQLite 完整性正确；创建校验最长路径 252 字符，回读临时目录前缀多 9 字符，达到 261 字符，本机未启用长路径。串行调用该合成根的 `BackupService.get_latest` 捕获 `BackupIntegrityError → FileNotFoundError`，底层路径长 261，见 `backup-long-path-diagnostic.json`。本轮未改备份代码；较短合成目录用于完成门禁，不把它当作长路径缺陷已修复。

### 凭据阻断与停止

外部 `safe_runtime.py` / `pytest_no_credentials.py` 在 eager `lifecase.main` 导入前隔离配置，禁止 Windows 凭据访问和非 loopback 连接。`sitecustomize.py` 对真实日用命令父/子进程在导入前验证隔离根、关闭设置恢复并禁止所有模型生成；守卫初始化异常直接退出，不允许继续导入。确定性 Provider 单元测试保留，仅合成真实服务禁止生成；不将替身测试当模型质量。

`child-guard-*.json` 记录模型尝试数、隔离根及 PID，不记录 Key。实际脚本验证的两组 launcher/worker PID 为 26852/29332、29000/31244，最终均退出，状态/停止/启动锁文件已清理，数据库保留。

`final-process-audit.json`：34 份守卫记录的模型尝试均为 0，全部关闭设置恢复；核对记录 PID 和进程创建时间后，本轮服务均已退出，8956 无监听，CLI runtime 只剩日志。另有两个旧 PID 已被较晚创建的 node/cmd 复用，明确保留，没有按旧 PID 批量结束进程。

`preservation-audit.json`：起始 70 份已有修改中，产品文件均逐字节保留；本轮只增补 5 份已有文档，其余原文件哈希一致。HEAD 仍为 c28fd28cea7c19b95f3df846a899adf110351d5d，暂存区为空。`git diff --check` 通过；协调文件继续 ignored。最终代码哈希见 `final-source-hashes.json`。

独立代码审查未发现可行动缺陷，建议补的“控制文件报错仍清理已知子进程”行为回归已补并通过。最终证据审查见 `independent-review.md`：未发现可行动阻断问题，独立重算原文件/最终源码哈希、守卫与停止状态均一致；较晚复用的 PID 按创建时间识别并保留。`final-document-audit.json` 确认受影响文档本地链接与 diff 检查通过。

## 未验证与后续

- 历史失败瞬间的真实占用者；外部扫描程序长时间持有文件；跨 Windows 交互会话；长期日用稳定性。
- Windows 长路径备份回读缺陷尚未修复。不要放宽校验、忽略成员或把 ZIP 存在视作可恢复证明。
- 真人 U3、私人 dogfood、真实模型质量、安装器/EXE、远端 CI 均未执行。
- 现有 Starlette/httpx 弃用警告保留，不顺带升级依赖。

建议下一次只对“Windows 长路径下备份创建成功但回读失败”补确定性回归与局部修复，保留备份完整性与路径约束；本次不自动开始。完整可复制提示保留于本机 ignored 的 `docs/NEXT_PROMPT.md`。

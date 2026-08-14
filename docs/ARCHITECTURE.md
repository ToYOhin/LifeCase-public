# Architecture

LifeCase 是一个本地优先的单用户应用。浏览器界面、FastAPI 服务、SQLite 权威数据和对象存储都运行在用户选择的本机数据目录边界内。

```text
Browser
  |
  v
Loopback FastAPI
  |-- Case / Claim / Deadline / Action services
  |-- Evidence extraction and Case-scoped retrieval
  |-- Approval, Timeline, Pack and Backup services
  |-- Optional consent-bound analysis runner
  |
  +-- SQLite authority
  +-- Content-addressed object storage
  +-- Rebuildable FTS5 index
```

## Authority

- SQLite 保存 Case、Claim、引用、状态历史和工作流权威；
- 原始材料与生成物使用内容寻址存储，并在读取时重新校验哈希；
- FTS5 只是可重建的检索投影，不是业务权威；
- 浏览器状态、模型输出和工具结果都不能绕过服务端验证直接成为已确认事实。

## Evidence flow

用户主动导入材料后，服务端执行有界提取并生成稳定 SourceSegment。检索始终在 SQL 中限定当前 Case，并在返回前重新验证 Source、Segment、文本和哈希。候选 Claim 必须保留来源引用；最终确认、纠正或拒绝由用户完成。

## Analysis boundary

模型 Provider 默认关闭。可选分析必须先展示服务端计算的范围和预算，再接受明确确认。运行采用固定阶段、结构化输出、一次修复上限、Case-scoped 检索和持久化用量记录。分析结果只产生待复核候选，不提供发送、提交、付款、签署或系统修改工具。

## Runtime

开发模式分别运行 FastAPI 和 Vite。日常使用脚本构建 Web 后，由单一 loopback FastAPI 进程同时提供 API 和静态资源。Windows 打包沿用同一拓扑；安装器只管理当前用户程序文件、快捷方式和卸载登记，用户数据保持在独立目录。

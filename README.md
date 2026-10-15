# LifeCase

LifeCase 是以案件为中心的 Web 个人事务工作台。它把主动导入的票据、邮件、文本型 PDF 整理成可追溯事实，让用户核实材料、准备沟通内容、记录处理经过并明确决定案件是否解决。

数据默认保存在本机。LifeCase 不会自动发送邮件、提交网页表单、付款、签署文件或删除原始材料。

四类事务的通用流程已接通：整理材料、核实与修正事实、登记本地行动、核对审批与完成证据、明确解决案件并回看记录。服务为单用户本机 Web；商业化、托管和多用户不列为当前完成门槛。保修案件另有独立“本次问题”，结合当前已核实/人工修正事实与材料引用分析；依据变化后创建新版本，保留旧问题和回答。本轮代码与进程内 API 检查通过，新增页面实测未运行，详见[四类流程收口](docs/evaluation/project-completion-20261001.md)与[项目状态](docs/STATUS.md)。

当前有效分析还可整理为报修短草案，由用户审阅、编辑后明确保存为案件待办。保存复用现有 Action 版本与审批合同，不增加模型调用；依据变化或读取失败会阻止使用旧草案，原正文仍保留。详见[草案保存合同](docs/API_CONTRACT.md#reviewed-repair-action-from-a-question-analysis)。

已保存的报修准备待办支持人工追加处理进展或结果备注，并在时间线回看。记录本身不会确认事实、批准或完成待办；完成仍需独立审批、开始和明确核对同案材料。依据变化时旧待办保持可读并停止推进。这里的完成仅表示沟通准备完成，不表示已发送报修或家电已修复。

旧待办的“查看原问题并重新分析”可定位其原运行的问题，复用现有核对、授权和草案另存流程；保存后可直接查看新待办或返回原待办。保留旧待办不提交状态变更，取消则需重新读取版本、填写操作人和理由并明确核对。新待办不继承旧审批，旧正文、处理记录与历史不被改写。

已保存正文可以在重新核对版本和依据后复制，也可查看对应原分析与引用。待办按当前可处理、依据变化、无法核对和已结束记录展示。案件是否解决由用户另行决定，已解决案件仍可从列表打开并回看处理结果、完成证据与案件记录。

![LifeCase 中的证据分析结果](docs/assets/p1-13-portfolio/01-v4-pro-answered-1440x900.png)

上图为历史合成演示；当前使用路径与验证结果见[验收索引](docs/evaluation/README.md)。后续源码更新属于 Unreleased，不自动更新既有安装包。

## 下载与安装

本公开仓库已于 2026-10-15 同步当前源码，包含四类事务闭环、保修分析及离线演示材料。同步范围与验证边界见[公开同步记录](docs/evaluation/public-sync-20261015.md)。

**本仓库尚无 Release 或可下载的安装包。** 请使用下方源码入口。仓库保留 Windows 当前用户安装器的构建脚本和[安装说明](docs/WINDOWS_INSTALLER.md)，本次没有打包或发布二进制；历史 `v0.2.0` 记录属于原私有源仓库，不代表公开仓库已有同名资产。

## 主要功能

- 本地 FastAPI、React/Vite 工作台和 SQLite 持久化；
- TXT、EML 和文本型 PDF 材料导入、稳定分段与 Case-scoped 全文检索；
- Claim 来源引用、人工确认、纠正历史和矛盾标记；
- 保修、退款、报销和订阅取消四类案件模板；
- 四类案件的已核实事实回看、普通字段修正和条件字段登记；
- 补材料、准备沟通、跟进及记录结果四种本地行动；推进行动绑定当前事实版本，审批与凭证完成分别核对；
- 保修案件的独立问题、当前事实引用分析及版本历史；
- 中文准备单、本人诉求保存、待补项定位与复制/TXT；
- 可审阅报修草案、待办保存、人工处理记录和案件解决；
- Deadline、Action、Approval、ICS 和 Case 时间线；
- 可独立校验的 Evidence Pack，以及本地 Backup/Restore；
- 桌面与移动视口共用的响应式界面。

## AI 与安全边界

LifeCase 的分析能力以证据和人工复核为中心，不是可自由行动的通用聊天机器人。

- 模型 Provider 默认关闭；仅在用户明确保存配置后恢复本机凭据，启动不执行模型生成；
- 分析限定在当前 Case，必须明确预览并确认；
- 分析只读，不提供外部工具，也不会代表用户执行操作；
- API Key 仅可保存到当前 Windows 用户的 Credential Manager，不写入 SQLite、浏览器存储或仓库；
- 导入内容、模型响应和外部数据均按不可信输入处理；
- 示例、截图和固定评估仅使用合成或不可逆脱敏数据。

详细说明见 [安全与隐私](docs/SECURITY_AND_PRIVACY.md) 和 [Agent 设计](docs/AGENT_DESIGN.md)。

## 从源码运行

要求：Windows PowerShell、Python 3.12～3.14、[uv](https://docs.astral.sh/uv/)、Node.js 20.19+、npm，以及锁定依赖所需的本机包缓存。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\lifecase.ps1 setup -DataRoot "$env:LOCALAPPDATA\LifeCase"
```

入口会恢复锁定依赖、构建 Web、初始化明确的数据目录，并启动单一 loopback API/Web 服务。缺少缓存时会失败关闭，不会静默联网补齐。可使用同一脚本的 `start`、`status`、`stop`、`upgrade` 和 `uninstall` 命令管理应用。

若日用命令提示控制状态忙碌或中断，请保留数据，稍后用同一数据目录执行 `status`，核对状态后再重试原命令。不要通过删除运行记录强行启动第二个实例。相关修复与验证边界见[控制文件占用记录](docs/evaluation/daily-use-control-contention-20260919.md)。

停止服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\lifecase.ps1 stop -DataRoot "$env:LOCALAPPDATA\LifeCase"
```

## 四类合成演示备料（离线）

源码环境可一次准备保修、退款、报销和订阅取消的固定 TXT、字段清单和操作说明：

```powershell
$demoOutput = Join-Path $env:TEMP 'LifeCase-demo-kit'
.\.venv\Scripts\python.exe scripts\prepare-demo-materials.py --output $demoOutput
```

命令只准备材料，不启动服务、读取 Key、调用模型或写入案件数据库。相同材料重跑直接复用；目录内容有变化则停止，不覆盖。操作说明位于输出目录的 `README.md`，候选登记、事实确认及后续行动仍在页面中明确操作。材料为合成文字，图片/发票说明不等于媒体本体或真实凭证核验。详见[备料验证](docs/evaluation/demo-materials-20261008.md)。

## 先完成一件家电事务

当前源码的无模型家电使用路径：导入材料 → 人工核实事实 → 回看、按需修正 → 在“行动与截止”预览、复制或保存中文报修准备单为本地 TXT。复制和下载前都会重新核对当前事实与来源，失败时不导出旧内容。详见 [中文报修准备单](docs/WARRANTY_PREPARATION.md)。此源码功能不表示已发布的安装包已更新。

仅当前有效的已确认/已修正事实进入肯定陈述；缺件、冲突和失效来源会提示核对。本人诉求可明确保存到本案件，刷新或重启后继续；未保存修改不会自动保留。复制前重新核对当前事实，复制不代表发送，也不代表行动完成。

准备单的“补充/复核/查看”入口会定位到对应字段；序列号可从材料原文登记，按售后要求提供。登记仍需明确复核确认，返回准备单后更新；连续登记不会被同案件的后台读取清空。

当前重点与后续安排见 [项目状态](docs/STATUS.md) 和 [开发路线](docs/DEVELOPMENT_ROADMAP.md)。

需要分析时，在“问题分析”输入本次问题，核对依据并明确授权。审阅草案后可保存为待办；后续处理记录、审批、核对完成证据及案件解决仍分别由用户决定。已有准备单和人工处理入口可在 Provider 关闭时使用，模型不替代事实权威或人工决定。

## 项目结构

```text
apps/
  api/                    FastAPI、领域服务、SQLite、检索与测试
  web/                    React 工作台及 feature-owned UI
packages/
  case-templates/         版本化案件模板
  contracts/              跨边界 JSON Schema
fixtures/
  synthetic-cases/        合成测试材料
docs/                     产品、架构、安全、API 与测试文档
scripts/                  PowerShell 运行与验证入口
tools/                    已有语料、评估与打包辅助工具
```

模块所有权与实际调用链见[项目地图](docs/PROJECT_MAP.md)。生产 q1、兼容两/七阶段与合成 Demo 的边界见[Agent 设计](docs/AGENT_DESIGN.md)。

## 源码验证

```powershell
uv sync --frozen
npm ci
.\scripts\verify.ps1
```

API 文档默认位于 `http://127.0.0.1:8000/docs`。

## 当前限制

- 仅提供响应式 Web 界面和 Windows 本机打包路径，没有原生桌面 UI、移动 App、托管服务或自动更新；
- 本地构建的安装器默认未签名；
- OCR、通用聊天、SSE 和外部连接器未包含在当前版本中；
- 解析进程有硬超时，但尚未提供操作系统级 CPU、内存和网络沙箱；
- 当前源码不代表企业生产部署认证或真实个人数据的长期持续使用验证。

## 文档

- [文档索引与阅读路径](docs/README.md)
- [产品需求](docs/PRODUCT_REQUIREMENTS.md)
- [项目地图](docs/PROJECT_MAP.md)
- [系统架构](docs/ARCHITECTURE.md)
- [数据模型](docs/DATA_MODEL.md)
- [API 合同](docs/API_CONTRACT.md)
- [安全与隐私](docs/SECURITY_AND_PRIVACY.md)
- [测试与评估](docs/TESTING_AND_EVALUATION.md)
- [Windows 安装器](docs/WINDOWS_INSTALLER.md)
- [原私有源仓库 v0.2.0 历史说明](docs/releases/v0.2.0.md)

## 许可证

当前仓库尚未授予开源许可证。未经版权所有者明确许可，不得复制、分发或再许可本项目代码。

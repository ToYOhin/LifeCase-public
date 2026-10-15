# 发布检查清单

更新：2026-10-15。本公开仓库尚无 Release；这里区分原私有源仓库的历史发布事实与未来候选版本的执行清单。清单不是 push、tag、Release 或上传授权。

## 原私有源仓库的历史发布基线

- 原私有源仓库的正式 [v0.2.0 Release](https://github.com/ToYOhin/LifeCase/releases/tag/v0.2.0) 已存在，非 draft / prerelease；不是本公开仓库的 Release。
- annotated `v0.2.0` 指向 `e7ce75cbc6b725eca0476f8b6273e4700f660750`；资产为 `LifeCaseSetup.exe` 和 `SHA256SUMS.txt`。
- 仓库核查为 PRIVATE，下载需要相应权限；发布与开源许可无关。
- 安装器未签名；不是 MSI、portable、原生 UI、自动更新、Windows 服务或生产就绪证明。
- 当前 [Unreleased](../CHANGELOG.md) 源码改进没有自动进入该资产。版本仍为 `0.2.0`，不能仅凭 package version 推断功能与资产相同。
- 旧候选阶段的 P1-02～P1-13 纳入审计与当时门禁保留在 [历史候选清单](history/release-checklist-v0.2.0-candidate.md)。

## 下一候选版本：范围与本地验证

开始新发布时复制/填写这一节，不把未执行项目预勾为通过：

- [ ] 明确目标版本、精确源码提交、相对既有资产的变化与非目标。
- [ ] Python/npm/锁文件/运行时/Windows 版本资源一致；依赖变化单独说明。
- [ ] 受影响回归及 `scripts/verify.ps1` 通过；Windows API 边界另查 Windows 与 Linux typing。
- [ ] 用户路径使用隔离数据、真实 API/Web 验收；持久化修改另查升级/回滚/恢复。
- [ ] 打包后针对同一构建执行冻结应用、安装、升级失败保留旧安装、启动/停止、卸载保留数据验收。
- [ ] 产物与 SHA-256 对应选定源码和构建；源码、构建、安装验收、CI 不互相替代。
- [ ] Markdown 链接、版本说明、diff、敏感/私人路径与产物扫描通过。
- [ ] 本机协调文件 ignored/untracked，运行时数据/日志/原件/安装二进制未暂存。
- [ ] 独立审查无未处置阻断问题，真实使用/模型/性能等未验证范围有明确说明。

当前准备单源码的合成验证见 [本轮证据](evaluation/warranty-preparation-20260918.md)。本轮提交推送不执行上面的新版本、打包或安装步骤。

## 发布步骤与权限

1. 获明确授权后提交并推送；不 force push、移动旧 tag 或覆盖已有资产。
2. 等待该精确 SHA 的 backend / web CI 均 success；父提交与本地结果不能代替。
3. 核查本地、origin/main 与远端 main；若为新版本，再独立确认 annotated tag 的目标。
4. 获 tag 与正式 Release 授权后执行，Release notes 必须对应同一源码与资产。
5. 获资产上传授权后上传经验证的安装器和校验和；明确未签名及兼容性边界。
6. 核实 GitHub 页面、tag 解引用和资产 digest，再更新当前状态与 CHANGELOG。安装资产不能用较新的源码说明冒充。

## 固定不变的证据边界

Provider 默认 disabled；只读分析不确认事实、不发送。保留原始材料、Case 隔离、人工依据、审批、旧 Run 与失败记录。固定 M7 为 27/30，travel/logistics evaluation-only，dogfood NOT_RUN，Ollama FAIL；M8 未启动。任何新证据需独立记录，不能由发布动作推导。

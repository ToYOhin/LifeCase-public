# 准备单媒体说明与入口核对

2026-10-01，`main / e2720b0`，仅核对当轮指定范围。没有发现准备单宣称媒体已导入、查看或核验的可复现误导，因此未改产品、未新增永久回归或扩展字段。

## 当前行为

- [准备单生成器](../../apps/web/src/features/warrantyPreparation/preparation.ts) 固定整理产品、序列号、商家、购买日期、订单、故障六个事实字段，不含 `defect_media`。媒体缺失、候选、已确认或冲突均不生成准备单专用字段或待补按钮；这是当前功能范围，不能写成媒体待补入口已交付。
- [准备单页面](../../apps/web/src/features/warrantyPreparation/WarrantyPreparationPanel.tsx) 只渲染上述字段的 register/review/current 目标；[App](../../apps/web/src/App.tsx) 对已有目标的路由保持正确，没有自动登记、确认或改值。
- 预览明确“文件已导入不等于材料齐全”，并提醒核对必要图片或视频；导入清单标注文本可读状态，页面明确清单不代表售后材料要求已满足。没有把已导入TXT等同于已核验影像。
- 需要登记材料说明时，可在事实复核展开原文登记并选择“问题图片或视频”；概览推荐步骤符合条件时也能定位该字段。登记和确认针对文本说明，媒体本体仍不导入、查看或解析。[前轮局部修正](media-hint-20261001.md)保留原结论；本轮不重复该轮浏览器验收。

## 必要检查与证据边界

唯一原始产物根：`H:/codex-output/LifeCase-preparation-media-20261001`。

本轮外置 `check.cjs` 只加载当前 preparation/quoteOffsets 两个纯TS模块，复用前轮合成 current-facts GET。Source元数据从引用派生，只有一个Source且提取状态为completed测试值，不是本轮完整Source回读。7种投影状态（缺失、candidate、inferred、confirmed、corrected、冲突、来源不可用）及跨案件媒体Claim拒绝检查通过；媒体值不进入准备单、输入不变，提醒及字段范围保持一致。`check-report.json` / `preparation-preview.txt`是确定性投影回放，不能代替真实API、浏览器、实际登记/确认、影像真实性或真人理解证据。

环境清除和网络阻断在模块加载前完成，限定模块加载清单不包含应用、凭据或模型模块；网络尝试计数0仅属于本脚本。没有导入FastAPI应用、恢复Key、调用模型、启动监听服务或浏览器。真实模型与真人结果保持 `NOT_RUN`，不设置真人验收门槛。单核、BelowNormal、串行，未重跑旧Vitest、类型/生产构建或后端全量。

首次外置脚本误用旧TypeScript编译API路径，随后误将引用中的Source数量认为是完整Source数量；失败日志保留，改用现有Node类型擦除及真实派生数量后通过。均为核对脚本错误，非产品缺陷；没有安装依赖或写入合成数据库。

独立代码/证据审查见产物根 `independent-review.md`。保全检查确认692个Git基线文件除必要文档外原字节不变，协调入口前值备份在before/docs，产品全部原样、暂存为空。无本轮服务需要关闭，前轮8931无监听；没有提交、推送、打包、部署或发布。

## 下一建议

如果希望从准备单直接操作媒体说明，可另行安排增加这一字段及现有导航入口，以减少页面切换。它是便利性功能，不是本轮发现的必须修复项；不自动扩展媒体导入、解析或业务规则。当前安排见[开发路线](../DEVELOPMENT_ROADMAP.md)。

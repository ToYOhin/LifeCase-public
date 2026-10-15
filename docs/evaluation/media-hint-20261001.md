# 保修概览媒体缺件提示修正

2026-10-01，`main / e2720b0` 上的局部未提交修复。只处理当前入口指定的“问题图片或视频”提示；保留所有既有修改。产品增量仅 `warrantyNextStep.ts` 的媒体字段分支，回归沿用同目录测试，不改 API、模板、状态、依赖、审批或历史。

概览现在提示“补充图片或视频说明”，明确当前支持 TXT、EML、文本型 PDF；登记和确认针对材料说明，媒体本体不会导入或解析。按钮仍定位 `defect_media/register`。已有 candidate/inferred 则定位原候选复核，既有冲突、不可用确认值、必填事实和待办优先级不变；不自动填值、引用或确认。

## 实际合同

- [保修模板](../../packages/case-templates/warranty_claim.v1.yaml) 的 `defect_media` 仍要求图片或视频材料；[缺件服务](../../apps/api/src/lifecase/templates/service.py) 依同名、无矛盾的 confirmed/corrected Claim 判断，未检查媒体文件或内容。
- [原文登记](../../apps/api/src/lifecase/claims/manual_service.py) 允许材料要求字段，要求有效同案 Segment 与原文引用，只创建 candidate。[确认](../../apps/api/src/lifecase/claims/service.py) 仍保留 evidence/user、版本和操作记录。有效文本引用证明的是文本声明，USER 确认证明的是用户明确记录，均不能证明系统查看过影像。
- 当前导入只支持 TXT、EML、文本型 PDF；影像 PDF 仍需 OCR。本轮没有增加 OCR、媒体上传、查看或解析能力，也没有放宽缺件或事实规则。

## 本轮验证

原始产物唯一根：`H:/codex-output/LifeCase-media-hint-20261001`。测试、构建、浏览器串行，单核、BelowNormal；没有重复后端全量、旧模型采样或首单备料。

| 检查 | 实际结果与边界 |
|---|---|
| 先回归后实现 | 新增6个展开用例；红灯3失败/33通过，修复后36/36通过。覆盖缺件、candidate/inferred、不可用/冲突确认值、优先级及输入不变，见 `regression-red.json` / `regression-green.json` |
| Web 对应检查 | `npm run check:web`、`npm run build:web`通过，见同名日志；未改后端，不重跑后端门禁 |
| 实际本地 API/Web | 独立复制上一轮已停止合成 runtime，未重新建案或导入。1440/390视口读取同一真实 Case：唯一媒体缺项、诚实提示、支持格式、按钮定位字段与焦点、空值/空引用、既有准备单及缺件不变，无横向溢出 |
| 确定性 GET 回放 | 从真实 GET 派生一个未核实媒体说明候选，仅拦截 current-facts/evidence-review 响应；1440/390概览及原候选复核通过。未持久化这个候选，不写成实际登记或确认验收 |
| 图片证据 | `screenshots/real-*-*.png`六张实际页面，`replay-*-*.png`四张回放页面；10张有效截图，另留失败截图。报告 `browser-report.json` 引用已完成桌面阶段 `browser-mobile-tab-failure.json`，恢复时未重跑已通过桌面步骤 |
| 独立审查 | 同轮独立代理按精确 before 增量审查，无必须修项；只读审查与执行验证分开，见 `independent-review.md` |

应用导入前复用既有守卫：清除配置环境，阻断 Windows 凭据恢复、Provider 客户端/模型及非回环网络，独立合成根、Provider disabled、AI 设置不持久化。`preimport-guard.json` 模型/客户端计数均0，退出 `runtime-guard-results.json` Provider尝试0。浏览器使用新临时配置、禁ServiceWorker，路由只准本机GET/HEAD；没有业务POST/PUT，停服POST仅为外置owner控制。非回环守卫没有独立尝试计数，不据此声称整个操作系统零网络。

真实模型 `NOT_RUN`；当前任务按用户要求不安排真人验收，历史真人 `NOT_RUN` 和 q1/Q07结论不改。本轮未执行媒体说明的实际新增/确认命令，也未做进程重启、模型质量、性能或真实私人材料验收。

## 失败保留与收口

外置取证脚本的错误保留：SQLite快照误用rowid和未编码FTS blob（`preparation-error.txt`）；首次15秒页面加载超时；两个exact-label超时；窄屏误点隐藏桌面页签。登记页失败DOM/截图实际显示字段已选中；修正外置选择器及使用现有窄屏下拉框，没有追加产品修复或重发业务请求。各次失败JSON/log保留，最终有效检查通过，不把这些脚本失败算作产品通过结果。

服务PID11156、8931端口正常停服；本轮浏览器关闭，脚本进程退出，`stop-check.json`确认无服务进程或监听。`closeout-data.json`确认32个SQLite表前后内容完全相同、完整性及外键通过，两个Source原对象和旧runtime六文件hash不变。691文件Git基线只在本轮允许范围变化，暂存仍空，见 `preservation-check.json`；ignored交接的前值逐字保存在before/docs。没有提交、推送、打包、部署或发布。

当轮建议核对准备单的媒体待补项说明和入口；后续[只读核对](preparation-media-audit-20261001.md)未发现必须修复的误导，当前安排见[项目状态](../STATUS.md)。

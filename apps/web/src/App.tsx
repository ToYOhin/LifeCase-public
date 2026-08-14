import { type FormEvent, useEffect, useMemo, useState } from "react";

import {
  createCase,
  getCase,
  LifeCaseApiError,
  transitionCase,
  uploadSource,
} from "./features/intake/api";
import { errorGuidanceFor } from "./features/intake/presenters";
import type { CaseRecord, CaseStatus } from "./features/intake/types";
import { ActionPanel } from "./features/actions/ActionPanel";
import { CaseAgentWorkspace } from "./features/agentDemo/CaseAgentWorkspace";
import { BackupRestorePanel } from "./features/backupRestore/BackupRestorePanel";
import { DeadlinePanel } from "./features/deadlines/DeadlinePanel";
import { EvidencePackPanel } from "./features/evidencePack/EvidencePackPanel";
import { EvidenceReviewPanel } from "./features/review/EvidenceReviewPanel";
import { TimelinePanel } from "./features/timeline/TimelinePanel";
import { CaseOverviewPanel } from "./features/intake/CaseOverviewPanel";
import { listTemplates } from "./features/templates/api";
import {
  templateCatalogView,
  templateIdentity,
  templateOptionLabel,
} from "./features/templates/presenters";
import type {
  CaseTemplateSelector,
  TemplateCatalogItem,
  TemplateCatalogLoadState,
} from "./features/templates/types";
import { listCases } from "./features/workbench/api";
import { statusLabel } from "./features/workbench/presenters";
import type {
  CaseWorkbenchItem,
  WorkbenchLoadState,
} from "./features/workbench/types";
import {
  CaseWorkflow,
  type WorkflowArea,
} from "./features/workbench/CaseWorkflow";
import { LazyWorkflowTool } from "./features/workbench/LazyWorkflowTool";
import { CaseDocket } from "./features/workbench/CaseDocket";
import {
  AiSettingsDrawer,
  AiSettingsPanel,
} from "./features/aiCapabilities/AiCapabilityPanel";

type Activity = "idle" | "creating" | "restoring" | "uploading" | "transitioning";

export function App() {
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [selectedTemplate, setSelectedTemplate] =
    useState<CaseTemplateSelector | "">("");
  const [templateCatalog, setTemplateCatalog] = useState<TemplateCatalogItem[]>([]);
  const [templateCatalogState, setTemplateCatalogState] =
    useState<TemplateCatalogLoadState>("loading");
  const [caseRecord, setCaseRecord] = useState<CaseRecord | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [activity, setActivity] = useState<Activity>("idle");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseWorkbenchItem[]>([]);
  const [workbenchState, setWorkbenchState] =
    useState<WorkbenchLoadState>("loading");
  const [timelineRefreshKey, setTimelineRefreshKey] = useState(0);
  const [transitionReason, setTransitionReason] = useState("");
  const [activeWorkflowArea, setActiveWorkflowArea] =
    useState<WorkflowArea>("overview");
  const [pendingScrollTarget, setPendingScrollTarget] = useState<string | null>(null);
  const [aiSettingsOpen, setAiSettingsOpen] = useState(false);

  useEffect(() => {
    void refreshWorkbench();
    void refreshTemplates();
  }, []);

  useEffect(() => {
    const caseId = window.location.hash.replace(/^#case=/, "").trim();
    if (!caseId) {
      return;
    }
    setActivity("restoring");
    getCase(caseId)
      .then((record) => {
        setCaseRecord(record);
        setActiveWorkflowArea("overview");
        setTitle(record.title);
        setGoal(record.goal);
        if (isTemplateSelector(record.type)) setSelectedTemplate(record.type);
      })
      .catch((reason: unknown) => setErrorMessage(reason))
      .finally(() => setActivity("idle"));
  }, []);

  useEffect(() => {
    if (!pendingScrollTarget) return;
    const frame = window.requestAnimationFrame(() => {
      scrollToSection(pendingScrollTarget);
      setPendingScrollTarget(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [caseRecord, pendingScrollTarget]);

  const failedSources = useMemo(
    () =>
      caseRecord?.sources.filter((source) =>
        ["failed", "ocr_required"].includes(source.extraction.status),
      ).length ?? 0,
    [caseRecord],
  );

  const currentTemplateCatalogView = templateCatalogView(
    templateCatalogState,
    templateCatalog.length,
  );
  const selectedTemplateItem = templateCatalog.find(
    (item) => item.case_type === selectedTemplate,
  );
  const nextStep = workflowNextStep(caseRecord);

  async function refreshWorkbench() {
    setWorkbenchState("loading");
    try {
      setCases(await listCases());
      setWorkbenchState("ready");
    } catch (reason) {
      setWorkbenchState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function refreshTemplates() {
    setTemplateCatalogState("loading");
    try {
      setTemplateCatalog(await listTemplates());
      setTemplateCatalogState("ready");
    } catch (reason) {
      setTemplateCatalogState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function handleReviewChanged() {
    await refreshWorkbench();
    setTimelineRefreshKey((current) => current + 1);
  }

  async function openCase(caseId: string) {
    setActivity("restoring");
    setError(null);
    try {
      const record = await getCase(caseId);
      setCaseRecord(record);
      setActiveWorkflowArea("overview");
      setTitle(record.title);
      setGoal(record.goal);
      if (isTemplateSelector(record.type)) setSelectedTemplate(record.type);
      window.location.hash = `case=${record.id}`;
      setPendingScrollTarget(CASE_WORKFLOW_SELECTOR);
    } catch (reason) {
      setErrorMessage(reason);
    } finally {
      setActivity("idle");
    }
  }

  function startNewCase() {
    setCaseRecord(null);
    setActiveWorkflowArea("overview");
    setSelectedTemplate("");
    setSelectedFile(null);
    setTitle("");
    setGoal("");
    setMessage(null);
    setError(null);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}`,
    );
    setPendingScrollTarget(CASE_START_SELECTOR);
  }

  function scrollToSection(selector: string) {
    document.querySelector(selector)?.scrollIntoView({ behavior: "smooth" });
  }

  function selectWorkflowArea(area: WorkflowArea) {
    setActiveWorkflowArea(area);
    window.requestAnimationFrame(() => scrollToSection(".case-workflow"));
  }

  function continueWorkflow() {
    if (nextStep.area) {
      selectWorkflowArea(nextStep.area);
      return;
    }
    scrollToSection(nextStep.target);
  }

  function setErrorMessage(reason: unknown) {
    const apiError =
      reason instanceof LifeCaseApiError
        ? reason
        : new LifeCaseApiError("unexpected_error", "本地操作未完成");
    setMessage(null);
    setError(errorGuidanceFor(apiError.code, apiError.message));
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedTemplate) {
      setError("请先从已审阅目录中明确选择案件类型。");
      return;
    }
    setActivity("creating");
    setError(null);
    setMessage(null);
    try {
      const created = await createCase({
        title,
        goal,
        template_id: selectedTemplate,
      });
      setCaseRecord(created);
      setActiveWorkflowArea("overview");
      window.location.hash = `case=${created.id}`;
      setMessage("案件已建立，可以导入第一份材料。");
      setPendingScrollTarget(CASE_WORKFLOW_SELECTOR);
      await refreshWorkbench();
    } catch (reason) {
      setErrorMessage(reason);
    } finally {
      setActivity("idle");
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!caseRecord || !selectedFile) {
      setError("请先建立案件并选择一个 TXT、EML 或 PDF 文件。");
      return;
    }
    setActivity("uploading");
    setUploadProgress(0);
    setError(null);
    setMessage(null);
    try {
      const imported = await uploadSource(
        caseRecord.id,
        selectedFile,
        setUploadProgress,
      );
      const restored = await getCase(caseRecord.id);
      setCaseRecord(restored);
      setSelectedFile(null);
      await refreshWorkbench();
      setTimelineRefreshKey((current) => current + 1);
      setMessage(
        imported.extraction.status === "completed"
          ? "材料已导入并完成文本提取。"
          : "材料已保存，请查看提取状态。",
      );
    } catch (reason) {
      setErrorMessage(reason);
    } finally {
      setActivity("idle");
    }
  }

  async function handleCaseTransition(target: CaseStatus) {
    if (!caseRecord || !transitionReason.trim()) {
      setError("请先说明状态变更理由；该理由会写入案件审计历史。");
      return;
    }
    setActivity("transitioning");
    setError(null);
    setMessage(null);
    try {
      await transitionCase(caseRecord.id, {
        expected_version: caseRecord.version,
        target_status: target,
        actor: "local_user",
        reason: transitionReason.trim(),
      });
      const restored = await getCase(caseRecord.id);
      setCaseRecord(restored);
      setTransitionReason("");
      setMessage(`案件状态已更新为“${statusLabel(restored.status)}”。`);
      await refreshWorkbench();
      setTimelineRefreshKey((current) => current + 1);
    } catch (reasonCaught) {
      setErrorMessage(reasonCaught);
    } finally {
      setActivity("idle");
    }
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">LOCAL EVIDENCE DESK</p>
          <h1>LifeCase</h1>
          <p className="subtitle">本地材料核验、事实登记与事务推进。</p>
        </div>
        <div className="masthead-actions">
          <span className="status">仅在本机运行 · 4 类事务</span>
          <button className="secondary-action ai-settings-entry" type="button" onClick={() => setAiSettingsOpen(true)}>
            AI 设置
          </button>
        </div>
      </header>

      <AiSettingsDrawer open={aiSettingsOpen} onClose={() => setAiSettingsOpen(false)}>
        <AiSettingsPanel />
      </AiSettingsDrawer>

      {!caseRecord && (
        <section className="case-focus" aria-labelledby="case-focus-title">
          <div className="next-step-card">
            <span className="next-step-number" aria-hidden="true">
              {nextStep.number}
            </span>
            <div>
              <p className="section-label">当前步骤</p>
              <h2 id="case-focus-title">{nextStep.title}</h2>
              <p>{nextStep.detail}</p>
            </div>
            <button type="button" onClick={continueWorkflow}>
              {nextStep.actionLabel}
            </button>
          </div>

          <div className="case-start-panel">
            <p className="case-focus-note">
              数据只保存在你选择的本机目录。建立案件后，再导入材料、核对事实并决定下一步。
            </p>
            <form className="case-form quick-case-form" onSubmit={handleCreate}>
              <label>
                事务类型
                {currentTemplateCatalogView === "populated" ? (
                  <select
                    name="template_id"
                    value={selectedTemplate}
                    onChange={(event) =>
                      setSelectedTemplate(event.target.value as CaseTemplateSelector)
                    }
                    disabled={activity !== "idle"}
                    required
                  >
                    <option value="">请选择事务类型</option>
                    {templateCatalog.map((item) => (
                      <option key={item.case_type} value={item.case_type}>
                        {templateOptionLabel(item)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="template-catalog-state" role="status">
                    {currentTemplateCatalogView === "loading" && "正在读取本地事务类型…"}
                    {currentTemplateCatalogView === "empty" && "本地没有可用的事务类型。"}
                    {currentTemplateCatalogView === "offline" && "本地服务未连接。"}
                    {currentTemplateCatalogView === "failure" && "事务类型暂时无法读取。"}
                  </span>
                )}
              </label>
              <label>
                案件名称
                <input
                  name="title"
                  value={title}
                  maxLength={160}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="例如：耳机保修申请"
                  required
                />
              </label>
              <label className="goal-field">
                处理目标
                <textarea
                  name="goal"
                  value={goal}
                  maxLength={1000}
                  onChange={(event) => setGoal(event.target.value)}
                  placeholder="例如：整理购买凭证并准备保修材料"
                  rows={3}
                  required
                />
              </label>
              <button
                type="submit"
                disabled={
                  activity !== "idle" ||
                  currentTemplateCatalogView !== "populated" ||
                  !selectedTemplate
                }
              >
                {activity === "creating" ? "正在建立…" : "建立案件"}
              </button>
              {selectedTemplateItem && (
                <div className="template-selection-note">
                  需要核对 {selectedTemplateItem.required_claims.length} 个事实字段和
                  {selectedTemplateItem.evidence_requirements.length} 类材料。
                  <details>
                    <summary>查看模板技术信息</summary>
                    <code>{templateIdentity(selectedTemplateItem)}</code>
                  </details>
                </div>
              )}
              {["empty", "offline", "failure"].includes(currentTemplateCatalogView) && (
                <button
                  type="button"
                  className="secondary-action"
                  onClick={() => void refreshTemplates()}
                >
                  重试读取事务类型
                </button>
              )}
            </form>
          </div>
          <div className="case-feedback" aria-live="polite">
            {activity === "restoring" && <p>正在从本地数据库恢复案件…</p>}
            {message && <p className="success-message">{message}</p>}
            {error && <p className="error-message">{error}</p>}
          </div>
        </section>
      )}

      {caseRecord && (
        <CaseWorkflow
          caseTitle={caseRecord.title}
          caseStatus={statusLabel(caseRecord.status)}
          caseStatusCode={caseRecord.status}
          nextStepTitle={nextStep.title}
          nextStepDetail={nextStep.detail}
          primaryActionLabel={nextStep.actionLabel}
          onPrimaryAction={continueWorkflow}
          onReturn={startNewCase}
          operations={(
            <details className="case-operations">
              <summary>案件状态与高级操作</summary>
              <div className="case-lifecycle-controls">
                <label>
                  状态变更理由
                  <input
                    value={transitionReason}
                    maxLength={1000}
                    onChange={(event) => setTransitionReason(event.target.value)}
                    placeholder="说明为何推进、等待、解决或归档"
                  />
                </label>
                <div>
                  {caseTransitionTargets(caseRecord.status).map((target) => (
                    <button
                      type="button"
                      className={
                        ["abandoned", "archived"].includes(target)
                          ? "is-danger"
                          : undefined
                      }
                      key={target}
                      disabled={activity !== "idle" || !transitionReason.trim()}
                      onClick={() => void handleCaseTransition(target)}
                    >
                      {caseTransitionLabel(target)}
                    </button>
                  ))}
                </div>
              </div>
            </details>
          )}
          feedback={(
            <div className="case-feedback" aria-live="polite">
              {activity === "restoring" && <p>正在从本地数据库恢复案件…</p>}
              {message && <p className="success-message">{message}</p>}
              {error && <p className="error-message">{error}</p>}
            </div>
          )}
          activeArea={activeWorkflowArea}
          onSelect={selectWorkflowArea}
          overview={(
            <CaseOverviewPanel
              caseRecord={caseRecord}
              template={selectedTemplateItem}
              selectedFile={selectedFile}
              failedSources={failedSources}
              isIdle={activity === "idle"}
              isRestoring={activity === "restoring"}
              isUploading={activity === "uploading"}
              uploadProgress={uploadProgress}
              onFileChange={setSelectedFile}
              onUpload={handleUpload}
            />
          )}
          agent={(
            <CaseAgentWorkspace
              caseId={caseRecord.id}
              caseTitle={null}
              caseType={caseRecord.type}
              refreshKey={timelineRefreshKey}
              onChanged={handleReviewChanged}
            />
          )}
          review={(
            <EvidenceReviewPanel
              caseId={caseRecord.id}
              caseTitle={null}
              claimFields={
                selectedTemplateItem
                  ? [
                      ...selectedTemplateItem.required_claims,
                      ...selectedTemplateItem.evidence_requirements.map(
                        (item) => item.key,
                      ),
                    ]
                  : []
              }
              refreshKey={timelineRefreshKey}
              onReviewChanged={handleReviewChanged}
            />
          )}
          actions={(
            <>
              <DeadlinePanel
                caseId={caseRecord.id}
                caseTitle={null}
                refreshKey={timelineRefreshKey}
                onChanged={handleReviewChanged}
              />
              <ActionPanel
                caseId={caseRecord.id}
                caseTitle={null}
                refreshKey={timelineRefreshKey}
                onChanged={handleReviewChanged}
                sources={caseRecord.sources}
                evidenceFields={
                  selectedTemplateItem?.evidence_requirements.map(
                    (item) => item.key,
                  ) ?? []
                }
              />
            </>
          )}
          records={(
            <>
              <TimelinePanel
                caseId={caseRecord.id}
                caseTitle={null}
                refreshKey={timelineRefreshKey}
              />
              <LazyWorkflowTool title="证据包" hint="需要时再恢复或生成可校验 ZIP">
                <EvidencePackPanel
                  caseId={caseRecord.id}
                  caseTitle={null}
                  refreshKey={timelineRefreshKey}
                  onCreated={handleReviewChanged}
                />
              </LazyWorkflowTool>
              <LazyWorkflowTool title="备份与恢复" hint="危险操作保持收起，展开后再读取备份状态">
                <BackupRestorePanel />
              </LazyWorkflowTool>
            </>
          )}
        />
      )}
      <CaseDocket
        key={caseRecord?.id ?? "full"}
        mode={caseRecord ? "switcher" : "full"}
        state={workbenchState}
        cases={cases}
        onOpen={(caseId) => void openCase(caseId)}
        onRefresh={() => void refreshWorkbench()}
        onCreate={() => scrollToSection(CASE_START_SELECTOR)}
      />
    </main>
  );
}

function isTemplateSelector(value: string): value is CaseTemplateSelector {
  return [
    "warranty",
    "return_refund",
    "reimbursement",
    "subscription_cancellation",
  ].includes(value);
}

const CASE_TRANSITIONS: Record<CaseStatus, CaseStatus[]> = {
  intake: ["needs_review", "ready", "abandoned"],
  needs_review: ["intake", "ready", "abandoned"],
  ready: ["needs_review", "in_progress", "abandoned"],
  in_progress: ["needs_review", "waiting_external", "blocked", "resolved", "abandoned"],
  waiting_external: ["in_progress", "blocked", "resolved", "abandoned"],
  blocked: ["needs_review", "in_progress", "waiting_external", "abandoned"],
  resolved: ["archived"],
  abandoned: ["archived"],
  archived: [],
};

export function caseTransitionTargets(status: CaseStatus): CaseStatus[] {
  return CASE_TRANSITIONS[status];
}

function caseTransitionLabel(status: CaseStatus): string {
  const labels: Record<CaseStatus, string> = {
    intake: "返回收件",
    needs_review: "转待复核",
    ready: "标记可推进",
    in_progress: "开始处理",
    waiting_external: "等待外部回复",
    blocked: "记录阻塞",
    resolved: "标记已解决",
    abandoned: "放弃案件",
    archived: "归档案件",
  };
  return labels[status];
}

export function workflowNextStep(record: CaseRecord | null) {
  if (!record) {
    return {
      number: "01",
      title: "建立案件",
      detail: "选择事务类型，写下要解决的问题。",
      target: ".case-start-panel",
      area: null,
      actionLabel: "建立案件",
    };
  }
  if (record.sources.length === 0) {
    return {
      number: "02",
      title: "导入第一份材料",
      detail: "选择 TXT、EML 或文本型 PDF；原文件不会被移动或删除。",
      target: ".case-workflow",
      area: "overview" as const,
      actionLabel: "选择材料",
    };
  }
  return {
    number: "03",
    title: "检查事实与材料缺口",
    detail: "先确认提取状态，再进入事实复核和后续行动。",
    target: ".case-workflow",
    area: "review" as const,
    actionLabel: "进入事实复核",
  };
}

export const CASE_START_SELECTOR = ".case-start-panel";
export const CASE_WORKFLOW_SELECTOR = ".case-workflow";

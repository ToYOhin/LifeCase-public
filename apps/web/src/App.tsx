import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  createCase,
  importRequiresRead,
  LifeCaseApiError,
  readCaseAfterTransition,
  readImportCase,
  transitionCase,
  uploadSource,
} from "./features/intake/api";
import { errorGuidanceFor } from "./features/intake/presenters";
import type { CaseRecord, CaseStatus, CaseTransitionRecord, SourceRecord } from "./features/intake/types";
import { ActionPanel } from "./features/actions/ActionPanel";
import { changedActionTarget, type ActionFollowUpTarget } from "./features/actions/actionFollowUpState";
import type { ActionRecord } from "./features/actions/types";
import { WarrantyPreparationPanel } from "./features/warrantyPreparation/WarrantyPreparationPanel";
import { CaseAgentWorkspace } from "./features/agentDemo/CaseAgentWorkspace";
import { BackupRestorePanel } from "./features/backupRestore/BackupRestorePanel";
import { DeadlinePanel } from "./features/deadlines/DeadlinePanel";
import { EvidencePackPanel } from "./features/evidencePack/EvidencePackPanel";
import { EvidenceReviewPanel } from "./features/review/EvidenceReviewPanel";
import { CurrentFactsPanel } from "./features/review/CurrentFactsPanel";
import { fieldLabel } from "./features/review/presenters";
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
import { WarrantyNextStepCard } from "./features/workbench/WarrantyNextStepCard";
import { caseNextStep, hasCaseNextStep, hasWarrantyNextStep, nextStepFieldArea, type WarrantyOverviewSnapshot } from "./features/workbench/warrantyNextStep";
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
  const [importReadPending, setImportReadPending] = useState<{ caseId: string; source: SourceRecord | null } | null>(null);
  const [retryUpload, setRetryUpload] = useState(false);
  const importSequence = useRef(0);
  const importBusy = useRef(false);
  const displayedCase = useRef(caseRecord?.id); displayedCase.current = caseRecord?.id;
  useEffect(() => () => { ++importSequence.current; }, []);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseWorkbenchItem[]>([]);
  const [workbenchState, setWorkbenchState] =
    useState<WorkbenchLoadState>("loading");
  const [timelineRefreshKey, setTimelineRefreshKey] = useState(0);
  const [transitionReasons, setTransitionReasons] = useState<Record<string, string>>({});
  const transitionReason = caseRecord ? transitionReasons[caseRecord.id] ?? "" : "";
  const [transitionReads, setTransitionReads] = useState<Record<string, CaseTransitionRecord | null>>({});
  const transitionNeedsRead = Boolean(caseRecord && Object.hasOwn(transitionReads, caseRecord.id));
  const transitionBusy = useRef(false);
  const [transitionWorking, setTransitionWorking] = useState(false);
  const [activeWorkflowArea, setActiveWorkflowArea] =
    useState<WorkflowArea>("overview");
  const [pendingScrollTarget, setPendingScrollTarget] = useState<string | null>(null);
  const [aiSettingsOpen, setAiSettingsOpen] = useState(false);
  const [actionFollowUp, setActionFollowUp] = useState<ActionFollowUpTarget | null>(null);
  const [actionFocus, setActionFocus] = useState<{ caseId: string; actionId: string } | null>(null);
  const currentActionFollowUp = actionFollowUp?.caseId === caseRecord?.id ? actionFollowUp : null;
  const [preparationReviewTarget, setPreparationReviewTarget] = useState<{
    caseId: string; field: string; mode: "register" | "review" | "current";
  } | null>(null);
  const reviewTarget = preparationReviewTarget?.caseId === caseRecord?.id ? preparationReviewTarget : null;

  useEffect(() => {
    void refreshWorkbench();
    void refreshTemplates();
  }, []);

  useEffect(() => {
    const caseId = window.location.hash.replace(/^#case=/, "").trim();
    if (!caseId) {
      return;
    }
    const sequence = ++importSequence.current;
    setActivity("restoring");
    readImportCase(caseId, null)
      .then((record) => {
        if (sequence !== importSequence.current) return;
        setCaseRecord(record);
        setActiveWorkflowArea("overview");
        setTitle(record.title);
        setGoal(record.goal);
        if (isTemplateSelector(record.type)) setSelectedTemplate(record.type);
      })
      .catch((reason: unknown) => {
        if (sequence === importSequence.current) setErrorMessage(reason);
      })
      .finally(() => {
        if (sequence === importSequence.current) setActivity("idle");
      });
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
    const sequence = ++importSequence.current;
    if (importBusy.current && caseRecord) setImportReadPending(pending => pending ?? { caseId: caseRecord.id, source: null });
    importBusy.current = false;
    setActivity("restoring");
    setError(null);
    setMessage(null);
    try {
      const pendingSource = importReadPending?.caseId === caseId ? importReadPending.source : null;
      const record = await readImportCase(caseId, pendingSource);
      if (sequence !== importSequence.current) return;
      setImportReadPending(null);
      if (caseId !== caseRecord?.id || pendingSource) { setRetryUpload(false); setSelectedFile(null); }
      setCaseRecord(record);
      setActiveWorkflowArea("overview");
      setPreparationReviewTarget(null);
      setActionFollowUp(null);
      setActionFocus(null);
      setTitle(record.title);
      setGoal(record.goal);
      if (isTemplateSelector(record.type)) setSelectedTemplate(record.type);
      window.location.hash = `case=${record.id}`;
      setPendingScrollTarget(CASE_WORKFLOW_SELECTOR);
    } catch (reason) {
      if (sequence === importSequence.current) setErrorMessage(reason);
    } finally {
      if (sequence === importSequence.current) setActivity("idle");
    }
  }

  function startNewCase() {
    ++importSequence.current; importBusy.current = false;
    setImportReadPending(null); setRetryUpload(false); setActivity("idle");
    setActionFollowUp(null);
    setActionFocus(null);
    setPreparationReviewTarget(null);
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
    setPreparationReviewTarget(null);
    setActiveWorkflowArea(area);
    window.requestAnimationFrame(() => scrollToSection(".case-workflow"));
  }

  function openPreparationField(caseId: string, target: { field: string; mode: "register" | "review" | "current" }) {
    if (caseId !== caseRecord?.id) return;
    setPreparationReviewTarget({ caseId, ...target });
    setActiveWorkflowArea(nextStepFieldArea(caseRecord.type, target));
  }

  function reviewChangedAction(item: ActionRecord) {
    if (item.case_id !== caseRecord?.id) return;
    const target = changedActionTarget(item, true);
    if (!target) return;
    setActionFollowUp(target);
    selectWorkflowArea("agent");
  }

  function openAction(caseId: string, actionId: string) {
    if (caseId !== caseRecord?.id) return;
    setActionFocus({ caseId, actionId });
    selectWorkflowArea("actions");
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
    const sequence = ++importSequence.current;
    setActivity("creating");
    setError(null);
    setMessage(null);
    try {
      const created = await createCase({
        title,
        goal,
        template_id: selectedTemplate,
      });
      if (sequence === importSequence.current) {
        setCaseRecord(created);
        setActiveWorkflowArea("overview");
        window.location.hash = `case=${created.id}`;
        setMessage("案件已建立，可以导入第一份材料。");
        setPendingScrollTarget(CASE_WORKFLOW_SELECTOR);
      }
      // A saved Case must remain discoverable even after the user navigates away.
      await refreshWorkbench();
    } catch (reason) {
      if (sequence === importSequence.current) setErrorMessage(reason);
    } finally {
      if (sequence === importSequence.current) setActivity("idle");
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (importBusy.current || activity !== "idle" || importReadPending?.caseId === caseRecord?.id) return;
    if (!caseRecord || !selectedFile) {
      setError("请先建立案件并选择一个 TXT、EML 或 PDF 文件。");
      return;
    }
    setActivity("uploading");
    setUploadProgress(0);
    setError(null);
    setMessage(null);
    const caseId = caseRecord.id;
    const sequence = ++importSequence.current;
    const current = () => sequence === importSequence.current && displayedCase.current === caseId;
    importBusy.current = true;
    let imported: SourceRecord | null = null;
    try {
      imported = await uploadSource(
        caseId,
        selectedFile,
        value => { if (current()) setUploadProgress(value); },
      );
      if (!current()) return;
      const restored = await readImportCase(caseId, imported);
      if (!current()) return;
      setCaseRecord(restored);
      setSelectedFile(null);
      setRetryUpload(false);
      await refreshWorkbench();
      if (!current()) return;
      setTimelineRefreshKey((current) => current + 1);
      setMessage(
        imported.extraction.status === "completed"
          ? "材料已导入并完成文本提取。"
          : imported.extraction.status === "completed_with_notices" ? "材料已导入并提取，请查看材料提示。"
            : "材料已保存，但文本提取未完成。请按材料卡片提示处理后再人工导入。",
      );
    } catch (reason) {
      if (!current()) return;
      setRetryUpload(true);
      if (imported || importRequiresRead(reason)) {
        setImportReadPending({ caseId, source: imported });
        setError(imported ? "材料已保存，但列表暂时无法读取。请重新读取材料列表，无需再次上传。"
          : `${reason instanceof LifeCaseApiError ? errorGuidanceFor(reason.code, reason.message) : "连接或响应读取失败。"} 导入结果暂时无法确认。请先重新读取材料列表，核对是否已保存，再决定是否重试。`);
      } else setErrorMessage(reason);
    } finally {
      if (current()) { importBusy.current = false; setActivity("idle"); }
    }
  }

  async function handleReadImport() {
    if (!caseRecord || activity !== "idle" || importBusy.current) return;
    const caseId = caseRecord.id;
    const pending = importReadPending?.caseId === caseId ? importReadPending : null;
    const sequence = ++importSequence.current;
    const current = () => sequence === importSequence.current && displayedCase.current === caseId;
    importBusy.current = true; setActivity("restoring"); setError(null); setMessage(null);
    try {
      const restored = await readImportCase(caseId, pending?.source ?? null);
      if (!current()) return;
      setCaseRecord(restored); setImportReadPending(null);
      if (pending?.source) { setSelectedFile(null); setRetryUpload(false); }
      setMessage(pending?.source ? "已重新读取已保存材料，请查看提取状态。"
        : "材料列表已更新。请先核对已有记录；仍需导入时再选择文件或点击重试。");
      setTimelineRefreshKey(value => value + 1);
      await refreshWorkbench();
    } catch {
      if (current()) {
        setImportReadPending(pending ?? { caseId, source: null });
        setError("材料列表仍无法核对，当前选择与编辑保留。请恢复连接后重新读取。");
      }
    } finally {
      if (current()) { importBusy.current = false; setActivity("idle"); }
    }
  }

  async function handleCaseTransition(target: CaseStatus) {
    if (activity !== "idle" || transitionBusy.current || transitionNeedsRead) return;
    if (!caseRecord || !transitionReason.trim()) {
      setError("请先说明状态变更理由；该理由会写入案件审计历史。");
      return;
    }
    setActivity("transitioning");
    setError(null);
    setMessage(null);
    const caseId = caseRecord.id;
    const submittedReason = transitionReason;
    const sequence = ++importSequence.current;
    const current = () => sequence === importSequence.current && displayedCase.current === caseId;
    transitionBusy.current = true; setTransitionWorking(true);
    setTransitionReads(reads => ({ ...reads, [caseId]: null }));
    try {
      const saved = await transitionCase(caseId, {
        expected_version: caseRecord.version,
        target_status: target,
        actor: "local_user",
        reason: transitionReason.trim(),
      });
      setTransitionReads(reads => ({ ...reads, [caseId]: saved }));
      if (!current()) return;
      const restored = await readCaseAfterTransition(caseId, saved);
      if (!current()) return;
      setCaseRecord(restored);
      setTransitionReads(reads => { const next = { ...reads }; delete next[caseId]; return next; });
      setTransitionReasons(reasons => reasons[caseId] === submittedReason ? { ...reasons, [caseId]: "" } : reasons);
      setMessage(`案件状态已更新为“${statusLabel(restored.status)}”。`);
      setTimelineRefreshKey((current) => current + 1);
      await refreshWorkbench();
    } catch (reasonCaught) {
      if (current()) setError(reasonCaught instanceof LifeCaseApiError && reasonCaught.code === "case_version_conflict"
        ? "案件已被更新，当前理由已保留。请重新读取状态，再决定是否变更。"
        : "状态保存或读取未完成，当前理由已保留。请先重新读取案件状态，核对结果。");
    } finally {
      transitionBusy.current = false; setTransitionWorking(false);
      if (current()) setActivity("idle");
    }
  }

  async function handleReadCaseStatus() {
    if (!caseRecord || activity !== "idle" || transitionBusy.current) return;
    const caseId = caseRecord.id;
    const sequence = ++importSequence.current;
    const current = () => sequence === importSequence.current && displayedCase.current === caseId;
    transitionBusy.current = true; setTransitionWorking(true); setActivity("restoring");
    setError(null); setMessage(null);
    try {
      const restored = await readCaseAfterTransition(caseId, transitionReads[caseId] ?? null);
      if (!current()) return;
      setCaseRecord(restored);
      setTransitionReads(reads => { const next = { ...reads }; delete next[caseId]; return next; });
      setMessage(`已重新读取案件状态：“${statusLabel(restored.status)}”。请核对后决定下一步。`);
      setTimelineRefreshKey(value => value + 1);
      await refreshWorkbench();
    } catch {
      if (current()) setError("案件状态仍无法核对，当前理由已保留。请恢复连接后重新读取。");
    } finally {
      transitionBusy.current = false; setTransitionWorking(false);
      if (current()) setActivity("idle");
    }
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <h1>LifeCase</h1>
          <p className="subtitle">整理材料，处理日常事务。</p>
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
          key={caseRecord.id}
          caseTitle={caseRecord.title}
          caseStatus={statusLabel(caseRecord.status)}
          caseStatusCode={caseRecord.status}
          nextStepTitle={nextStep.title}
          nextStepDetail={nextStep.detail}
          primaryActionLabel={nextStep.actionLabel}
          onPrimaryAction={continueWorkflow}
          onReturn={startNewCase}
          recommendation={activeWorkflowArea === "overview" && hasCaseNextStep(caseRecord) ? (
            <WarrantyNextStepCard key={caseRecord.id} record={caseRecord} refreshKey={timelineRefreshKey}
              isIdle={activity === "idle"} importNeedsRead={importReadPending?.caseId === caseRecord.id}
              resolve={snapshot => workflowNextStep(caseRecord, snapshot)} onReturn={startNewCase} onSelect={selectWorkflowArea} onOpenAction={openAction}
              onOpenField={openPreparationField}
              onReadImport={() => void handleReadImport()} />
          ) : undefined}
          operations={(
            <details className="case-operations">
              <summary>案件状态与高级操作</summary>
              <div className="case-lifecycle-controls">
                <p>待办完成后，由你核对处理结果并决定案件是否解决。</p>
                <label>
                  状态变更理由
                  <input
                    value={transitionReason}
                    maxLength={1000}
                    onChange={(event) => setTransitionReasons(reasons => ({ ...reasons, [caseRecord.id]: event.target.value }))}
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
                      disabled={activity !== "idle" || transitionWorking || transitionNeedsRead || !transitionReason.trim()}
                      onClick={() => void handleCaseTransition(target)}
                    >
                      {caseTransitionLabel(target)}
                    </button>
                  ))}
                </div>
                {transitionNeedsRead && <div role="status">
                  <p>请先核对案件状态；重新读取不会重复保存。</p>
                  <button type="button" className="secondary-action" disabled={activity !== "idle" || transitionWorking}
                    onClick={() => void handleReadCaseStatus()}>重新读取案件状态</button>
                </div>}
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
              onFileChange={file => { setSelectedFile(file); setRetryUpload(false); }}
              onUpload={handleUpload}
              importNeedsRead={importReadPending?.caseId === caseRecord.id}
              retryUpload={retryUpload}
              onReadImport={() => void handleReadImport()}
            />
          )}
          agent={(
            <CaseAgentWorkspace
              caseId={caseRecord.id}
              caseTitle={null}
              caseType={caseRecord.type}
              refreshKey={timelineRefreshKey}
              onChanged={handleReviewChanged}
              actionFollowUp={currentActionFollowUp}
              onOpenAction={openAction}
            />
          )}
          review={(
            <>
            {reviewTarget && <div className="preparation-return" role="status">
              <p>{reviewTarget.mode === "register" ? "补充" : "核对"}{fieldLabel(reviewTarget.field)}：{reviewTarget.mode === "register" ? "请选择材料原文并登记候选，再明确确认。" : "请核对该字段的事实和原文，必要时修正。"}</p>
              <button type="button" onClick={() => selectWorkflowArea("actions")}>{caseRecord.type === "warranty" ? "返回准备单" : "返回行动与截止"}</button>
            </div>}
            {isTemplateSelector(caseRecord.type) && (
              <CurrentFactsPanel key={`facts-${caseRecord.id}`} caseId={caseRecord.id} caseType={caseRecord.type} refreshKey={timelineRefreshKey} onChanged={handleReviewChanged} focusField={reviewTarget?.mode === "current" ? reviewTarget.field : undefined} />
            )}
            <EvidenceReviewPanel
              key={`review-${caseRecord.id}`}
              caseId={caseRecord.id}
              caseTitle={null}
              claimFields={
                selectedTemplateItem
                  ? [...new Set([
                      ...selectedTemplateItem.required_claims,
                      ...(selectedTemplateItem.conditional_claims ?? []).map(item => item.field),
                      ...selectedTemplateItem.evidence_requirements.map(
                        (item) => item.key,
                      ),
                    ])]
                  : []
              }
              refreshKey={timelineRefreshKey}
              onReviewChanged={handleReviewChanged}
              target={reviewTarget && reviewTarget.mode !== "current" ? { field: reviewTarget.field, mode: reviewTarget.mode } : undefined}
              onImportMaterials={() => selectWorkflowArea("overview")}
            />
            </>
          )}
          actions={(
            <>
              {caseRecord.type === "warranty" && (
                <WarrantyPreparationPanel key={`preparation-${caseRecord.id}`} caseId={caseRecord.id} refreshKey={timelineRefreshKey}
                  isCaseOpen={hasWarrantyNextStep(caseRecord)} onChanged={handleReviewChanged}
                  focusDesiredOutcome={reviewTarget?.field === "desired_outcome" && reviewTarget.mode === "register"}
                  onResolveOutcome={mode => openPreparationField(caseRecord.id, { field: "desired_outcome", mode })}
                  onResolveField={field => {
                  if (!field.next) return;
                  openPreparationField(caseRecord.id, { field: field.key, mode: field.next });
                }} />
              )}
              <DeadlinePanel
                caseId={caseRecord.id}
                caseTitle={null}
                refreshKey={timelineRefreshKey}
                onChanged={handleReviewChanged}
              />
              <ActionPanel
                key={`actions-${caseRecord.id}`}
                caseId={caseRecord.id}
                caseTitle={null}
                refreshKey={timelineRefreshKey}
                onChanged={handleReviewChanged}
                onReviewChangedBasis={reviewChangedAction}
                caseType={caseRecord.type}
                requiredClaimFields={selectedTemplateItem?.required_claims ?? null}
                onReviewFacts={() => selectWorkflowArea("review")}
                focusActionId={actionFocus?.caseId === caseRecord.id ? actionFocus.actionId : undefined}
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
                key={`timeline-${caseRecord.id}`}
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
              <LazyWorkflowTool title="备份与恢复" hint="查看备份或恢复已有数据">
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

const CASE_LIFECYCLE_GUIDANCE = {
  needs_review: {
    number: "03",
    title: "检查事实与材料缺口",
    detail: "案件处于待复核状态；核对事实及其来源，再决定是否推进。",
    area: "review",
    actionLabel: "进入事实复核",
  },
  ready: {
    number: "04",
    title: "安排后续行动",
    detail: "案件已标记为就绪；查看已有行动和截止日期，决定下一步处理。",
    area: "actions",
    actionLabel: "查看行动与截止",
  },
  in_progress: {
    number: "04",
    title: "继续处理已有行动",
    detail: "案件正在处理中；查看行动进度与截止日期，再更新处理进展。",
    area: "actions",
    actionLabel: "查看行动与截止",
  },
  waiting_external: {
    number: "04",
    title: "跟进外部回复",
    detail: "案件正在等待外部回复；查看已有行动与截止日期，收到回复后再更新进展。",
    area: "actions",
    actionLabel: "查看跟进与截止",
  },
  blocked: {
    number: "04",
    title: "检查阻塞原因",
    detail: "查看状态变更记录中的阻塞原因，确认继续处理所需的条件。",
    area: "records",
    actionLabel: "查看阻塞记录",
  },
  resolved: {
    number: "05",
    title: "核对处理结果",
    detail: "案件已标记为解决；核对进展记录，确认后可在状态操作中归档。",
    area: "records",
    actionLabel: "查看结案记录",
  },
  abandoned: {
    number: "05",
    title: "查看放弃记录",
    detail: "案件已放弃，不代表问题已解决；可查阅原因，并在状态操作中归档。",
    area: "records",
    actionLabel: "查看结案记录",
  },
  archived: {
    number: "05",
    title: "查阅归档记录",
    detail: "案件已归档；可查阅历史、证据包与备份，不再提示继续推进。",
    area: "records",
    actionLabel: "查看归档记录",
  },
} satisfies Record<
  Exclude<CaseStatus, "intake">,
  { number: string; title: string; detail: string; area: WorkflowArea; actionLabel: string }
>;

export function workflowNextStep(record: CaseRecord | null, warranty?: WarrantyOverviewSnapshot): {
  number: string; title: string; detail: string; target: string; area: WorkflowArea | null;
  actionLabel: string; actionId?: string;
} {
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
  if (warranty) {
    const step = caseNextStep(record, warranty);
    if (step) return { ...step, number: "04", target: CASE_WORKFLOW_SELECTOR };
  }
  if (record.status !== "intake") {
    return {
      ...CASE_LIFECYCLE_GUIDANCE[record.status],
      target: CASE_WORKFLOW_SELECTOR,
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

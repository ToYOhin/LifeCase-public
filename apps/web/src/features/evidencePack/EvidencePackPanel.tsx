import { useEffect, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { createEvidencePack, getLatestEvidencePack } from "./api";
import { evidencePackView, packSizeLabel, shortPackHash } from "./presenters";
import type { EvidencePackArtifact, EvidencePackLoadState } from "./types";

interface EvidencePackPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  refreshKey: number;
  onCreated: () => void;
}

export function EvidencePackPanel({
  caseId,
  caseTitle,
  refreshKey,
  onCreated,
}: EvidencePackPanelProps) {
  const [artifact, setArtifact] = useState<EvidencePackArtifact | null>(null);
  const [state, setState] = useState<EvidencePackLoadState>("idle");

  useEffect(() => {
    if (!caseId) {
      setArtifact(null);
      setState("idle");
      return;
    }
    void load(caseId);
  }, [caseId, refreshKey]);

  async function load(targetCaseId: string) {
    setState("loading");
    try {
      const recovered = await getLatestEvidencePack(targetCaseId);
      setArtifact(recovered);
      setState(recovered ? "ready" : "empty");
    } catch (reason) {
      setArtifact(null);
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function create() {
    if (!caseId) return;
    setState("creating");
    try {
      setArtifact(await createEvidencePack(caseId));
      setState("ready");
      onCreated();
    } catch (reason) {
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  const view = evidencePackView(caseId, state);
  return (
    <section className="evidence-pack" aria-labelledby="evidence-pack-title">
      <div className="evidence-pack-head">
        <div>
          <p className="section-label">PORTABLE RECORD</p>
          <h2 id="evidence-pack-title">证据包</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <span className="evidence-pack-format">M6 · FORMAT 1</span>
      </div>

      {view === "unselected" && <p className="evidence-pack-state">尚未选择案件。</p>}
      {view === "loading" && <p className="evidence-pack-state" role="status">正在恢复已保存的证据包…</p>}
      {view === "creating" && <p className="evidence-pack-state" role="status">正在冻结权威快照并校验每个成员…</p>}
      {view === "empty" && (
        <div className="evidence-pack-state">
          <p>当前案件还没有证据包。生成过程不会发送材料或调用外部服务。</p>
          <button type="button" onClick={() => void create()}>生成证据包</button>
        </div>
      )}
      {view === "offline" && (
        <div className="evidence-pack-state" role="alert">
          <p>本地 API 未连接；已保存的证据包没有丢失。</p>
          <button type="button" onClick={() => caseId && void load(caseId)}>重新连接</button>
        </div>
      )}
      {view === "failure" && (
        <div className="evidence-pack-state" role="alert">
          <p>证据包无法安全读取或生成。系统没有提供未经校验的下载。</p>
          <button type="button" onClick={() => caseId && void load(caseId)}>重新校验</button>
        </div>
      )}
      {view === "ready" && artifact && (
        <article className="evidence-pack-card">
          <div className="evidence-pack-layers" aria-label="证据包四层内容">
            <span>原始材料</span>
            <span>派生产物</span>
            <span>系统生成</span>
            <span>用户确认</span>
          </div>
          <div>
            <strong>{artifact.filename}</strong>
            <span>SHA-256 <code title={artifact.sha256}>{shortPackHash(artifact.sha256)}</code></span>
            <span>格式 {artifact.format_version} · {packSizeLabel(artifact.size_bytes)}</span>
          </div>
          <div className="evidence-pack-actions">
            <a href={artifact.download_url} download={artifact.filename}>下载并保存</a>
            <button type="button" onClick={() => void create()}>重新生成</button>
          </div>
          <p>包含 original、derived、generated、confirmed 四层；manifest 与 checksums 可独立复核。</p>
        </article>
      )}
    </section>
  );
}

import { useEffect, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { applyRestore, createBackup, getLatestBackup, validateBackup } from "./api";
import { backupSizeLabel, shortBackupHash } from "./presenters";
import type {
  BackupArtifact,
  BackupRestoreState,
  RestoreResult,
  RestoreValidation,
} from "./types";

export function BackupRestorePanel() {
  const [artifact, setArtifact] = useState<BackupArtifact | null>(null);
  const [selected, setSelected] = useState<File | null>(null);
  const [validation, setValidation] = useState<RestoreValidation | null>(null);
  const [result, setResult] = useState<RestoreResult | null>(null);
  const [targetName, setTargetName] = useState("restored-copy");
  const [confirmed, setConfirmed] = useState(false);
  const [state, setState] = useState<BackupRestoreState>("loading");

  useEffect(() => {
    void load();
  }, []);

  function fail(reason: unknown) {
    setState(
      reason instanceof LifeCaseApiError && reason.code === "network_error"
        ? "offline"
        : "error",
    );
  }

  async function load() {
    setState("loading");
    try {
      const latest = await getLatestBackup();
      setArtifact(latest);
      setState(latest ? "ready" : "empty");
    } catch (reason) {
      fail(reason);
    }
  }

  async function create() {
    setState("creating");
    try {
      setArtifact(await createBackup());
      setState("ready");
    } catch (reason) {
      fail(reason);
    }
  }

  async function validate() {
    if (!selected) return;
    setState("validating");
    setValidation(null);
    setResult(null);
    try {
      setValidation(await validateBackup(selected));
      setState("needs_confirmation");
    } catch (reason) {
      fail(reason);
    }
  }

  async function restore() {
    if (!validation || !confirmed) return;
    setState("restoring");
    try {
      setResult(await applyRestore(validation.validation_id, targetName));
      setState("success");
    } catch (reason) {
      fail(reason);
    }
  }

  return (
    <section className="backup-restore" aria-labelledby="backup-restore-title">
      <div className="backup-restore-head">
        <div>
          <p className="section-label">LOCAL RECOVERY</p>
          <h2 id="backup-restore-title">备份与恢复</h2>
          <p>全库备份包含 SQLite 权威快照和被引用文件；Evidence Pack 仍是案件导出。</p>
        </div>
        <span>FORMAT 1 · SHA-256</span>
      </div>

      <div className="backup-columns">
        <article>
          <h3>创建备份</h3>
          {state === "loading" && <p role="status">正在读取已保留的本地备份…</p>}
          {state === "creating" && <p role="status">正在创建一致快照并逐项校验…</p>}
          {!artifact && state !== "loading" && state !== "creating" && (
            <p>尚无已验证备份。</p>
          )}
          {artifact && (
            <div className="backup-record">
              <strong>{artifact.filename}</strong>
              <span>{artifact.case_count} 个案件 · {artifact.member_count} 个成员</span>
              <span>{backupSizeLabel(artifact.size_bytes)} · v{artifact.format_version}</span>
              <code title={artifact.sha256}>{shortBackupHash(artifact.sha256)}</code>
              <a href={artifact.download_url} download={artifact.filename}>下载备份</a>
            </div>
          )}
          <button type="button" onClick={() => void create()} disabled={["creating", "validating", "restoring"].includes(state)}>
            {artifact ? "创建新快照" : "创建首个备份"}
          </button>
        </article>

        <article>
          <h3>恢复到新目录</h3>
          <label className="backup-file-picker">
            <span>{selected?.name ?? "选择 LifeCase Backup ZIP"}</span>
            <input
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => {
                setSelected(event.target.files?.[0] ?? null);
                setValidation(null);
                setConfirmed(false);
                setState(event.target.files?.[0] ? "selected" : artifact ? "ready" : "empty");
              }}
              disabled={state === "validating" || state === "restoring"}
            />
          </label>
          {selected && !validation && (
            <button type="button" onClick={() => void validate()} disabled={state === "validating"}>
              {state === "validating" ? "校验中…" : "独立校验"}
            </button>
          )}
          {validation && (
            <div className="restore-confirmation">
              <strong>校验通过</strong>
              <span>{validation.case_count} 个案件 · 数据库 {shortBackupHash(validation.database_sha256)}</span>
              <label>
                新运行目录标识
                <input value={targetName} maxLength={80} onChange={(event) => setTargetName(event.target.value)} />
              </label>
              <label className="restore-check">
                <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                我确认只恢复到一个全新目录，不覆盖、合并或删除现有数据。
              </label>
              <button type="button" onClick={() => void restore()} disabled={!confirmed || !targetName || state === "restoring"}>
                {state === "restoring" ? "恢复中…" : "恢复已验证备份"}
              </button>
            </div>
          )}
          {state === "success" && result && (
            <p className="backup-success">恢复完成：{result.runtime_id}（{result.case_count} 个案件）</p>
          )}
          {state === "offline" && <p role="alert">本地 API 未连接；没有写入恢复目标。</p>}
          {state === "error" && <p role="alert">备份或恢复失败；未验证内容不会发布。</p>}
        </article>
      </div>
    </section>
  );
}

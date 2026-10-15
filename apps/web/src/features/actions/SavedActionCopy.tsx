import { useEffect, useRef, useState } from "react";
import { copySavedActionDraft, hasSavedActionDraft, type SavedActionCopyResult } from "./savedActionCopyState";
import type { ActionRecord } from "./types";

const messages: Record<Exclude<SavedActionCopyResult, "superseded">, string> = {
  copied: "正文已复制；未发送，待办与审批未改变。",
  changed: "待办已更新，尚未复制。请重新读取并核对正文后再复制。",
  "basis-unavailable": "依据已变化或无法核对，尚未复制。请重新读取；依据变化时先重新分析。",
  "read-failed": "暂时无法核对最新待办，尚未复制。请恢复连接后重试。",
  "clipboard-failed": "浏览器未能写入剪贴板，尚未确认复制成功。请重试。",
};

export function SavedActionCopy({ item, available, onReload }: {
  item: ActionRecord; available: boolean; onReload: () => void;
}) {
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState<SavedActionCopyResult | null>(null);
  const sequence = useRef(0);
  const busy = useRef(false);
  const key = JSON.stringify([item.case_id, item.id, item.version, item.status, item.payload_sha256,
    item.payload, item.communication_draft, item.analysis_basis_status, available]);
  const target = useRef(key); target.current = key;
  useEffect(() => {
    ++sequence.current; busy.current = false; setWorking(false); setResult(null);
    return () => { ++sequence.current; busy.current = false; };
  }, [key]);

  async function copy() {
    if (!available || busy.current || item.analysis_basis_status !== "current") return;
    const generation = ++sequence.current;
    const current = () => sequence.current === generation && target.current === key;
    busy.current = true; setWorking(true); setResult(null);
    try {
      const next = await copySavedActionDraft(item, current, text => navigator.clipboard.writeText(text));
      if (current() && next !== "superseded") setResult(next);
    } finally {
      if (current()) { busy.current = false; setWorking(false); }
    }
  }

  if (!hasSavedActionDraft(item)) return null;
  return <div className="action-draft-copy" aria-busy={working}>
    <div className="action-controls">
      <button type="button" disabled={!available || working || item.analysis_basis_status !== "current"}
        title="重新核对当前版本与依据后，复制已保存正文" onClick={() => void copy()}>
        {working ? "正在核对…" : "复制已保存正文"}
      </button>
      {(result === "changed" || result === "basis-unavailable" || result === "read-failed") &&
        <button type="button" disabled={!available || working} onClick={onReload}>重新读取待办</button>}
    </div>
    {result && result !== "superseded" && <p role={result === "copied" ? "status" : "alert"}>{messages[result]}</p>}
  </div>;
}

import { useEffect, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { getPreparationIntent, savePreparationIntent, type PreparationIntent } from "./intentApi";

interface Editor {
  caseId: string;
  input: string;
  saved: PreparationIntent | null;
  incoming: PreparationIntent | null;
  status: "loading" | "ready" | "saving" | "reading" | "reload-required";
  error: string | null;
  message: string | null;
}

function emptyEditor(caseId: string): Editor {
  return { caseId, input: "", saved: null, incoming: null, status: "loading", error: null, message: null };
}

export function usePreparationIntent(caseId: string) {
  const [editor, setEditor] = useState<Editor>(() => emptyEditor(caseId));
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const working = useRef(false);
  const current = editor.caseId === caseId ? editor : emptyEditor(caseId);
  const busy = ["loading", "saving", "reading"].includes(current.status);
  const dirty = current.saved !== null && current.input !== current.saved.intent;

  useEffect(() => {
    const generation = ++sequence.current;
    const next = new AbortController();
    controller.current = next;
    working.current = true;
    setEditor(emptyEditor(caseId));
    void getPreparationIntent(caseId, next.signal).then((saved) => {
      if (generation !== sequence.current || next.signal.aborted) return;
      setEditor({ ...emptyEditor(caseId), input: saved.intent, saved, status: "ready" });
    }).catch(() => {
      if (generation !== sequence.current || next.signal.aborted) return;
      setEditor({ ...emptyEditor(caseId), status: "reload-required", error: "已保存诉求读取失败。请重新读取，确认结果后再编辑或保存。" });
    }).finally(() => { if (generation === sequence.current) working.current = false; });
    return () => { ++sequence.current; next.abort(); controller.current?.abort(); };
  }, [caseId]);

  function update(input: string) {
    if (working.current || current.saved === null) return;
    setEditor((state) => ({ ...state, input, message: null }));
  }

  async function save() {
    if (working.current || current.status !== "ready" || !current.saved || !dirty) return;
    const generation = ++sequence.current;
    const next = new AbortController();
    controller.current?.abort(); controller.current = next;
    working.current = true;
    setEditor((state) => ({ ...state, status: "saving", error: null, message: null }));
    try {
      const saved = await savePreparationIntent(caseId, current.input, current.saved.version, next.signal);
      if (generation !== sequence.current || next.signal.aborted) return;
      setEditor({ ...emptyEditor(caseId), input: saved.intent, saved, status: "ready", message: "诉求已保存。事实与来源会继续按当前记录重新核对。" });
    } catch (error) {
      if (generation !== sequence.current || next.signal.aborted) return;
      const message = error instanceof LifeCaseApiError && error.code === "preparation_intent_version_conflict"
        ? "其他窗口已保存新的诉求。你的输入已保留，请读取最新内容后选择。"
        : "保存结果未能确认。你的输入已保留，请先读取最新已保存诉求，避免重复覆盖。";
      setEditor((state) => ({ ...state, status: "reload-required", error: message }));
    } finally { if (generation === sequence.current) working.current = false; }
  }

  async function readLatest() {
    if (working.current) return;
    const generation = ++sequence.current;
    const next = new AbortController();
    controller.current?.abort(); controller.current = next;
    working.current = true;
    setEditor((state) => ({ ...state, status: "reading", incoming: null, error: null, message: null }));
    try {
      const incoming = await getPreparationIntent(caseId, next.signal);
      if (generation !== sequence.current || next.signal.aborted) return;
      setEditor(state => {
        // A caller may have begun another request before these edits were made.
        // Decide from the latest input, never from that caller's render snapshot.
        const hasEdits = state.saved !== null && state.input !== state.saved.intent;
        if (!hasEdits || incoming.intent === state.input) {
          return { ...emptyEditor(caseId), input: incoming.intent, saved: incoming, status: "ready", message: "已读取当前保存的诉求。" };
        }
        return { ...state, incoming, status: "reload-required", message: "请比较已保存内容与当前输入，再选择要继续使用的内容。" };
      });
    } catch {
      if (generation !== sequence.current || next.signal.aborted) return;
      setEditor((state) => ({ ...state, status: "reload-required", error: "最新诉求读取失败。当前输入仍保留，请稍后重试。" }));
    } finally { if (generation === sequence.current) working.current = false; }
  }

  function chooseIncoming(useSaved: boolean) {
    if (working.current || !current.incoming) return;
    setEditor({ ...current, saved: current.incoming, input: useSaved ? current.incoming.intent : current.input,
      incoming: null, status: "ready", error: null,
      message: useSaved ? "已使用保存的诉求。" : "已保留你的输入；再次点击“保存诉求”才会替换已保存内容。" });
  }

  return { ...current, busy, dirty, update, save, readLatest, chooseIncoming };
}

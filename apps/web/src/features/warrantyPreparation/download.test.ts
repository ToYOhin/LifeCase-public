import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadPreparation } from "./download";

function browser() {
  const anchor = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
  const append = vi.fn();
  const create = vi.fn((_blob: Blob) => "blob:synthetic-sheet");
  const revoke = vi.fn();
  vi.stubGlobal("document", { createElement: () => anchor, body: { append } });
  vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
  vi.useFakeTimers();
  return { anchor, append, create, revoke };
}

afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("准备单本地 TXT 下载", () => {
  it("downloads exactly the displayed Chinese text as UTF-8 with BOM and releases browser resources", async () => {
    const { anchor, append, create, revoke } = browser();
    const text = "中文报修准备单\n产品：海星 🧺（人工修正）\n本人诉求：检测\n请说明费用。";
    expect(downloadPreparation(text, "case_synthetic")).toBe(true);
    const blob = create.mock.calls[0]![0] as unknown as Blob;
    expect(blob.type).toBe("text/plain;charset=utf-8");
    const bytes = new Uint8Array(await blob.arrayBuffer());
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(new TextDecoder().decode(bytes.slice(3))).toBe(text);
    expect(anchor.href).toBe("blob:synthetic-sheet");
    expect(anchor.download).toBe("中文报修准备单-case_synthetic.txt");
    expect(append).toHaveBeenCalledWith(anchor);
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(anchor.remove).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:synthetic-sheet");
  });

  it("keeps the filename bounded and free of path separators and Windows reserved characters", () => {
    const { anchor } = browser();
    expect(downloadPreparation("合成", '../a\\b:*?\"<>|'.repeat(20))).toBe(true);
    expect(anchor.download).not.toMatch(/[\\/:*?"<>|]/);
    expect(anchor.download.length).toBeLessThan(110);
    expect(anchor.download.endsWith(".txt")).toBe(true);
  });

  it.each(["blob", "url", "append", "click"])("reports %s failure without claiming a download and cleans up allocated resources", failure => {
    const { anchor, append, create, revoke } = browser();
    const fail = () => { throw new Error("Synthetic browser failure"); };
    if (failure === "blob") vi.stubGlobal("Blob", class { constructor() { fail(); } });
    if (failure === "url") create.mockImplementation(fail);
    if (failure === "append") append.mockImplementation(fail);
    if (failure === "click") anchor.click.mockImplementation(fail);
    expect(downloadPreparation("合成", "case_a")).toBe(false);
    if (failure !== "click") expect(anchor.click).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledTimes(failure === "blob" || failure === "url" ? 0 : 1);
    if (failure === "append" || failure === "click") expect(anchor.remove).toHaveBeenCalledOnce();
  });
});

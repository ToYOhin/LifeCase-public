/** Starts a browser download; the browser owns the final save/cancel decision. */
export function downloadPreparation(text: string, caseId: string): boolean {
  let url: string | undefined;
  let anchor: HTMLAnchorElement | undefined;
  try {
    const file = new Blob(["\uFEFF", text], { type: "text/plain;charset=utf-8" });
    url = URL.createObjectURL(file);
    anchor = document.createElement("a");
    anchor.href = url;
    const filenameCase = caseId.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80) || "case";
    anchor.download = `中文报修准备单-${filenameCase}.txt`;
    document.body.append(anchor);
    anchor.click();
    return true;
  } catch {
    return false;
  } finally {
    anchor?.remove();
    // Let the browser consume the URL before releasing it, including on failure.
    const downloadUrl = url;
    if (downloadUrl) setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
  }
}

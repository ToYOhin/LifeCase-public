import type { BackupRestoreState } from "./types";

export function backupRestoreView(state: BackupRestoreState): BackupRestoreState {
  return state;
}

export function backupSizeLabel(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KiB`;
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MiB`;
}

export function shortBackupHash(value: string): string {
  return value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value;
}

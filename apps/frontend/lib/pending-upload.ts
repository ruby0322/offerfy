const pendingUploads = new Map<string, File>();

export function stashPendingUpload(resumeId: string, file: File): void {
  pendingUploads.set(resumeId, file);
}

export function peekPendingUpload(resumeId: string): File | null {
  return pendingUploads.get(resumeId) ?? null;
}

export function takePendingUpload(resumeId: string): File | null {
  const file = pendingUploads.get(resumeId) ?? null;
  if (file) pendingUploads.delete(resumeId);
  return file;
}

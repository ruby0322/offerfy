const pendingKickstarts = new Set<string>();

export function queueTailorKickstart(resumeId: string): void {
  pendingKickstarts.add(resumeId);
}

export function peekTailorKickstart(resumeId: string): boolean {
  return pendingKickstarts.has(resumeId);
}

export function takeTailorKickstart(resumeId: string): boolean {
  const queued = pendingKickstarts.has(resumeId);
  if (queued) pendingKickstarts.delete(resumeId);
  return queued;
}

type Progress = { assetId: string; name: string };

function progressKey(projectId: string): string | null {
  const user = JSON.parse(localStorage.getItem('user') || 'null');
  return Number.isInteger(user?.id) && Number.isInteger(user?.tenant_id)
    ? `annotation-progress:${user.tenant_id}:${user.id}:${projectId}` : null;
}

export function readAnnotationProgress(projectId: string): Progress | null {
  try {
    const key = progressKey(projectId);
    const value = key && JSON.parse(localStorage.getItem(key) || 'null');
    return value && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.assetId)
      && typeof value.name === 'string' ? value : null;
  } catch { return null; }
}

export function rememberAnnotationProgress(asset: { id: string; project_id: string; original_name: string }): boolean {
  try {
    const key = progressKey(asset.project_id);
    if (!key) return false;
    localStorage.setItem(key, JSON.stringify({ assetId: asset.id, name: asset.original_name }));
    return true;
  } catch { return false; }
}

export function clearAnnotationProgress(projectId: string) {
  try {
    const key = progressKey(projectId);
    if (key) localStorage.removeItem(key);
  } catch { /* Browser storage may be disabled. */ }
}

export type Box = { x: number; y: number; width: number; height: number; label: string };
export type Draft = { boxes: Box[]; negative: boolean };
export type DraftHistory = { past: Draft[]; present: Draft; future: Draft[] };
export type View = { x: number; y: number; width: number; height: number };
export const draftHistory = (present: Draft): DraftHistory => ({ past: [], present, future: [] });
export function changeDraft(history: DraftHistory, next: Draft): DraftHistory {
  if (JSON.stringify(history.present) === JSON.stringify(next)) return history;
  return { past: [...history.past, history.present].slice(-100), present: next, future: [] };
}
export function undoDraft(history: DraftHistory): DraftHistory {
  if (!history.past.length) return history;
  return { past: history.past.slice(0, -1), present: history.past.at(-1)!, future: [history.present, ...history.future] };
}
export function redoDraft(history: DraftHistory): DraftHistory {
  if (!history.future.length) return history;
  return { past: [...history.past, history.present], present: history.future[0], future: history.future.slice(1) };
}
export function hitBoxes(boxes: Box[], point: { x: number; y: number }) {
  return boxes.map((box, i) => ({ box, i })).filter(({ box }) =>
    point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height)
    .sort((a, b) => a.box.width * a.box.height - b.box.width * b.box.height || b.i - a.i).map(({ i }) => i);
}
export function duplicatePairs(boxes: Box[]) {
  const pairs: [number, number][] = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.label !== b.label) continue;
    const intersection = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)) *
      Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
    const union = a.width * a.height + b.width * b.height - intersection;
    if (union > 0 && intersection / union >= .9) pairs.push([i, j]);
  }
  return pairs;
}
export function zoomView(view: View, point: { x: number; y: number }, factor: number, width: number, height: number): View {
  const scale = Math.max(.2, Math.min(8, width / view.width * factor));
  const w = width / scale, h = height / scale;
  return { x: point.x - (point.x - view.x) * w / view.width, y: point.y - (point.y - view.y) * h / view.height, width: w, height: h };
}

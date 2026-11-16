import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent,ReactNode } from 'react';
import { Button, Space, Tag } from 'antd';
import { hitBoxes, zoomView, labelColor } from './detectionEditor';
import type { Box, View } from './detectionEditor';

type Props = {
  image: string; width: number; height: number; boxes: Box[]; selected: number; label: string;
  tool: 'draw' | 'select'; disabled: boolean; labels?: string[]; ariaLabel?:string; toolbarExtra?:ReactNode; onZoomIn?:()=>void;
  onSelect: (index: number) => void; onChange: (boxes: Box[]) => void; onDragging: (value: boolean) => void;
};
type Drag = { pointerId: number; mode: 'pan' | 'draw' | 'move' | 'resize'; handle: string | null; start: { x: number; y: number }; view: View; inverse: DOMMatrix; index: number; before: Box[] };
export default function DetectionCanvas({ image, width, height, boxes, selected, label, tool, disabled, labels, ariaLabel='检测画框区域', toolbarExtra, onZoomIn, onSelect, onChange, onDragging }: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<Drag | null>(null);
  const preview = useRef<Box[] | null>(null);
  const space = useRef(false);
  const [previewBoxes, setPreviewBoxes] = useState<Box[] | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, width, height });
  const point = (x: number, y: number) => {
    const p = svg.current!.createSVGPoint(); p.x = x; p.y = y;
    return p.matrixTransform(svg.current!.getScreenCTM()!.inverse());
  };
  const clamp = (p: { x: number; y: number }) => ({ x: Math.max(0, Math.min(width, p.x)), y: Math.max(0, Math.min(height, p.y)) });
  const cancel = useCallback(() => {
    const current = drag.current;
    if (current?.mode === 'pan') setView(current.view);
    drag.current = null; preview.current = null; setPreviewBoxes(null); onDragging(false);
    if (current && svg.current?.hasPointerCapture(current.pointerId)) svg.current.releasePointerCapture(current.pointerId);
  }, [onDragging]);
  useEffect(() => {
    const element = svg.current!;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      if (drag.current) return;
      if(event.deltaY<0)onZoomIn?.();
      setView(current => zoomView(current, point(event.clientX, event.clientY), event.deltaY > 0 ? 1 / 1.15 : 1.15, width, height));
    };
    element.addEventListener('wheel', wheel, { passive: false });
    const release = () => { space.current = false; };
    const blur = () => { release(); cancel(); };
    window.addEventListener('keyup', release); window.addEventListener('blur', blur);
    return () => { element.removeEventListener('wheel', wheel); window.removeEventListener('keyup', release); window.removeEventListener('blur', blur); };
  }, [width, height, cancel, onZoomIn]);
  const begin = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (drag.current) { if (event.pointerId !== drag.current.pointerId) cancel(); return; }
    if (!event.isPrimary || ![0, 1].includes(event.button)) return;
    svg.current!.focus(); event.preventDefault();
    const raw = point(event.clientX, event.clientY), p = clamp(raw);
    const before = boxes.map(b => ({ ...b }));
    const handle = (event.target as Element).getAttribute('data-resize');
    let mode: Drag['mode'], index = selected;
    if (event.ctrlKey || event.metaKey || space.current || event.button === 1 || (disabled && event.pointerType === 'touch')) mode = 'pan';
    else {
      if (disabled) return;
      const hits = hitBoxes(boxes, raw);
      if (event.altKey && hits.length) {
        onSelect(hits[(hits.indexOf(selected) + 1) % hits.length]); return;
      }
      if (handle && !event.shiftKey) mode = 'resize';
      else if (event.shiftKey || (tool === 'draw' && !hits.length)) {
        if (!label || raw.x < 0 || raw.y < 0 || raw.x > width || raw.y > height) return;
        mode = 'draw'; index = boxes.length;
        before.push({ x: p.x, y: p.y, width: 0, height: 0, label }); onSelect(index);
      } else if (hits.length) { mode = 'move'; index = hits[0]; onSelect(index); }
      else { onSelect(-1); return; }
    }
    drag.current = { pointerId: event.pointerId, mode, handle, index, start: mode === 'pan' || mode === 'resize' ? raw : p, view, inverse: svg.current!.getScreenCTM()!.inverse(), before };
    svg.current!.setPointerCapture(event.pointerId); onDragging(true);
  };
  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current; if (!d || d.pointerId !== event.pointerId) return;
    const raw = point(event.clientX, event.clientY);
    if (d.mode === 'pan') {
      const fixed = svg.current!.createSVGPoint(); fixed.x = event.clientX; fixed.y = event.clientY;
      const local = fixed.matrixTransform(d.inverse);
      setView({ ...d.view, x: d.view.x + d.start.x - local.x, y: d.view.y + d.start.y - local.y }); return;
    }
    const p = clamp(raw), b = { ...d.before[d.index] };
    if (d.mode === 'draw') {
      b.x = Math.min(p.x, d.start.x); b.y = Math.min(p.y, d.start.y);
      b.width = Math.abs(p.x - d.start.x); b.height = Math.abs(p.y - d.start.y);
    } else if (d.mode === 'move') {
      b.x = Math.max(0, Math.min(width - b.width, b.x + p.x - d.start.x));
      b.y = Math.max(0, Math.min(height - b.height, b.y + p.y - d.start.y));
    } else {
      const dx = raw.x - d.start.x, dy = raw.y - d.start.y;
      const right = b.x + b.width, bottom = b.y + b.height;
      if (d.handle?.includes('w')) { b.x = Math.max(0, Math.min(right - 1, b.x + dx)); b.width = right - b.x; }
      if (d.handle?.includes('e')) b.width = Math.max(1, Math.min(width - b.x, b.width + dx));
      if (d.handle?.includes('n')) { b.y = Math.max(0, Math.min(bottom - 1, b.y + dy)); b.height = bottom - b.y; }
      if (d.handle?.includes('s')) b.height = Math.max(1, Math.min(height - b.y, b.height + dy));
    }
    preview.current = d.before.map((box, i) => i === d.index ? b : box); setPreviewBoxes(preview.current);
  };
  const finish = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current; if (!d || d.pointerId !== event.pointerId) return;
    if (d.mode !== 'pan' && preview.current) {
      const next = preview.current.filter(b => b.width >= 1 && b.height >= 1);
      onChange(next); if (d.index >= next.length) onSelect(-1);
    }
    drag.current = null; preview.current = null; setPreviewBoxes(null); onDragging(false);
    if (svg.current!.hasPointerCapture(event.pointerId)) svg.current!.releasePointerCapture(event.pointerId);
  };
  const displayed = previewBoxes || boxes;
  const active = displayed[selected];
  const stroke = Math.max(view.width / 450, 1), handle = Math.max(view.width / 80, 1);
  return <div className="detection-canvas">
    <Space wrap style={{ marginBottom: 12 }}>
      <Button aria-label="缩小图片" onClick={() => setView(v => zoomView(v, { x: v.x + v.width / 2, y: v.y + v.height / 2 }, 1 / 1.2, width, height))}>−</Button>
      <Tag aria-label="图片缩放比例" title="相对完整适应标注区的缩放比例">{view.x===0&&view.y===0&&view.width===width&&view.height===height?'适应 · ':''}{Math.round(width / view.width * 100)}%</Tag>
      <Button aria-label="放大图片" onClick={() => {onZoomIn?.();setView(v => zoomView(v, { x: v.x + v.width / 2, y: v.y + v.height / 2 }, 1.2, width, height));}}>+</Button>
      <Button onClick={() => setView({ x: 0, y: 0, width, height })}>适应图片</Button>
      {toolbarExtra}
    </Space>
    <svg ref={svg} className="workspace-canvas" aria-label={ariaLabel} role="img" tabIndex={0}
      viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
      style={{ width: '100%', aspectRatio: `${width} / ${height}`, maxHeight: '70dvh', display: 'block', marginInline: 'auto', background: '#111', touchAction: 'none', cursor: tool === 'draw' ? 'crosshair' : 'default' }}
      onPointerDown={begin} onPointerMove={move} onPointerUp={finish} onPointerCancel={event=>{if(drag.current?.pointerId===event.pointerId)cancel();}} onLostPointerCapture={event=>{if(drag.current?.pointerId===event.pointerId)cancel();}}
      onKeyDown={event => {
        if (event.code === 'Space') { event.preventDefault(); space.current = true; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); onSelect(-1); }
        if (event.key === 'Tab' && boxes.length && !event.ctrlKey && !event.altKey) {
          const next = selected < 0 ? (event.shiftKey ? boxes.length - 1 : 0) : selected + (event.shiftKey ? -1 : 1);
          if (next >= 0 && next < boxes.length) { event.preventDefault(); onSelect(next); }
          else onSelect(-1);
        }
      }}>
      <image href={image} width={width} height={height} preserveAspectRatio="none" />
      {displayed.map((b, i) => <g key={i}>
        <rect x={b.x} y={b.y} width={b.width} height={b.height} fill="none" stroke="#000" strokeWidth={(i === selected ? 2 : 1) * stroke + 4 * stroke} />
        <rect x={b.x} y={b.y} width={b.width} height={b.height} fill="none" stroke="#fff" strokeWidth={(i === selected ? 2 : 1) * stroke + 2 * stroke} />
        <rect x={b.x} y={b.y} width={b.width} height={b.height} fill="rgba(22,119,255,.08)" stroke={labelColor(b.label, labels)} strokeWidth={i === selected ? stroke * 2 : stroke} />
        <text x={b.x + stroke * 2} y={Math.max(stroke * 10, b.y - stroke * 3)} fill="#fff" stroke="#000" strokeWidth={stroke * 1.6} paintOrder="stroke" strokeLinejoin="round" fontSize={stroke * 8} pointerEvents="none">{i + 1} · {b.label}</text>
      </g>)}
      {active && !disabled && ([['nw',0,0],['n',.5,0],['ne',1,0],['e',1,.5],['se',1,1],['s',.5,1],['sw',0,1],['w',0,.5]] as const).map(([direction,x,y]) =>
        <rect key={direction} data-resize={direction} aria-label={`调整框 ${direction}`} x={active.x + active.width * x - handle / 2} y={active.y + active.height * y - handle / 2}
          width={handle} height={handle} fill="#ffff00" style={{cursor:`${direction}-resize`}} />)}
    </svg>
  </div>;
}

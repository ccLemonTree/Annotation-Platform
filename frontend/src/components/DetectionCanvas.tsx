import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Button, Space, Tag } from 'antd';
import { hitBoxes, zoomView } from './detectionEditor';
import type { Box, View } from './detectionEditor';

type Props = {
  image: string; width: number; height: number; boxes: Box[]; selected: number; label: string;
  tool: 'draw' | 'select'; disabled: boolean;
  onSelect: (index: number) => void; onChange: (boxes: Box[]) => void; onDragging: (value: boolean) => void;
};
type Drag = { mode: 'pan' | 'draw' | 'move' | 'resize'; start: { x: number; y: number }; view: View; inverse: DOMMatrix; index: number; before: Box[] };
export default function DetectionCanvas({ image, width, height, boxes, selected, label, tool, disabled, onSelect, onChange, onDragging }: Props) {
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
  const cancel = () => {
    if (drag.current?.mode === 'pan') setView(drag.current.view);
    drag.current = null; preview.current = null; setPreviewBoxes(null); onDragging(false);
  };
  useEffect(() => {
    const element = svg.current!;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      if (drag.current) return;
      setView(current => zoomView(current, point(event.clientX, event.clientY), event.deltaY > 0 ? 1 / 1.15 : 1.15, width, height));
    };
    element.addEventListener('wheel', wheel, { passive: false });
    const release = () => { space.current = false; };
    window.addEventListener('keyup', release); window.addEventListener('blur', release);
    return () => { element.removeEventListener('wheel', wheel); window.removeEventListener('keyup', release); window.removeEventListener('blur', release); };
  }, [width, height]);
  const begin = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (![0, 1].includes(event.button)) return;
    svg.current!.focus(); event.preventDefault();
    const raw = point(event.clientX, event.clientY), p = clamp(raw);
    const before = boxes.map(b => ({ ...b }));
    let mode: Drag['mode'], index = selected;
    if (event.ctrlKey || event.metaKey || space.current || event.button === 1) mode = 'pan';
    else {
      if (disabled) return;
      const hits = hitBoxes(boxes, raw);
      if (event.altKey && hits.length) {
        onSelect(hits[(hits.indexOf(selected) + 1) % hits.length]); return;
      }
      if ((event.target as Element).getAttribute('data-resize') === 'true' && !event.shiftKey) mode = 'resize';
      else if (event.shiftKey || (tool === 'draw' && !hits.length)) {
        if (!label || raw.x < 0 || raw.y < 0 || raw.x > width || raw.y > height) return;
        mode = 'draw'; index = boxes.length;
        before.push({ x: p.x, y: p.y, width: 0, height: 0, label }); onSelect(index);
      } else if (hits.length) { mode = 'move'; index = hits[0]; onSelect(index); }
      else { onSelect(-1); return; }
    }
    drag.current = { mode, index, start: mode === 'pan' ? raw : p, view, inverse: svg.current!.getScreenCTM()!.inverse(), before };
    svg.current!.setPointerCapture(event.pointerId); onDragging(true);
  };
  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current; if (!d) return;
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
      b.width = Math.max(1, Math.min(width - b.x, p.x - b.x)); b.height = Math.max(1, Math.min(height - b.y, p.y - b.y));
    }
    preview.current = d.before.map((box, i) => i === d.index ? b : box); setPreviewBoxes(preview.current);
  };
  const finish = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current; if (!d) return;
    if (d.mode !== 'pan' && preview.current) {
      const next = preview.current.filter(b => b.width >= 1 && b.height >= 1);
      onChange(next); if (d.index >= next.length) onSelect(-1);
    }
    drag.current = null; preview.current = null; setPreviewBoxes(null); onDragging(false);
    if (svg.current!.hasPointerCapture(event.pointerId)) svg.current!.releasePointerCapture(event.pointerId);
  };
  const displayed = previewBoxes || boxes;
  const stroke = Math.max(view.width / 450, 1), handle = Math.max(view.width / 80, 1);
  return <div className="detection-canvas">
    <Space wrap style={{ marginBottom: 12 }}>
      <Button aria-label="缩小图片" onClick={() => setView(v => zoomView(v, { x: v.x + v.width / 2, y: v.y + v.height / 2 }, 1 / 1.2, width, height))}>−</Button>
      <Tag aria-label="图片缩放比例">{Math.round(width / view.width * 100)}%</Tag>
      <Button aria-label="放大图片" onClick={() => setView(v => zoomView(v, { x: v.x + v.width / 2, y: v.y + v.height / 2 }, 1.2, width, height))}>+</Button>
      <Button onClick={() => setView({ x: 0, y: 0, width, height })}>适应图片</Button>
    </Space>
    <svg ref={svg} className="workspace-canvas" aria-label="检测画框区域" role="img" tabIndex={0}
      viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
      style={{ width: '100%', aspectRatio: `${width} / ${height}`, maxHeight: '70dvh', display: 'block', background: '#111', touchAction: 'none', cursor: tool === 'draw' ? 'crosshair' : 'default' }}
      onPointerDown={begin} onPointerMove={move} onPointerUp={finish} onPointerCancel={cancel} onLostPointerCapture={cancel}
      onKeyDown={event => {
        if (event.code === 'Space') { event.preventDefault(); space.current = true; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); onSelect(-1); }
        if (event.key === 'Tab' && boxes.length && !event.ctrlKey && !event.altKey) {
          const next = selected < 0 ? (event.shiftKey ? boxes.length - 1 : 0) : selected + (event.shiftKey ? -1 : 1);
          if (next >= 0 && next < boxes.length) { event.preventDefault(); onSelect(next); }
          else onSelect(-1);
        }
      }}>
      <image href={image} width={width} height={height} />
      {displayed.map((b, i) => <g key={i}>
        <rect x={b.x} y={b.y} width={b.width} height={b.height} fill="rgba(22,119,255,.08)" stroke={i === selected ? '#ffff00' : '#00ff88'} strokeWidth={stroke} />
        <text x={b.x + stroke * 2} y={Math.max(stroke * 10, b.y - stroke * 3)} fill="#00ff88" fontSize={stroke * 8} pointerEvents="none">{i + 1} · {b.label}</text>
        {i === selected && !disabled && <rect data-resize="true" x={b.x + b.width - handle / 2} y={b.y + b.height - handle / 2} width={handle} height={handle} fill="#ffff00" />}
      </g>)}
    </svg>
  </div>;
}

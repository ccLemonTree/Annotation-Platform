import { useState } from 'react';
import { labelColor } from './detectionEditor';

export interface DetectionBoxItem {
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  score?: number;
}

export interface DetectionBoxOverlayProps {
  src: string;
  width: number;
  height: number;
  boxes: DetectionBoxItem[];
  alt?: string;
  maxHeight?: number | string;
  className?: string;
  labels?: string[];
  neutral?: boolean;
  showScore?: boolean;
  showIndex?: boolean;
  svgLabel?: string;
  onImageLoad?: (size: { width: number; height: number }) => void;
  onImageError?: () => void;
}

export function DetectionBoxOverlay({
  src,
  width,
  height,
  boxes,
  alt = '检测叠框预览',
  maxHeight,
  className,
  labels,
  neutral = false,
  showScore = true,
  showIndex = false,
  svgLabel = '检测框预览',
  onImageLoad,
  onImageError,
}: DetectionBoxOverlayProps) {
  const [natural, setNatural] = useState({ width: 0, height: 0 });
  const w = width || natural.width;
  const h = height || natural.height;
  const fontSize = w ? Math.max(14, Math.round(w / 48)) : 14;

  return (
    <div
      className={className}
      style={{
        position: 'relative',
        width: '100%',
        maxHeight,
        aspectRatio: w && h ? `${w} / ${h}` : undefined,
        lineHeight: 0,
      }}
    >
      <img
        src={src}
        alt={alt}
        style={{
          display: 'block',
          width: '100%',
          height: w && h ? '100%' : 'auto',
          maxHeight,
          objectFit: 'contain',
        }}
        onLoad={event => {
          const img = event.currentTarget;
          const next = { width: img.naturalWidth, height: img.naturalHeight };
          setNatural(next);
          onImageLoad?.(next);
        }}
        onError={onImageError}
      />
      {w > 0 && h > 0 && boxes.length > 0 && (
        <svg
          role="img"
          aria-label={svgLabel}
          viewBox={`0 0 ${w} ${h}`}
          preserveAspectRatio="xMidYMid meet"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
        >
          {boxes.map((box, index) => {
            const color = neutral ? '#aeb7c6' : labelColor(box.label, labels);
            const prefix = showIndex ? `${index + 1} · ` : '';
            const suffix = showScore && typeof box.score === 'number' ? ` ${(box.score * 100).toFixed(1)}%` : '';
            const text = `${prefix}${box.label}${suffix}`;
            const textX = Math.max(0, Math.min(box.x, w - Math.min(w * 0.5, fontSize * text.length * 0.6)));
            const textY = Math.max(fontSize, box.y - 4);
            return (
              <g key={index}>
                <rect x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke="#0b101b" strokeWidth={6} vectorEffect="non-scaling-stroke" />
                <rect x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke="#ffffff" strokeWidth={4} vectorEffect="non-scaling-stroke" />
                <rect x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
                <text
                  x={textX}
                  y={textY}
                  fill="#ffffff"
                  stroke="#0b101b"
                  strokeWidth={Math.max(3, fontSize * 0.2)}
                  paintOrder="stroke"
                  strokeLinejoin="round"
                  fontSize={fontSize}
                >
                  {text}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

export default DetectionBoxOverlay;

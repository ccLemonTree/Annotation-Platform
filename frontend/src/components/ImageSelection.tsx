import { useEffect, useRef } from 'react';
import { Button, Space } from 'antd';
import { getImageSelection } from '../api/images';
import type { ImageFilters } from '../api/images';

export default function ImageSelection({ filters, count, projectId, busy, disabled, onBusy, onSelect, onError }: {
  filters: ImageFilters; count: number; projectId?: string; busy: boolean; disabled?: boolean;
  onBusy: (busy: boolean) => void; onSelect: (ids: string[]) => void; onError: (error: string) => void;
}) {
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); onBusy(false); }, [onBusy]);
  const selectAll = async () => {
    const controller = new AbortController(); request.current = controller; onBusy(true); onError('');
    try {
      const result = await getImageSelection(filters, projectId, controller.signal);
      if (!controller.signal.aborted) onSelect(result.image_ids);
    } catch { if (!controller.signal.aborted) onError('读取全部图片失败，原选择已保留，请重试'); }
    finally { if (!controller.signal.aborted) onBusy(false); }
  };
  return <Space wrap style={{ marginBottom: 12 }}>
    <strong aria-live="polite">已选 {count} 张</strong>
    <Button onClick={selectAll} disabled={busy || disabled}>选择当前筛选全部图片</Button>
    <Button onClick={() => onSelect([])} disabled={busy || !count}>清空选择</Button>
    <span className="workspace-help">跨页保留选择；切换筛选会清空选择{projectId ? '，全选排除项目已有图片' : ''}。</span>
  </Space>;
}

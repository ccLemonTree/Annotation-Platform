import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Image, Space } from 'antd';
import apiClient from '../api/client';
import type { PaginatedResponse } from '../api/assets';
import type { ImageFilters } from '../api/images';

type PreviewRow = { id: string; original_name: string };
export default function PagedAssetPreview({ rows, index, page, pageSize, total, projectId, imageKind, filters, onClose }: {
  rows: PreviewRow[]; index: number; page: number; pageSize: number; total: number;
  projectId?: string; filters: ImageFilters; onClose: () => void;
  imageKind?: 'all'|'originals'|'crops';
}) {
  const [current, setCurrent] = useState({ rows, index: (page - 1) * pageSize + index, page, total });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [content, setContent] = useState<{ id: string; source?: string; failed?: boolean }>();
  const request = useRef<AbortController | null>(null);
  const row = current.rows[current.index % pageSize];

  useEffect(() => () => { request.current?.abort(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    apiClient.get(`/api/${projectId ? 'assets' : 'images'}/${row.id}/content`, { responseType: 'blob', signal: controller.signal })
      .then(response => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(response.data);
        setContent({ id: row.id, source: objectUrl });
      })
      .catch(() => { if (!controller.signal.aborted) setContent({ id: row.id, failed: true }); });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [row.id, projectId]);

  const move = useCallback(async (offset: number) => {
    const next = current.index + offset;
    if (request.current || next < 0 || next >= current.total) return;
    setError('');
    const nextPage = Math.floor(next / pageSize) + 1;
    if (nextPage === current.page) {
      setCurrent({ ...current, index: next });
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    try {
      const { data,headers } = await apiClient.get<PaginatedResponse<PreviewRow>>(projectId ? '/api/assets' : '/api/images', {
        params: { ...(projectId ? { project_id: projectId, image_kind:imageKind,q:filters.q } : filters), page: nextPage, page_size: pageSize },
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if(projectId&&filters.q&&headers['x-asset-filename-filter']!=='1')throw Error('后台尚未启用文件名搜索');
      if (!data.items[next % pageSize] || next >= data.total) {
        setError('图片列表已变化，请关闭预览后刷新列表');
        return;
      }
      setCurrent({ rows: data.items, index: next, page: nextPage, total: data.total });
    } catch {
      if (!controller.signal.aborted) setError('图片列表读取失败，请重试');
    } finally {
      if (!controller.signal.aborted) { request.current = null; setPending(false); }
    }
  }, [current, pageSize, projectId, imageKind, filters]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      const target = event.target;
      if (target instanceof Element && target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="combobox"]')) return;
      const otherDialog = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
        .some(dialog => !dialog.classList.contains('asset-paged-preview') && dialog.getClientRects().length > 0 && getComputedStyle(dialog).visibility !== 'hidden');
      if (otherDialog) return;
      event.preventDefault();
      void move(event.key === 'ArrowLeft' ? -1 : 1);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [move]);

  const loaded = content?.id === row.id;
  return <Image src={loaded ? content.source : undefined} alt={row.original_name} styles={{ root: { display: 'none' } }}
    classNames={{ popup: { root: 'asset-paged-preview' } }} preview={{
      open: true,
      onOpenChange: open => { if (!open) onClose(); },
      imageRender: node => loaded && content.source ? node : <span style={{ color: 'white' }} role="status">{loaded && content.failed ? '图片加载失败，可切换其他图片' : '图片加载中…'}</span>,
      actionsRender: node => <Space orientation="vertical" style={{ color: 'white', textAlign: 'center' }}>
        <span>{row.original_name}</span>
        <Space>
          <Button aria-label="上一张" disabled={pending || current.index === 0} onClick={() => void move(-1)}>上一张</Button>
          <span aria-live="polite">{current.index + 1} / {current.total}</span>
          <Button aria-label="下一张" disabled={pending || current.index >= current.total - 1} onClick={() => void move(1)}>下一张</Button>
        </Space>
        {error && <span role="alert">{error}</span>}
        {node}
      </Space>,
    }} />;
}

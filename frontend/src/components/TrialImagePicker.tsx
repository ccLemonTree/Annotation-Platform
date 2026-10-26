import { useEffect, useState } from 'react';
import { Alert, Button, DatePicker, Input, Pagination, Select, Space } from 'antd';
import { getImageCollections, getImages } from '../api/images';
import type { ImageCollection, ImageFilters, LibraryImage } from '../api/images';

export default function TrialImagePicker({ value, onChange }: {
  value?: string; onChange: (id?: string) => void;
}) {
  const [query, setQuery] = useState<ImageFilters>({ page: 1, page_size: 20 });
  const [collections, setCollections] = useState<ImageCollection[]>();
  const [collectionError, setCollectionError] = useState(false), [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ query: ImageFilters; items: LibraryImage[]; total: number; error?: boolean }>();
  const loading = result?.query !== query;
  const images = loading ? [] : result?.items || [];
  useEffect(() => {
    let active = true;
    getImageCollections().then(items => {
      if (active) { setCollections(items); setCollectionError(false); }
    }).catch(() => { if (active) setCollectionError(true); });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    let active = true;
    getImages(query).then(data => {
      if (active) setResult({ query, items: data.items, total: data.total });
    }).catch(() => { if (active) setResult({ query, items: [], total: 0, error: true }); });
    return () => { active = false; };
  }, [query]);
  const filter = (next: Partial<ImageFilters>) => {
    onChange(undefined);
    setQuery({ ...query, ...next, page: 1 });
  };
  const retry = () => { setQuery({ ...query }); setRevision(current => current + 1); };
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      <Select aria-label="试运行图片集" placeholder="全部图片集" showSearch optionFilterProp="label" allowClear
        style={{ flex: '1 1 200px', minWidth: 0 }} value={query.collection_id}
        loading={!collections && !collectionError} onChange={collection_id => filter({ collection_id })}
        options={collections?.map(group => ({ value: group.id, label: `${group.name}（${group.image_count}）` }))} />
      <Input.Search aria-label="试运行图片名称" placeholder="按文件名搜索" enterButton="搜索" allowClear maxLength={255}
        style={{ flex: '1 1 220px', minWidth: 0 }} onSearch={q => filter({ q: q || undefined })} />
    </div>
    <DatePicker.RangePicker aria-label="试运行入库时间" placeholder={['入库开始时间', '入库结束时间']}
      style={{ width: '100%' }} showTime allowEmpty={[true, true]} onChange={range => filter({
        created_from: range?.[0]?.toISOString(), created_to: range?.[1]?.toISOString(),
      })} />
    {collectionError && <Alert type="error" showIcon title="图片集读取失败，请重试" action={<Button aria-label="重试图片集" size="small" onClick={retry}>重试</Button>} />}
    {!loading && result?.error && <Alert type="error" showIcon title="图片列表读取失败，请重试" action={<Button aria-label="重试图片列表" size="small" onClick={retry}>重试</Button>} />}
    <Select aria-label="试运行图片库图片" placeholder="选择当前页图片，也可手输下方 ID" allowClear
      style={{ width: '100%' }} loading={loading} disabled={loading} value={value || undefined} onChange={onChange}
      options={images.map(image => ({ value: image.id, label: `${image.original_name} · ${image.id}` }))} />
    <Pagination aria-label="试运行图片分页" current={query.page} pageSize={query.page_size} total={result?.total || 0}
      showSizeChanger responsive showLessItems pageSizeOptions={[20, 50, 100]} disabled={loading}
      showTotal={total => `共 ${total} 张`} style={{ display: 'flex', flexWrap: 'wrap', rowGap: 8 }}
      onChange={(page, page_size) => {
        onChange(undefined);
        setQuery({ ...query, page: page_size === query.page_size ? page : 1, page_size });
      }} />
  </Space>;
}

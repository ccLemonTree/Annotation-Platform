import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Drawer, Pagination, Space, Table, Tag } from 'antd';
import { getImages, getImageCollections, associateImages } from '../api/images';
import type { LibraryImage, ImageCollection, ImageFilters as Filters } from '../api/images';
import { getAssets } from '../api/assets';
import AssetImage from './AssetImage';
import ImageFilters from './ImageFilters';
import ImageSelection from './ImageSelection';

export default function ProjectImagePicker({ projectId, projectName, taskType='detection', onClose, onAdded }: {
  projectId: string; projectName: string;taskType?:string; onClose: () => void; onAdded: (count: number,summary?:string) => void;
}) {
  const [images, setImages] = useState<LibraryImage[]>([]);
  const [collections, setCollections] = useState<ImageCollection[]>([]);
  const [used, setUsed] = useState(new Set<string>());
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [filters, setFilters] = useState<Filters>({});
  const [pageSize, setPageSize] = useState(20);
  const [page, setPage] = useState(1), [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true), [contextLoading, setContextLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [includeImported,setIncludeImported]=useState(false);
  useEffect(() => {
    let active = true;
    (async () => {
      const groups = await getImageCollections();
      const ids = new Set<string>();
      for (let page = 1; ; page++) {
        const result = await getAssets({ project_id: projectId, page, page_size: 100 });
        result.items.forEach(asset => { if (asset.image_id) ids.add(asset.image_id); });
        if (page * 100 >= result.total || !result.items.length) break;
      }
      if (active) { setCollections(groups); setUsed(ids); setReady(true); }
    })().catch(() => { if (active) setError('项目图片读取失败，请关闭后重试'); })
      .finally(() => { if (active) setContextLoading(false); });
    return () => { active = false; };
  }, [projectId]);
  useEffect(() => {
    let active = true; setLoading(true); setImages([]);
    getImages({ ...filters, page, page_size: pageSize })
      .then(result => { if (active) { setImages(result.items); setTotal(result.total); } })
      .catch(() => { if (active) setError('图片库读取失败，请关闭后重试'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [filters, page, pageSize]);
  const add = async () => {
    setBusy(true); setError('');
    try {
      const result = await associateImages(projectId, selected.map(String), ids => {
        const done = new Set(ids);
        setSelected(keys => keys.filter(key => !done.has(String(key))));
        setUsed(previous => new Set([...previous, ...ids]));
      },includeImported);
      onAdded(result.added_count,includeImported?`已添加 ${result.added_count} 张图片，带入标注 ${result.annotated}，冲突保留 ${result.conflicts}`:undefined);
    } catch { setError('添加未全部完成；成功批次已生效，剩余选择已保留，请重试'); }
    finally { setBusy(false); }
  };
  return <Drawer title={`从图片库添加到 ${projectName}`} open size={1080}
    styles={{ body: { display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' } }}
    onClose={() => { if (!busy) onClose(); }} footer={<Space wrap>
      <span>已选 {selected.length} 张 · 添加到 {projectName}</span>
      <Button aria-label="取消选图" onClick={onClose} disabled={busy}>取消</Button>
      <Button type="primary" loading={busy} disabled={!selected.length || contextLoading || loading || !ready}
        onClick={add}>添加到 {projectName}</Button>
    </Space>}>
    <div style={{ flexShrink: 0, maxHeight: '42%', overflowY: 'auto' }}>
    <p>选择图片后添加到当前项目，已在项目中的图片无需重复添加。</p>
    <Checkbox checked={includeImported} disabled={busy||taskType!=='detection'} onChange={event=>{setIncludeImported(event.target.checked);setSelected([]);}}>带入已有YOLO标注</Checkbox>
    <p className="workspace-help">创建项目独立副本，沿用导入时信任状态；已有标注不覆盖。已在项目中的图片也可补入图库标注。</p>
    {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 12 }} />}
    <ImageFilters value={filters} collections={collections} scenes={images.flatMap(image => image.scene_labels)}
      prefix="选择器" disabled={busy} onChange={value => { setFilters(value); setPage(1); setSelected([]); }} />
    <ImageSelection filters={filters} count={selected.length} projectId={includeImported?undefined:projectId} busy={busy} disabled={!ready || loading}
      onBusy={setBusy} onSelect={setSelected} onError={setError} />
    </div>
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
    <Table dataSource={images} rowKey="id" loading={loading || contextLoading} scroll={{ x: 680 }}
      rowSelection={{ selectedRowKeys: selected, onChange: setSelected, preserveSelectedRowKeys: true,
        getCheckboxProps: image => ({ disabled: busy || !ready || contextLoading || (used.has(image.id)&&(!includeImported||!image.has_imported_annotation)) }) }}
      columns={[
        { title: '预览', width: 80, render: (_, image) => <AssetImage imageId={image.id} width={56} height={56} style={{ objectFit: 'cover' }} /> },
        { title: '文件名', dataIndex: 'original_name', width: 230, ellipsis: true },
        { title: '图片集', width: 180, render: (_, image) => <Space wrap>{image.collection_names.map(name => <Tag key={name}>{name}</Tag>)}</Space> },
        { title: '场景', width: 140, render: (_, image) => image.scene_labels.join('、') || '—' },
        {title:'导入标注',width:120,render:(_,image)=>image.has_imported_annotation?<Tag color={image.imported_annotation_trusted?'success':'default'}>{image.imported_annotation_trusted?'已信任':'待审核'}</Tag>:'—'},
        { title: '使用情况', width: 140, render: (_, image) => used.has(image.id) ? <Tag>已在当前项目</Tag> : <Tag color="blue">可添加</Tag> },
      ]}
      pagination={false} />
    </div>
    <Pagination aria-label="图片选择分页" current={page} pageSize={pageSize} total={total} showSizeChanger responsive showLessItems
      pageSizeOptions={[20, 50, 100]} showTotal={count => <span style={{ whiteSpace: 'nowrap' }}>共 {count} 张</span>} disabled={busy}
      style={{ flexShrink: 0, paddingTop: 12 }} onChange={(next, size) => { setPage(next); setPageSize(size); }} />
  </Drawer>;
}

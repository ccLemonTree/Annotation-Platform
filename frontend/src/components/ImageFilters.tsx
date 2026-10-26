import { DatePicker, Input, Select, Space } from 'antd';
import type { ImageCollection, ImageFilters as Filters } from '../api/images';

export default function ImageFilters({ value, collections, scenes, onChange, disabled, prefix = '筛选' }: {
  value: Filters; collections: ImageCollection[]; scenes: string[]; onChange: (value: Filters) => void; disabled?: boolean; prefix?: string;
}) {
  const change = (next: Partial<Filters>) => onChange({ ...value, ...next });
  return <Space wrap style={{ marginBottom: 12 }}>
    <Select aria-label={`${prefix}场景`} placeholder="全部场景" showSearch optionFilterProp="label" allowClear disabled={disabled}
      style={{ width: 180 }} value={value.scene} onChange={scene => change({ scene })}
      options={[...new Set([...collections.flatMap(group => group.ingest_config?.scenes || []), ...scenes])].map(scene => ({ label: scene, value: scene }))} />
    <Select aria-label={`${prefix}场景分类状态`} placeholder="全部分类状态" allowClear disabled={disabled}
      style={{ width: 180 }} value={value.scene_status} onChange={scene_status => change({ scene_status })}
      options={[{ label: '未分类', value: 'unclassified' }, { label: '待分类', value: 'pending' }, { label: '分类中', value: 'processing' }, { label: '已分类', value: 'completed' }, { label: '分类失败', value: 'failed' }]} />
    <Select aria-label={`${prefix}图片集`} placeholder="全部图片集" showSearch optionFilterProp="label" allowClear disabled={disabled}
      value={value.collection_id} style={{ width: 220 }} onChange={collection_id => change({ collection_id })}
      options={collections.map(group => ({ value: group.id, label: `${group.name}（${group.image_count}）` }))} />
    <Input.Search enterButton="搜索" aria-label={`${prefix}图片名称`} placeholder="按文件名搜索" allowClear disabled={disabled}
      style={{ width: 220 }} onSearch={q => change({ q: q || undefined })} />
    <Select aria-label={`${prefix}图片来源`} placeholder="全部来源" allowClear disabled={disabled}
      value={value.source_type} style={{ width: 140 }} onChange={source_type => change({ source_type })}
      options={[{ label: '上传', value: 'upload' }, { label: 'SmartEye', value: 'smarteye' }]} />
    <Input.Search enterButton="搜索" aria-label={`${prefix}图片批次`} placeholder="输入批次或拉取任务ID" allowClear disabled={disabled}
      style={{ width: 230 }} onSearch={batch_id => change({ batch_id: batch_id || undefined })} />
    <DatePicker.RangePicker aria-label={`${prefix}入库时间`} showTime allowEmpty={[true, true]} disabled={disabled} onChange={range => change({
      created_from: range?.[0]?.toISOString(), created_to: range?.[1]?.toISOString(),
    })} />
  </Space>;
}

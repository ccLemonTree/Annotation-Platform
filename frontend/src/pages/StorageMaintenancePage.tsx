import { useState } from 'react';
import { Alert, Button, Card, Descriptions, Space, Table, Typography } from 'antd';
import { getCleanupPreview } from '../api/storageMaintenance';
import type { CleanupPreview } from '../api/storageMaintenance';

const reasons: Record<string, string> = {
  library_image: '图片库引用', asset: '项目图片引用', frozen_export: '冻结包引用', validation_set: '验证集引用',
  smarteye_runtime: 'SmartEye下载引用', smarteye_legacy: '历史下载引用',
  hardlink: '共享硬链接', grace_period: '年龄宽限', active_uploads: '未完成上传保护',
  active_downloads: '下载任务保护', linked_path: '链接路径', not_regular: '非普通文件', missing: '文件已不存在',
};

export default function StorageMaintenancePage() {
  const [preview, setPreview] = useState<CleanupPreview>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function scan() {
    setLoading(true); setPreview(undefined); setError('');
    try { setPreview(await getCleanupPreview()); }
    catch { setError('预览失败，未生成清理候选。请核对存储与引用表后重试。'); }
    finally { setLoading(false); }
  }

  return <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
    <Typography.Title level={2}>存储维护</Typography.Title>
    <Alert type="info" showIcon title="管理员只读预览" description="核查所有租户的本地原图引用。仅检查已知 images 哈希目录，保护下载、冻结包、暂存及共享硬链接。" />
    <Space>
      <Button type="primary" onClick={scan} loading={loading}>扫描预览</Button>
      <Button disabled>隔离归档（需要维护写入保护）</Button>
    </Space>
    {error && <Alert type="error" showIcon title={error} />}
    {preview && <>
      <Alert type="warning" showIcon title="隔离归档暂不可执行" description={preview.message} />
      <Card title="本次预览">
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} items={[
          { key: 'scanned', label: '已核查文件', children: preview.scanned_count },
          { key: 'candidates', label: '未发现引用的候选', children: preview.candidate_count },
          { key: 'bytes', label: '候选大小', children: `${preview.candidate_bytes.toLocaleString()} 字节` },
          { key: 'grace', label: '年龄宽限', children: `${preview.grace_days} 天` },
          { key: 'uploads', label: '未完成上传', children: preview.active_uploads },
          { key: 'downloads', label: '未结束下载', children: preview.active_downloads },
        ]} />
        <Typography.Paragraph type="secondary">扫描结果仅供核对；年龄宽限不保证并发安全，候选大小不代表可释放磁盘空间。</Typography.Paragraph>
        {Object.entries(preview.protected_counts).map(([reason, count]) =>
          <Typography.Paragraph key={reason}>{reasons[reason] || reason}：{count}</Typography.Paragraph>)}
      </Card>
      {preview.truncated && <Alert type="info" title="仅显示前200个候选，以上汇总包含全部已核查文件。" />}
      <Table rowKey="path" dataSource={preview.items} pagination={{ pageSize: 20 }} columns={[
        { title: '存储相对路径', dataIndex: 'path', render: (path: string) => <Typography.Text style={{ overflowWrap: 'anywhere' }}>{path}</Typography.Text> },
        { title: '大小（字节）', dataIndex: 'size', render: (size: number) => size.toLocaleString() },
        { title: '最后修改时间', dataIndex: 'modified_at', render: (value: number) => new Date(value * 1000).toLocaleString() },
      ]} />
    </>}
  </Space>;
}

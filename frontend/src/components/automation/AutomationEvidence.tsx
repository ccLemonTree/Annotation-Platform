import { Alert, Descriptions, Space, Table, Tag, Typography } from 'antd';
import type { AutomationItem } from '../../api/automation';
import JsonView from '../JsonView';

export default function AutomationEvidence({ item }: { item: AutomationItem }) {
  const evidence = item.evidence || {};
  const verdict = (value: unknown) => value === 'pass' ? '通过' : value === 'fail' ? '不通过' : value === 'uncertain' ? '待确认' : '缺少证据';
  const boxes = Array.isArray(evidence.boxes) ? evidence.boxes.filter((box): box is Record<string, unknown> => !!box && typeof box === 'object') : [];
  return <>
    <Descriptions size="small" column={1} items={[
      { key: 'id', label: '明细 ID', children: item.id },
      { key: 'asset', label: '资产 ID', children: item.asset_id },
      { key: 'annotation', label: '标注 / 版本', children: `${item.annotation_id || '尚无标注'} / ${item.annotation_version ?? '—'}` },
      { key: 'reason', label: '原因', children: item.reason || '—' },
    ]} />
    <Alert type="info" title="逐框结论、整图完整性与负样本结论以服务端完整证据为准；缺少证据不能视为通过。" />
    <Space wrap style={{ marginBlock: 16 }}>
      <Tag>整图完整性：{verdict(evidence.completeness)}</Tag>
      <Tag>负样本结论：{verdict(evidence.negative)}</Tag>
    </Space>
    {typeof evidence.reason === 'string' && <Typography.Paragraph>{evidence.reason}</Typography.Paragraph>}
    <Table aria-label="逐框审核结论" size="small" rowKey={(_, index) => String(index)} dataSource={boxes} pagination={false} scroll={{ x: 400 }} columns={[
      { title: '框编号', dataIndex: 'index', width: 90, render: value => typeof value === 'number' ? value + 1 : '—' },
      { title: '结论', dataIndex: 'verdict', width: 100, render: verdict },
      { title: '理由', dataIndex: 'reason', render: value => typeof value === 'string' ? value : '—' },
    ]} />
    <Typography.Title level={5}>AI审核完整证据</Typography.Title>
    <JsonView label="AI审核完整证据" value={item.evidence}/>
    <Typography.Title level={5}>检测 / 审核结果</Typography.Title>
    <JsonView label="检测 / 审核结果" value={item.result}/>
  </>;
}

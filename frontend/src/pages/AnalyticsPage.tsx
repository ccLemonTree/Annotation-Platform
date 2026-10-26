import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Col, Empty, Input, Progress, Row, Select, Space, Statistic, Table, Typography } from 'antd';
import { getAnalytics } from '../api/analytics';
import type { Analytics, AnalyticsFilters, EfficiencyUser } from '../api/analytics';
import { getProjects } from '../api/projects';
import type { Project } from '../api/projects';

const duration = (value: number | null) => value === null ? '未采集' : `${(value/60).toFixed(1)} 分钟`;
const rate = (value: number | null) => value === null ? '未采集' : `${value.toFixed(1)} 次/小时`;

export default function AnalyticsPage() {
  const [params] = useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | undefined>(params.get('project_id') || undefined);
  const [start, setStart] = useState(''), [end, setEnd] = useState('');
  const [filters, setFilters] = useState<AnalyticsFilters>({ project_id: projectId });
  const [data, setData] = useState<Analytics>();
  const [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [projectError, setProjectError] = useState('');
  useEffect(() => {
    let active = true;
    (async () => {
      const items: Project[] = [];
      let page = 1, total = 1;
      while (items.length < total) {
        const result = await getProjects({ page: page++, page_size: 100 });
        items.push(...result.items); total = result.total;
        if (!result.items.length || !active) break;
      }
      if (active) setProjects(items);
    })().catch(() => { if (active) setProjectError('项目列表读取失败，请刷新重试'); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    getAnalytics(filters, controller.signal).then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(() => { if (!controller.signal.aborted) setError('统计读取失败，请重试'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters]);
  const apply = () => {
    if (start && end && start > end) { setError('开始日期不能晚于结束日期'); return; }
    const exclusiveEnd = end ? new Date(`${end}T00:00:00`) : undefined;
    if (exclusiveEnd) exclusiveEnd.setDate(exclusiveEnd.getDate()+1);
    setLoading(true); setError(''); setData(undefined);
    setFilters({ project_id: projectId, start: start ? new Date(`${start}T00:00:00`).toISOString() : undefined,
      end: exclusiveEnd?.toISOString() });
  };
  const q = data?.quality, w = data?.workload, e = data?.efficiency;
  return <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
    <Card>
      <Space wrap>
        <Select aria-label="统计项目" allowClear placeholder="全部项目" style={{ minWidth: 240 }} value={projectId}
          onChange={setProjectId} options={projects.map(p => ({ value: p.id, label: p.project_name }))} />
        <label>开始日期 <Input aria-label="统计开始日期" type="date" value={start} onChange={ev => setStart(ev.target.value)} /></label>
        <label>结束日期 <Input aria-label="统计结束日期" type="date" value={end} onChange={ev => setEnd(ev.target.value)} /></label>
        <Button type="primary" loading={loading} onClick={apply}>查询统计</Button>
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>日期按浏览器本地时区、包含结束当天；不填为全部时间。当前状态按最新版本创建日期筛选，工作量按事件发生日期筛选。</Typography.Paragraph>
    </Card>
    {projectError && <Alert type="warning" title={projectError} />}
    {error && <Alert type="error" showIcon title={error} />}
    <Alert showIcon type="info" title="流程通过率不等于独立测试集准确率；这里没有独立真值评测。" />
    {!data && !loading && !error && <Empty description="暂无统计" />}
    <Card title="最新版本质量流程" loading={loading}>
      {q && <>
        <Row gutter={[16, 16]}>{[
          ['最新标注图片', q.total], ['审核通过', q.approved], ['审核拒绝', q.rejected],
          ['待审核', q.pending], ['负样本图片', q.negative_samples],
        ].map(([title, value]) => <Col key={title} xs={12} md={4}><Statistic title={title} value={value} /></Col>)}</Row>
        <Typography.Paragraph style={{ marginTop: 16 }}>流程通过率（通过 / 已审核）：{q.process_pass_rate === null ? '尚无已审核数据' : `${(q.process_pass_rate*100).toFixed(1)}%`}</Typography.Paragraph>
        {q.process_pass_rate !== null && <Progress percent={Number((q.process_pass_rate*100).toFixed(1))} />}
        <Typography.Paragraph>最新版本来源：人工 {q.sources.manual} · 模型 {q.sources.model} · 未知 {q.sources.unknown}；审核来源：人工 {q.review_origins.human} · 自动 {q.review_origins.automatic} · 未知 {q.review_origins.unknown}</Typography.Paragraph>
        <Table size="small" rowKey="label" dataSource={q.categories} pagination={{ pageSize: 10 }} locale={{ emptyText: '暂无检测框类别' }}
          columns={[{ title: '类别', dataIndex: 'label' }, { title: '框数', dataIndex: 'boxes' }, { title: '涉及图片数', dataIndex: 'assets' }]} />
        <Typography.Paragraph type="secondary">{data?.definitions.quality} 负样本仅统计明确的空 boxes 列表；类别分布按最新版本检测框统计。</Typography.Paragraph>
      </>}
    </Card>
    <Card title="工作量与人工效率" loading={loading}>
      {w && e && <>
        <Row gutter={[16, 16]}>{[
          ['人工标注保存（次）', w.human_annotation_events], ['人工审核（次）', w.human_review_events],
          ['模型自动生成（次）', w.automatic_annotation_events], ['自动审核（次）', w.automatic_review_events],
          ['人工返工（次）', w.rework_events], ['来源未知事件', w.unknown_events],
        ].map(([title, value]) => <Col key={title} xs={12} md={4}><Statistic title={title} value={value} /></Col>)}</Row>
        {e.timing_status === 'not_collected' && <Alert style={{ marginTop: 16 }} type="warning" title="此范围内活跃时长未采集，无法计算人工效率；旧数据不会按创建时间差估算。" />}
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} md={8}><Statistic title="去重人工活跃时长" value={duration(e.active_seconds)} /></Col>
          <Col xs={24} md={8}><Statistic title="标注活跃时长 / 效率" value={duration(e.annotation_seconds)} suffix={rate(e.annotation_per_hour)} /></Col>
          <Col xs={24} md={8}><Statistic title="审核活跃时长 / 效率" value={duration(e.review_seconds)} suffix={rate(e.review_per_hour)} /></Col>
        </Row>
        <Typography.Paragraph style={{ marginTop: 16 }}>有计时证据的人工操作：标注 {e.timed_annotation_events} 次、审核 {e.timed_review_events} 次；自动工作量不计入人工每小时产出。</Typography.Paragraph>
        <Table<EfficiencyUser> size="small" rowKey="user_id" dataSource={e.users} scroll={{ x: 1080 }} pagination={{ pageSize: 10 }} columns={[
          { title: '用户', dataIndex: 'username' }, { title: '人工标注（次）', dataIndex: 'annotation_events' },
          { title: '人工审核（次）', dataIndex: 'review_events' },
          { title: '去重活跃时长', dataIndex: 'active_seconds', render: duration },
          { title: '标注时长', dataIndex: 'annotation_seconds', render: duration },
          { title: '审核时长', dataIndex: 'review_seconds', render: duration },
          { title: '计时内标注', dataIndex: 'timed_annotation_events' }, { title: '计时内审核', dataIndex: 'timed_review_events' },
          { title: '人工标注效率', dataIndex: 'annotation_per_hour', render: rate },
          { title: '人工审核效率', dataIndex: 'review_per_hour', render: rate },
        ]} />
        <Typography.Paragraph type="secondary">{data?.definitions.workload}<br />{data?.definitions.timing}<br />{data?.definitions.efficiency}</Typography.Paragraph>
      </>}
    </Card>
  </Space>;
}

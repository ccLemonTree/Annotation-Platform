import { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Progress, Tag } from 'antd';
import { ApiOutlined, CheckCircleOutlined, FileImageOutlined, ReloadOutlined, TagsOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { getAutomationJobs } from '../../api/automation';
import { getSemanticJobs } from '../../api/imageSemantics';
import { getValidationRuns } from '../../api/validation';
import { getTrainingRuns } from '../../api/training';

type Task = { id: string; name: string; detail: string; status: string; created: string; to: string; progress?: number; kind: 'automation' | 'semantic' | 'validation' | 'training' };
const labels: Record<string, string> = { pending: '等待中', running: '处理中', completed: '已完成', failed: '失败', canceled: '已取消', cancelled: '已取消', draft: '待启动', starting: '准备中', stopping: '停止中', unknown: '状态待核对' };
const active = ['pending', 'running', 'starting', 'stopping'];
const icons = { automation: <TagsOutlined />, semantic: <FileImageOutlined />, validation: <CheckCircleOutlined />, training: <ApiOutlined /> };

export default function DashboardQueue({ admin }: { admin: boolean }) {
  const [tasks, setTasks] = useState<Task[]>([]), [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true), [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      const sources: Promise<Task[]>[] = [
        getAutomationJobs({ page: 1, page_size: 3 }, controller.signal).then(data => data.items.map(job => ({ id: job.id, name: job.kind === 'review' ? 'AI 审核' : '预标注与 AI 审核', detail: `${job.processed} / ${job.total} 张已处理`, status: job.status, created: job.created_at, to: '/automation', kind: 'automation' as const, progress: job.total ? job.processed / job.total * 100 : undefined }))),
        getSemanticJobs(undefined, 1, controller.signal).then(data => data.slice(0, 3).map(job => ({ id: job.id, name: '图片库生成历史', detail: `${job.total} 张图片`, status: (['running', 'pending', 'failed', 'cancelled'] as const).find(status => (job.counts[status] || 0) > 0) || (job.counts.completed === job.total ? 'completed' : 'unknown'), created: job.created_at, to: '/image-semantics/history', kind: 'semantic' as const, progress: job.total ? ((job.counts.completed || 0) + (job.counts.failed || 0) + (job.counts.cancelled || 0)) / job.total * 100 : undefined }))),
        getValidationRuns({ page: 1, page_size: 3 }, controller.signal).then(data => data.items.map(run => ({ id: run.id, name: '模型验证', detail: run.name, status: run.status, created: run.created_at, to: '/validation', kind: 'validation' as const, progress: run.total ? run.processed / run.total * 100 : undefined }))),
      ];
      if (admin) sources.push(getTrainingRuns(1, controller.signal).then(data => data.items.slice(0, 3).map(run => ({ id: run.id, name: '模型训练', detail: run.name, status: run.status, created: run.created_at, to: '/training', kind: 'training' as const }))));
      const results = await Promise.allSettled(sources);
      if (controller.signal.aborted) return;
      setFailed(results.some(result => result.status === 'rejected'));
      setTasks(results.flatMap(result => result.status === 'fulfilled' ? result.value : [])
        .sort((a, b) => Number(active.includes(b.status)) - Number(active.includes(a.status)) || Date.parse(b.created) - Date.parse(a.created)).slice(0, 3));
      setLoading(false);
    };
    void load();
    return () => controller.abort();
  }, [admin, revision]);
  return <Card className="dashboard-panel dashboard-queue" title={<h2>处理队列</h2>} loading={loading}
    extra={<Button type="text" aria-label="刷新处理队列" icon={<ReloadOutlined />} onClick={() => setRevision(value => value + 1)} />}>
    {failed && <Alert type="warning" showIcon title="部分任务读取失败，可刷新重试" />}
    {tasks.length ? <div className="dashboard-queue-list">{tasks.map(task => <Link key={`${task.kind}-${task.id}`} to={task.to} className="dashboard-queue-row">
      <span className="dashboard-task-icon">{icons[task.kind]}</span><div><strong>{task.name}</strong><p title={task.detail}>{task.detail}</p>
        {task.progress !== undefined && active.includes(task.status) && <Progress percent={task.progress} showInfo={false} size="small" strokeColor="var(--ui-primary)" />}</div>
      <Tag color={active.includes(task.status) ? 'processing' : task.status === 'completed' ? 'success' : task.status === 'failed' || task.status === 'unknown' ? 'error' : 'default'}>{labels[task.status] || task.status}</Tag>
    </Link>)}</div> : <Empty description={failed ? '任务读取失败，请刷新重试' : '暂无处理任务'} />}
    <p className="dashboard-panel-note">最近任务，进行中的任务优先 · 只读展示</p>
  </Card>;
}

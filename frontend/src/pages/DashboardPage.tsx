import { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Progress } from 'antd';
import { FileImageOutlined, FolderOpenOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { getProjects } from '../api/projects';
import type { Project } from '../api/projects';
import { getImages } from '../api/images';
import type { LibraryImage } from '../api/images';
import apiClient from '../api/client';
import AssetImage from '../components/AssetImage';
import PagedAssetPreview from '../components/PagedAssetPreview';
import DashboardQueue from '../components/dashboard/DashboardQueue';
import DashboardGpu from '../components/dashboard/DashboardGpu';
import { useCurrentUser } from '../components/currentUser';
import './DashboardPage.css';

interface Overview {
  assets_overview: { total: number; project_references: number };
  annotations_overview: { total: number; approved: number; pending_review: number };
}

export default function DashboardPage() {
  const [overview, setOverview] = useState<Overview>();
  const [projects, setProjects] = useState<Project[]>([]);
  const [images, setImages] = useState<LibraryImage[]>([]);
  const [loading, setLoading] = useState(true), [errors, setErrors] = useState<string[]>([]);
  const [revision, setRevision] = useState(0), [preview, setPreview] = useState<number>();
  const admin = useCurrentUser()?.role === 'admin';
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const load = async () => {
      setLoading(true); setErrors([]); setPreview(undefined);
      const [stats, projectList, imageList] = await Promise.allSettled([
        apiClient.get<Overview>('/api/stats/dashboard', { signal: controller.signal }),
        getProjects({ page: 1, page_size: 5 }),
        getImages({ page: 1, page_size: 4 }, controller.signal),
      ]);
      if (!active) return;
      setOverview(stats.status === 'fulfilled' ? stats.value.data : undefined);
      setProjects(projectList.status === 'fulfilled' ? projectList.value.items : []);
      setImages(imageList.status === 'fulfilled' ? imageList.value.items : []);
      setErrors([stats.status === 'rejected' ? '统计读取失败' : '', projectList.status === 'rejected' ? '项目读取失败' : '', imageList.status === 'rejected' ? '图片读取失败' : ''].filter(Boolean));
      setLoading(false);
    };
    void load();
    return () => { active = false; controller.abort(); };
  }, [revision]);
  const project = projects[0];
  const progress = project?.total_assets ? Math.floor(project.reviewed_count * 100 / project.total_assets) : 0;
  const reviewLink = project ? `/annotations?project_id=${encodeURIComponent(project.id)}&review_status=pending` : '/annotations';
  const unannotated = overview && Number.isFinite(overview.assets_overview.project_references)
    ? Math.max(0, overview.assets_overview.project_references - overview.annotations_overview.total) : undefined;
  const metricGroups = [
    { title: '共享图片库', icon: <FileImageOutlined />, metrics: [
      { label: '独立原图', value: overview?.assets_overview.total },
    ] },
    { title: '项目图片', icon: <FolderOpenOutlined />, metrics: [
      { label: '已审核通过', value: overview?.annotations_overview.approved },
      { label: '待审核 / 返工', value: overview?.annotations_overview.pending_review, attention: (overview?.annotations_overview.pending_review ?? 0) > 0 },
      { label: '待标注', value: unannotated },
    ] },
  ];
  return <div className="dashboard-shell">
    {errors.length > 0 && <Alert type="error" showIcon title={errors.join('；')} action={<Button onClick={() => setRevision(value => value + 1)}>重试概览</Button>} />}
    <section className="dashboard-metrics" aria-label="数据概况" aria-busy={loading}>
      {metricGroups.map(group => <div className="dashboard-metric-group" key={group.title}>
        <h2><span aria-hidden="true">{group.icon}</span>{group.title}</h2>
        <dl>{group.metrics.map(metric => <div className={`dashboard-metric${metric.attention ? ' dashboard-metric-attention' : ''}`} key={metric.label}>
          <dt>{metric.label}</dt>
          <dd>{loading || metric.value == null ? '—' : metric.value.toLocaleString()}<small>张</small></dd>
        </div>)}</dl>
      </div>)}
    </section>
    <div className="dashboard-grid">
      <Card className="dashboard-panel dashboard-current" title={<h2>当前工作</h2>} extra={<Link to="/projects">项目列表</Link>} loading={loading}>
        {project ? <div className="dashboard-work">
          <div>
            <h3><Link className="dashboard-project-name" to={`/projects/${project.id}/images`}>{project.project_name}</Link></h3>
            {project.description && <p className="dashboard-project-description" title={project.description}>{project.description}</p>}
          </div>
          <div className="dashboard-project-progress">
            <div className="dashboard-progress-heading">
              <span>{project.task_type === 'vector' ? '图片描述 / 分类审核进度' : '审核进度'}</span>
              <strong>{progress}%</strong>
            </div>
            <Progress percent={progress} showInfo={false} strokeColor="var(--ui-primary)" size="small" />
            <p>已通过 <b>{project.reviewed_count.toLocaleString()}</b> / {project.total_assets.toLocaleString()} 张</p>
          </div>
          <div className="dashboard-work-actions">
            <Link className="dashboard-primary-link" to={reviewLink}>{project.task_type === 'vector' ? '审核匹配与描述 →' : '查看待审图片 →'}</Link>
            <Link to={`/projects/${project.id}/images`}>项目图片</Link>
          </div>
        </div> : <Empty description={errors.includes('项目读取失败') ? '项目读取失败，请重试概览' : '暂无标注项目'}><Link to="/projects">前往项目管理</Link></Empty>}
      </Card>
      <Card className="dashboard-panel dashboard-gallery" title={<h2>图片巡览</h2>} extra={<Link to="/projects/data-sources">浏览图片库</Link>} loading={loading}>
        {images.length ? <div className="dashboard-mosaic" data-count={images.length}>
          {images.map((image, index) => <button type="button" className="dashboard-photo" aria-label={`预览 ${image.original_name}`} key={image.id} onClick={() => setPreview(index)}>
            <AssetImage imageId={image.id} alt={image.original_name} preview={false} width="100%" height="100%" />
            <span className="dashboard-photo-caption">
              <span>{image.original_name}</span>
              {image.scene_labels[0] && <small>{image.scene_labels[0]}</small>}
            </span>
          </button>)}
        </div> : <Empty description={errors.includes('图片读取失败') ? '图片读取失败，请重试概览' : '图片库暂无图片'}><Link to="/projects/data-sources">前往图片管理</Link></Empty>}
      </Card>
      <DashboardQueue admin={admin} />
      <DashboardGpu admin={admin} />
    </div>
    {preview !== undefined && images[preview] && <PagedAssetPreview rows={images} index={preview} page={1} pageSize={4} total={images.length} filters={{}} onClose={() => setPreview(undefined)} />}
  </div>;
}

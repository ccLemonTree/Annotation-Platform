import { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Progress, Tag } from 'antd';
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
  const [admin] = useState(() => {
    try { return JSON.parse(localStorage.getItem('user') || 'null')?.role === 'admin'; }
    catch { return false; }
  });
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const load = async () => {
      setLoading(true); setErrors([]); setPreview(undefined);
      const [stats, projectList, imageList] = await Promise.allSettled([
        apiClient.get<Overview>('/api/stats/dashboard', { signal: controller.signal }),
        getProjects({ page: 1, page_size: 5 }),
        getImages({ page: 1, page_size: 4 }),
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
  const progress = project?.total_assets ? Math.round(project.reviewed_count / project.total_assets * 100) : 0;
  const reviewLink = project ? `/annotations?project_id=${encodeURIComponent(project.id)}&review_status=pending` : '/annotations';
  const unannotated = overview && Number.isFinite(overview.assets_overview.project_references)
    ? Math.max(0, overview.assets_overview.project_references - overview.annotations_overview.total) : undefined;
  const metrics = [
    { label: '图片资产', value: overview?.assets_overview.total, note: '原图共享，标注独立' },
    { label: '已审核通过', value: overview?.annotations_overview.approved, note: '按项目图片统计' },
    { label: '待审核 / 返工', value: overview?.annotations_overview.pending_review, note: '包含待审核与驳回', attention: true },
    { label: '待标注', value: unannotated, note: '按项目使用记录统计' },
  ];
  return <div className="dashboard-shell">
    <nav className="dashboard-tabs" aria-label="数据工作区"><Link to="/" aria-current="page">工作概览</Link><Link to="/assets">图片管理</Link><span>查看图片、项目与处理进度</span></nav>
    {errors.length > 0 && <Alert type="error" showIcon title={errors.join('；')} action={<Button onClick={() => setRevision(value => value + 1)}>重试概览</Button>} />}
    <section className="dashboard-metrics" aria-label="数据概况" aria-busy={loading}>
      {metrics.map(metric => <div className={`dashboard-metric${metric.attention ? ' dashboard-metric-attention' : ''}`} key={metric.label}>
        <span>{metric.label}</span><strong>{loading || metric.value == null ? '--' : metric.value.toLocaleString()}<small>张</small></strong><p>{metric.note}</p>
      </div>)}
    </section>
    <div className="dashboard-grid">
      <Card className="dashboard-panel dashboard-current" title={<h2>当前工作</h2>} extra={<Tag color="blue">人工复核</Tag>} loading={loading}>
        {project ? <div className="dashboard-work">
          <div><Link className="dashboard-project-name" to={`/projects/${project.id}/images`}>{project.project_name}</Link>
            <h3>让每一张图片，<br />成为有效的训练数据。</h3><p>先确认标注结果，再完成人工审核与边界情况复核。</p></div>
          <div className="dashboard-project-progress"><div><span>{project.task_type==='vector'?'图片描述 / 分类审核进度（不含匹配判定）':'当前项目审核进度'}</span><strong>{progress}%</strong></div>
            <Progress percent={progress} showInfo={false} strokeColor="var(--ui-primary)" size="small" />
            <small>{project.reviewed_count.toLocaleString()} / {project.total_assets.toLocaleString()} 张已通过</small></div>
          <div className="dashboard-work-actions"><Link className="dashboard-primary-link" to={reviewLink}>{project.task_type==='vector'?'审核匹配与描述 →':'查看待审图片 →'}</Link><Link to={`/projects/${project.id}/images`}>项目图片 ↗</Link></div>
        </div> : <Empty description="暂无标注项目"><Link to="/projects">前往项目管理</Link></Empty>}
      </Card>
      <Card className="dashboard-panel dashboard-gallery" title={<h2>图片巡览</h2>} extra={<Link to="/assets">浏览图片库 →</Link>} loading={loading}>
        {images.length ? <><div className={`dashboard-mosaic${images.length === 1 ? ' dashboard-mosaic-single' : ''}`}>
          {images.map((image, index) => <button type="button" className="dashboard-photo" aria-label={`预览 ${image.original_name}`} key={image.id} onClick={() => setPreview(index)}>
            <AssetImage imageId={image.id} alt={image.original_name} preview={false} width="100%" height="100%" />
            <span className="dashboard-photo-caption" title={image.original_name}>{image.scene_labels[0] || image.original_name}</span>
          </button>)}
        </div><p className="dashboard-panel-note">图片库真实原图 · 点击放大查看</p></> : <Empty description={errors.includes('图片读取失败') ? '图片读取失败，请重试概览' : '图片库暂无图片'}><Link to="/assets">前往图片管理</Link></Empty>}
      </Card>
      <DashboardQueue admin={admin} />
      <DashboardGpu admin={admin} />
    </div>
    <footer className="dashboard-footer"><span>原图共享 · 标注独立 · 版本可追溯</span><span>统计按最近一次读取展示；任务和算力可单独刷新</span></footer>
    {preview !== undefined && images[preview] && <PagedAssetPreview rows={images} index={preview} page={1} pageSize={4} total={images.length} filters={{}} onClose={() => setPreview(undefined)} />}
  </div>;
}

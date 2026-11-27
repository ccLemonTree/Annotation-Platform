import { useState, useEffect, useRef } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  App,
  Card,
  Button,
  Pagination,
  Spin,
  Empty,
  Progress,
  Modal,
  Form,
  Input,
  Select,
  Popconfirm,
  Alert,
} from 'antd';
import {
  AimOutlined, AppstoreOutlined, ArrowRightOutlined, BlockOutlined, DeleteOutlined,
  EditOutlined, FileTextOutlined, FolderOutlined, NodeIndexOutlined, PlusOutlined,
  SearchOutlined, TeamOutlined,
} from '@ant-design/icons';
import { isAxiosError } from 'axios';
import {
  getProjects,
  getProject,
  getProjectUsedLabels,
  updateProject,
  deleteProject,
  type Project,
  type ProjectStatus,
} from '../api/projects';

import CreateProjectModal from '../components/CreateProjectModal';
import ProjectAssignments from '../components/ProjectAssignments';
import ProjectWorkflow from '../components/ProjectWorkflow';
import './ProjectsPage.css';

const { TextArea } = Input;

const projectTasks = {
  detection: { label: '目标检测', icon: AimOutlined },
  classification: { label: '图片分类', icon: AppstoreOutlined },
  caption: { label: '图片描述与属性', icon: FileTextOutlined },
  vector: { label: '向量匹配', icon: NodeIndexOutlined },
  segmentation: { label: '图像分割', icon: BlockOutlined },
};
const projectStatuses = { draft: '草稿', active: '进行中', completed: '已完成', archived: '已归档' };
const projectNumber = new Intl.NumberFormat('zh-CN');

function ProjectCard({ project }: { project: Project }) {
  const task = projectTasks[project.task_type];
  const TaskIcon = task?.icon ?? FolderOutlined;
  const percent = project.total_assets ? Math.floor(project.reviewed_count * 100 / project.total_assets) : 0;
  return (
    <article className="project-card" aria-label={project.project_name}>
      <Link className="project-card-link" to={`/projects/${project.id}/images`} aria-label={`进入项目 ${project.project_name}`}>
        <div className="project-card-meta">
          <span className="project-card-task"><TaskIcon aria-hidden />{task?.label ?? project.task_type}</span>
          <span className="project-card-status" data-status={project.status}>{projectStatuses[project.status] ?? project.status}</span>
        </div>
        <h2>{project.project_name}</h2>
        {project.description && <p className="project-card-description">{project.description}</p>}
        <dl className="project-card-counts">
          <div><dt>图片</dt><dd>{projectNumber.format(project.total_assets)}</dd></div>
          <div><dt>已标注</dt><dd>{projectNumber.format(project.annotated_count)}</dd></div>
          <div><dt>已审核</dt><dd>{projectNumber.format(project.reviewed_count)}</dd></div>
        </dl>
        <div className="project-card-footer">
          <div className="project-card-progress">
            <div><span>审核进度</span><strong>{percent}%</strong></div>
            <Progress aria-label="项目审核进度" percent={percent} size="small" showInfo={false} />
          </div>
          <span className="project-card-enter">{project.total_assets ? '进入项目' : '添加第一批图片'}<ArrowRightOutlined aria-hidden /></span>
        </div>
      </Link>
    </article>
  );
}

export default function ProjectsPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { projectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [loadError, setLoadError] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const projectRequest = useRef(0);
  const [assignmentProject,setAssignmentProject] = useState<Project>();
  const [loading, setLoading] = useState(true);
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [pagination, setPagination] = useState({ page: 1, page_size: 10, total: 0 });
  const [searchText, setSearchText] = useState('');
  const [composingSearch, setComposingSearch] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [statusFilter, setStatusFilter] = useState<ProjectStatus | ''>('');
  const [revision, setRevision] = useState(0);
  const [form] = Form.useForm();
  const configuredLabels: string[] = Form.useWatch('labels', form) || [];
  const taskType = Form.useWatch('task_type', form) || 'detection';
  const changingTaskType = !!editingProject && taskType !== editingProject.task_type;
  const [usedLabels, setUsedLabels] = useState<string[]>([]);
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [labelsLoading, setLabelsLoading] = useState(false);
  const [labelsError, setLabelsError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [saveError, setSaveError] = useState('');
  useEffect(() => {
    if (!modalVisible || !editingProject || !['detection','classification'].includes(editingProject.task_type)) return;
    let active = true;
    setLabelsLoading(true); setLabelsError(''); setUsedLabels([]); setRenames({});
    getProjectUsedLabels(editingProject.id)
      .then(data => { if (active) setUsedLabels(data.labels || []); })
      .catch(() => { if (active) setLabelsError('读取已标注类别失败，请重新打开编辑窗口'); })
      .finally(() => { if (active) setLabelsLoading(false); });
    return () => { active = false; };
  }, [modalVisible, editingProject]);

  useEffect(() => {
    const next = searchText.trim();
    if (composingSearch || next === keyword) return;
    const timer = window.setTimeout(() => {
      setKeyword(next);
      setPagination(previous => ({ ...previous, page: 1 }));
    }, next ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [composingSearch, searchText, keyword]);

  useEffect(() => {
    let pending = false;
    const load = async (quiet = false) => {
      if (pending || quiet && document.hidden) return;
      pending = true;
      try { await fetchProjects(quiet); }
      finally { pending = false; }
    };
    const refresh = () => { void load(true); };
    void load();
    const timer = setInterval(refresh, 15000);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      projectRequest.current++;
      clearInterval(timer);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [pagination.page, pagination.page_size, revision, projectId, keyword, statusFilter]);

  const fetchProjects = async (quiet = false) => {
    const request = ++projectRequest.current;
    try {
      if (!quiet) setLoading(true);
      const response = projectId ? {items:[await getProject(projectId)], total:1} : await getProjects({
        page: pagination.page,
        page_size: pagination.page_size,
        q: keyword || undefined,
        status: statusFilter || undefined,
      });
      if (request !== projectRequest.current) return;
      setLoadError(false);
      setProjects(response.items);
      setPagination(prev => ({ ...prev, total: response.total }));
    } catch (error) {
      if (request === projectRequest.current && !quiet) setLoadError(true);
    } finally {
      if (request === projectRequest.current) setLoading(false);
    }
  };

  const handleCreate = () => {
    setCreateModalVisible(true);
  };

  const handleEdit = (project: Project) => {
    setSaveError('');
    setEditingProject(project);
    form.setFieldsValue({ ...project, labels: project.label_schema?.labels || [],classification_mode:project.label_schema?.classification_mode||'single' });
    setModalVisible(true);
  };

  const handleDelete = async (projectId: string) => {
    try {
      await deleteProject(projectId);
      message.success('删除成功');
      navigate('/projects');
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleSubmit = async () => {
    if (!editingProject) return;
    const validated = await form.validateFields().catch(() => null);
    if (!validated) return;
    try {
      const { labels = editingProject.label_schema?.labels || [], classification_mode, ...values } = validated;
      if (submitting || labelsLoading || labelsError) return;
      const normalized = labels.map((label: string) => label.trim());
      const mapping = changingTaskType ? {} : Object.fromEntries(Object.entries(renames).filter(([source, target]) => target && source !== target));
      if (!changingTaskType && usedLabels.some(source => !normalized.includes(mapping[source] || source))) {
        message.error('已有框使用了被移除的类别，请指定旧类别到新类别的对应关系'); return;
      }
      const payload = {
        ...values,
        label_schema: { ...(editingProject.label_schema || {}), labels: normalized },
        label_renames: mapping,
        base_label_schema: editingProject.label_schema,
      };
      if (values.task_type === 'classification') payload.label_schema.classification_mode = classification_mode || 'single';
      setSubmitting(true);
      setSaveError('');

      const { task_type, ...changes } = payload;
      await updateProject(editingProject.id, changingTaskType ? { ...changes, task_type } : changes);
      message.success('更新成功');

      setModalVisible(false);
      fetchProjects();
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : undefined;
      setSaveError(typeof detail === 'string' ? detail : '更新失败，请重新打开最新配置后重试');
    } finally { setSubmitting(false); }
  };

  const project = projectId ? projects.find(item => item.id === projectId) : undefined;
  const labels = project?.label_schema?.labels;
  const hasFilters = Boolean(keyword || statusFilter);
  const emptyProjectList = !loading && !loadError && !projects.length && !hasFilters && !searchText && pagination.page === 1;
  const originalLabels = editingProject?.label_schema?.labels;
  const mappingSources = [...new Set<string>([...(Array.isArray(originalLabels) ? originalLabels : []), ...usedLabels])];
  useEffect(() => {
    if (project && searchParams.get('edit') === '1') {
      handleEdit(project);
      const next = new URLSearchParams(searchParams);
      next.delete('edit');
      setSearchParams(next, { replace: true });
    }
  }, [project, searchParams]);
  return (
    <div>
      {assignmentProject&&<ProjectAssignments project={assignmentProject} onClose={()=>setAssignmentProject(undefined)}/>}
      {loadError&&<Alert type="error" showIcon title="读取项目失败" action={<Button onClick={()=>setRevision(n=>n+1)}>重试</Button>} style={{marginBottom:16}}/>}
      {projectId ? project ? <>
        <ProjectWorkflow project={project} active="settings"/>
        <Card className="project-settings">
          <header className="project-settings-heading">
            <h2>基本信息</h2>
            <Button type="primary" icon={<EditOutlined aria-hidden />} onClick={() => handleEdit(project)}>编辑名称与类别</Button>
          </header>
          <dl className="project-settings-fields">
            <div><dt>任务类型</dt><dd>{projectTasks[project.task_type]?.label ?? project.task_type}</dd></div>
            <div><dt>项目状态</dt><dd><span className="project-card-status" data-status={project.status}>{projectStatuses[project.status] ?? project.status}</span></dd></div>
            {(project.task_type === 'classification' || project.task_type === 'detection' || (Array.isArray(labels) && labels.length > 0)) && (
              <div className="project-settings-wide"><dt>标注类别</dt><dd>
                {Array.isArray(labels) && labels.length ? <ul className="project-settings-labels">
                  {labels.map(label => <li key={label}>{label}</li>)}
                </ul> : '尚未设置'}
              </dd></div>
            )}
            {project.description && <div className="project-settings-wide"><dt>项目描述</dt><dd>{project.description}</dd></div>}
          </dl>
          <footer className="project-settings-actions">
            <Button icon={<TeamOutlined aria-hidden />} onClick={() => setAssignmentProject(project)}>分配项目成员</Button>
            <Popconfirm title="确定删除项目？" description="项目及其标注、审核记录将永久删除。"
              onConfirm={() => handleDelete(project.id)} okText="删除项目" okButtonProps={{ danger: true }} cancelText="取消">
              <Button type="text" danger icon={<DeleteOutlined aria-hidden />}>删除项目</Button>
            </Popconfirm>
          </footer>
        </Card>
      </> : loading ? <Spin/> : null : <>
        {!emptyProjectList && <div className="project-list-toolbar">
          <Input className="project-list-search" type="search" prefix={<SearchOutlined aria-hidden />}
            aria-label="搜索项目名称" placeholder="按项目名称搜索" allowClear maxLength={200}
            value={searchText} onChange={event=>setSearchText(event.target.value)}
            onCompositionStart={()=>setComposingSearch(true)} onCompositionEnd={event=>{
              setSearchText(event.currentTarget.value);
              setComposingSearch(false);
            }} />
          <Select<ProjectStatus | ''> className="project-list-status" aria-label="按项目状态筛选"
            value={statusFilter} onChange={value => {
              setStatusFilter(value);
              setPagination(previous => ({ ...previous, page: 1 }));
            }}
            options={[{ value: '', label: '全部状态' }, ...Object.entries(projectStatuses).map(([value, label]) => ({ value, label }))]} />
          <span className="project-list-total" role="status">
            {!loading && !loadError && `${hasFilters ? '匹配' : '共'} ${projectNumber.format(pagination.total)} 个项目`}
          </span>
          <Button type="primary" icon={<PlusOutlined aria-hidden />} onClick={handleCreate}>新建项目</Button>
        </div>}
        <Spin spinning={loading}>
          <div className="project-list-results" aria-busy={loading}>
            {!projects.length && !loading && !loadError ? (
              <Empty className="project-list-empty" image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={hasFilters ? '未找到匹配的项目' : '还没有项目'}>
                {hasFilters ? <Button onClick={() => {
                  setSearchText('');
                  setKeyword('');
                  setStatusFilter('');
                  setPagination(previous => ({ ...previous, page: 1 }));
                }}>清除筛选</Button> :
                  <Button type="primary" onClick={handleCreate}>创建第一个项目</Button>}
              </Empty>
            ) : <div className="project-card-grid">
              {projects.map(item => <ProjectCard key={item.id} project={item} />)}
            </div>}
          </div>
        </Spin>
        {(pagination.total > 10 || pagination.page > 1) && <div className="project-list-pagination">
          <Pagination current={pagination.page} pageSize={pagination.page_size} total={pagination.total} showSizeChanger
            responsive onChange={(page,pageSize)=>setPagination(previous=>({...previous,page,page_size:pageSize}))} />
        </div>}
      </>}

      <CreateProjectModal
        open={createModalVisible}
        onCancel={() => setCreateModalVisible(false)}
        onSuccess={created => {
          setCreateModalVisible(false);
          navigate(`/projects/${created.id}/images`);
        }}
      />

      <Modal
        title="编辑项目"
        open={modalVisible && !!editingProject}
        confirmLoading={submitting}
        okButtonProps={{ disabled: labelsLoading || !!labelsError }}
        onOk={handleSubmit}
        onCancel={() => { if (!submitting) setModalVisible(false); }}
        closable={!submitting}
        mask={{closable:!submitting}}
        width={600}
        styles={{ body: { maxHeight: '72dvh', overflowY: 'auto' } }}
        okText="保存设置"
        cancelText="取消"
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ task_type: 'detection',classification_mode:'single',labels:[] }}
        >
          <Form.Item
            name="project_name"
            label="项目名称"
            rules={[
              { required: true, message: '请输入项目名称' },
              { max: 200, message: '项目名称不能超过200个字符' },
            ]}
          >
            <Input maxLength={200} />
          </Form.Item>

          <Form.Item name="task_type" label="任务类型" rules={[{required:true}]}>
            <Select options={[{value:'detection',label:'目标检测'},{value:'caption',label:'图片描述与属性'},
              {value:'classification',label:'图片分类'},{value:'vector',label:'向量匹配'},{value:'segmentation',label:'图像分割',disabled:true}]}/>
          </Form.Item>

          {taskType==='classification'&&<Form.Item name="classification_mode" label="分类方式"><Select options={[{value:'single',label:'单选分类'},{value:'multi',label:'多标签分类'}]}/></Form.Item>}
          {(taskType==='classification'||editingProject&&taskType==='detection')&&<Form.Item name="labels" label="标注类别" extra="按回车添加类别"
            rules={[{ type: 'array', required: true, min: 1, message: '请至少设置一个类别' },
              { validator: (_, labels: string[]=[]) => labels.every(label => label.trim()) && new Set(labels.map(label => label.trim())).size === labels.length
                ? Promise.resolve() : Promise.reject(new Error('类别不能空白或重复')) }]}>
            <Select aria-label="项目标注类别" mode="tags" tokenSeparators={[',', '，']} />
          </Form.Item>}

          {editingProject&&<>
          {labelsError && <Alert type="error" showIcon title={labelsError} />}
          {saveError && <Alert type="error" showIcon title={saveError} style={{ marginBottom: 16 }} />}
          {labelsLoading && <p role="status">正在读取已有类别…</p>}
          {!changingTaskType && ['detection','classification'].includes(taskType) && mappingSources.length > 0 && <div style={{ marginBottom: 20 }}>
            <h3>同步已有标注类别</h3>
            <p style={{ color: 'var(--ui-muted)' }}>更名会生成待审核的新版本，保留历史标注和冻结包。</p>
            {mappingSources.map(source =>
              <div key={source} style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 8 }}>
                <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{source}{usedLabels.includes(source) ? '（已有框）' : ''}</span>
                <span>→</span>
                <Select aria-label={`类别映射 ${source}`} style={{ width: 220, maxWidth: '100%' }} allowClear disabled={labelsLoading}
                  placeholder={configuredLabels.includes(source) ? '保留名称' : '请选择新类别'}
                  value={renames[source] || undefined} onChange={target => setRenames(previous => ({ ...previous, [source]: target || '' }))}
                  options={configuredLabels.map(label => ({ label, value: label.trim() }))} />
              </div>)}
          </div>}
          <Form.Item
            name="description"
            label="项目描述（选填）"
          >
            <TextArea
              rows={3}
              maxLength={500}
              showCount
            />
          </Form.Item>

          {editingProject && (
            <Form.Item
              name="status"
              label="项目状态"
            >
              <Select
                options={[
                  { label: '进行中', value: 'active' },
                  { label: '已归档', value: 'archived' },
                ]}
              />
            </Form.Item>
          )}
          </>}
        </Form>
      </Modal>
    </div>
  );
}

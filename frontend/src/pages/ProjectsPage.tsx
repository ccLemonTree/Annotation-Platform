// 项目管理页面
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
  Space,
  Tag,
  Popconfirm,
  Alert,
  Collapse,
} from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { isAxiosError } from 'axios';
import {
  getProjects,
  getProject,
  getProjectUsedLabels,
  updateProject,
  deleteProject,
  type Project,
} from '../api/projects';

import CreateProjectModal from '../components/CreateProjectModal';
import ProjectAssignments from '../components/ProjectAssignments';
import ProjectWorkflow from '../components/ProjectWorkflow';
import './ProjectsPage.css';

const { TextArea } = Input;

export default function ProjectsPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { projectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [loadError, setLoadError] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const projectRequest = useRef(0);
  const [assignmentProject,setAssignmentProject] = useState<Project>();
  const [loading, setLoading] = useState(false);
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [pagination, setPagination] = useState({ page: 1, page_size: 10, total: 0 });
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
  }, [pagination.page, pagination.page_size, revision, projectId]);

  const fetchProjects = async (quiet = false) => {
    const request = ++projectRequest.current;
    try {
      if (!quiet) setLoading(true);
      const response = projectId ? {items:[await getProject(projectId)], total:1} : await getProjects({
        page: pagination.page,
        page_size: pagination.page_size,
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
        <Card title="项目设置">
          <p className="workspace-help">管理项目名称、标注类别和成员。</p>
          <Space wrap><Button type="primary" icon={<EditOutlined/>} onClick={()=>handleEdit(project)}>编辑名称与类别</Button>
            <Button onClick={()=>setAssignmentProject(project)}>分配项目成员</Button></Space>
          <p>标注类别：{(project.label_schema?.labels as string[]||[]).join('、')||'尚未设置，可在开始标注前添加'}</p>
          <Collapse items={[{key:'danger',label:'删除项目',children:<><p>删除此项目及其标注、审核记录。此操作不可撤销。</p><Popconfirm title="确定删除此项目及其标注吗？" onConfirm={()=>handleDelete(project.id)} okText="删除项目" cancelText="取消"><Button danger icon={<DeleteOutlined/>}>删除项目</Button></Popconfirm></>}]} />
        </Card>
      </> : loading ? <Spin/> : null : <>
        <div className="page-toolbar"><p className="workspace-help">选择项目继续工作，或创建项目后添加第一批图片。</p>
          <Button type="primary" icon={<PlusOutlined/>} onClick={handleCreate}>新建项目</Button></div>
        <Spin spinning={loading}>
          {!projects.length&&!loading&&!loadError ? <Empty description="还没有项目，从项目名称开始"><Button type="primary" onClick={handleCreate}>创建第一个项目</Button></Empty> : <div className="project-card-grid">
            {projects.map(project=><Link key={project.id} className="project-card-link" to={`/projects/${project.id}/images`} aria-label={`进入项目 ${project.project_name}`}>
              <Card className="project-card" hoverable role="article" aria-label={project.project_name}
                title={project.project_name}
                extra={<Tag color={project.status==='active'?'success':'default'}>{({draft:'草稿',active:'进行中',completed:'已完成',archived:'已归档'} as Record<string,string>)[project.status]||project.status}</Tag>}>
                <p className="project-card-description">{project.description||'添加数据、完成标注与审核，再导出或训练。'}</p>
                <p className="project-card-counts">图片 {project.total_assets} · 已标注 {project.annotated_count} · 已审核 {project.reviewed_count}</p>
                <div className="project-card-progress"><span>审核进度</span><Progress aria-label="项目审核进度" percent={project.total_assets?Math.floor(project.reviewed_count*100/project.total_assets):0} size="small" /></div>
                <span className="project-card-enter">{project.total_assets?'进入项目':'添加第一批图片'} →</span>
              </Card>
            </Link>)}
          </div>}
        </Spin>
        <div style={{ marginTop: 20, display: 'flex', justifyContent: 'flex-end' }}>
          <Pagination current={pagination.page} pageSize={pagination.page_size} total={pagination.total} showSizeChanger
            showTotal={total=>`共 ${total} 个项目`} onChange={(page,pageSize)=>setPagination(previous=>({...previous,page,page_size:pageSize}))} />
        </div>
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
            <Input placeholder="请输入项目名称" />
          </Form.Item>

          <Form.Item name="task_type" label="标注格式" rules={[{required:true}]}>
            <Select options={[{value:'detection',label:'目标检测'},{value:'caption',label:'图片描述与属性'},
              {value:'classification',label:'图片分类'},{value:'vector',label:'向量匹配'},{value:'segmentation',label:'图像分割',disabled:true}]}/>
          </Form.Item>

          {taskType==='classification'&&<Form.Item name="classification_mode" label="分类方式"><Select options={[{value:'single',label:'单选分类'},{value:'multi',label:'多标签分类'}]}/></Form.Item>}
          {(taskType==='classification'||editingProject&&taskType==='detection')&&<Form.Item name="labels" label="标注类别" extra="输入类别后按回车。"
            rules={[{ type: 'array', required: true, min: 1, message: '请至少设置一个类别' },
              { validator: (_, labels: string[]=[]) => labels.every(label => label.trim()) && new Set(labels.map(label => label.trim())).size === labels.length
                ? Promise.resolve() : Promise.reject(new Error('类别不能空白或重复')) }]}>
            <Select aria-label="项目标注类别" mode="tags" tokenSeparators={[',', '，']} placeholder="例如：人、车；输入后按回车" />
          </Form.Item>}

          {editingProject&&<>
          {labelsError && <Alert type="error" showIcon title={labelsError} />}
          {saveError && <Alert type="error" showIcon title={saveError} style={{ marginBottom: 16 }} />}
          {!changingTaskType && ['detection','classification'].includes(taskType) && <div style={{ marginBottom: 20 }}>
            <h3>同步已有标注类别</h3>
            <p style={{ color: 'var(--ui-muted)' }}>选择旧类别对应的新类别。同步后最新标注生成新版本并待审核；历史和冻结包不变。{labelsLoading && ' 正在读取已有类别…'}</p>
            {[...new Set([...(Array.isArray(editingProject.label_schema?.labels) ? editingProject.label_schema.labels as string[] : []), ...usedLabels])].map(source =>
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
            label="项目描述"
          >
            <TextArea
              rows={4}
              placeholder="请输入项目描述（可选）"
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

// 项目管理页面
import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Card,
  Button,
  Table,
  Modal,
  Form,
  Input,
  Select,
  Space,
  message,
  Tag,
  Popconfirm,
  Alert,
} from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import {
  getProjects,
  createProject,
  updateProject,
  deleteProject,
  type Project,
  type ProjectCreate,
} from '../api/projects';

import { getAnnotations } from '../api/annotations';
import ContinueAnnotation from '../components/ContinueAnnotation';

const { TextArea } = Input;

export default function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [pagination, setPagination] = useState({ page: 1, page_size: 10, total: 0 });
  const [form] = Form.useForm();
  const configuredLabels: string[] = Form.useWatch('labels', form) || [];
  const taskType = Form.useWatch('task_type', form) || 'detection';
  const [usedLabels, setUsedLabels] = useState<string[]>([]);
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [labelsLoading, setLabelsLoading] = useState(false);
  const [labelsError, setLabelsError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    if (!modalVisible || !editingProject) return;
    let active = true;
    setLabelsLoading(true); setLabelsError(''); setUsedLabels([]); setRenames({});
    (async () => {
      const names = new Set<string>();
      for (let page = 1; ; page++) {
        const data = await getAnnotations({ project_id: editingProject.id, latest_only: true, page, page_size: 100 });
        for (const annotation of data.items) {
          for (const box of annotation.annotation_data.boxes || []) names.add(box.label);
          for (const label of annotation.annotation_data.labels || []) names.add(label);
        }
        if (page * 100 >= data.total || !data.items.length) break;
      }
      if (active) setUsedLabels([...names]);
    })().catch(() => { if (active) setLabelsError('读取已标注类别失败，请重新打开编辑窗口'); })
      .finally(() => { if (active) setLabelsLoading(false); });
    return () => { active = false; };
  }, [modalVisible, editingProject]);

  useEffect(() => {
    fetchProjects();
  }, [pagination.page, pagination.page_size]);

  const fetchProjects = async () => {
    try {
      setLoading(true);
      const response = await getProjects({
        page: pagination.page,
        page_size: pagination.page_size,
      });
      setProjects(response.items);
      setPagination(prev => ({ ...prev, total: response.total }));
    } catch (error) {
      message.error('获取项目列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = () => {
    setEditingProject(null); setRenames({}); setUsedLabels([]); setLabelsError(''); setLabelsLoading(false);
    form.resetFields();
    setModalVisible(true);
  };

  const handleEdit = (project: Project) => {
    setEditingProject(project);
    form.setFieldsValue({ ...project, labels: project.label_schema?.labels || [],classification_mode:project.label_schema?.classification_mode||'single' });
    setModalVisible(true);
  };

  const handleDelete = async (projectId: string) => {
    try {
      await deleteProject(projectId);
      message.success('删除成功');
      fetchProjects();
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleSubmit = async () => {
    try {
      const { labels=[], classification_mode, ...values } = await form.validateFields();
      if (submitting || labelsLoading || labelsError) return;
      const normalized = labels.map((label: string) => label.trim());
      const mapping = Object.fromEntries(Object.entries(renames).filter(([source, target]) => target && source !== target));
      if (usedLabels.some(source => !normalized.includes(mapping[source] || source))) {
        message.error('已有框使用了被移除的类别，请指定旧类别到新类别的对应关系'); return;
      }
      const payload = { ...values, label_schema: { ...(editingProject?.label_schema || {}), labels: normalized },
        ...(editingProject ? { label_renames: mapping, base_label_schema: editingProject.label_schema } : {}) };
      if(['classification','caption','vector'].includes(values.task_type))payload.label_schema.classification_mode=classification_mode||'single';
      setSubmitting(true);

      if (editingProject) {
        await updateProject(editingProject.id, payload);
        message.success('更新成功');
      } else {
        const created = await createProject(payload as ProjectCreate);
        message.success('创建成功，请为项目添加图片');
        setModalVisible(false);navigate(`/projects/${created.id}/images`);return;
      }

      setModalVisible(false);
      fetchProjects();
    } catch (error) {
      message.error(editingProject ? '更新失败，请核对类别映射或重新打开最新配置' : '创建失败');
    } finally { setSubmitting(false); }
  };

  const columns = [
    {
      title: '项目名称',
      dataIndex: 'project_name',
      render: (name: string, record: Project) => <Link to={`/projects/${record.id}/images`}>{name}</Link>,
      key: 'project_name',
      width: 200,
    },
    {
      title: '任务类型',
      dataIndex: 'task_type',
      key: 'task_type',
      width: 120,
      render: (type: string) => {
        const typeMap: Record<string, { label: string; color: string }> = {
          detection: { label: '目标检测', color: 'blue' },
          classification: { label: '图像分类', color: 'green' },
          segmentation: { label: '图像分割', color: 'purple' },
          caption: { label:'描述标注',color:'cyan' },
          vector: { label:'向量标注',color:'geekblue' },
        };
        const config = typeMap[type] || { label: type, color: 'default' };
        return <Tag color={config.color}>{config.label}</Tag>;
      },
    },
    {
      title: '标注类别',
      key: 'labels',
      width: 200,
      render: (_: unknown, record: Project) => Array.isArray(record.label_schema?.labels) && record.label_schema.labels.length
        ? <Space wrap>{(record.label_schema.labels as string[]).map(label => <Tag key={label}>{label}</Tag>)}</Space> : <Tag>未配置</Tag>,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={status === 'active' ? 'success' : 'default'}>
          {({ draft: '草稿', active: '进行中', completed: '已完成', archived: '已归档' } as Record<string, string>)[status] || status}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (date: string) => new Date(date).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 420,
      fixed: 'right' as const,
      render: (_: any, record: Project) => (
        <Space>
          <Link to={`/projects/${record.id}/images`}>管理图片</Link>
          <Link to={`/projects/${record.id}/prepare`}>标注准备</Link>
          <ContinueAnnotation projectId={record.id} taskType={record.task_type}/>
          <Link to={`/annotations?project_id=${record.id}`}>审核</Link>
          <Link to={`/projects/${record.id}/deliver`}>导出 / 训练</Link>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确定删除此项目吗？"
            onConfirm={() => handleDelete(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button
              type="link"
              danger
              size="small"
              icon={<DeleteOutlined />}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title="项目列表"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleCreate}
          >
            新建项目
          </Button>
        }
      >
        <Table
          columns={columns}
          scroll={{ x: 1050 }}
          dataSource={projects}
          rowKey="id"
          loading={loading}
          pagination={{
            current: pagination.page,
            pageSize: pagination.page_size,
            total: pagination.total,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条`,
            onChange: (page, pageSize) => {
              setPagination(prev => ({ ...prev, page, page_size: pageSize }));
            },
          }}
        />
      </Card>

      <Modal
        title={editingProject ? '编辑项目' : '新建项目'}
        open={modalVisible}
        confirmLoading={submitting}
        okButtonProps={{ disabled: labelsLoading || !!labelsError }}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        width={600}
        okText="确定"
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

          <Form.Item
            name="task_type"
            label="任务类型"
            rules={[{ required: true, message: '请选择任务类型' }]}
          >
            <Select
              disabled={!!editingProject}
              options={[
                { label: '目标检测', value: 'detection' },
                { label: '图像分类', value: 'classification' },
                { label: '图像分割', value: 'segmentation' },
                {label:'大模型描述标注',value:'caption'},
                {label:'向量匹配标注',value:'vector'},
              ]}
            />
          </Form.Item>

          {['classification','caption','vector'].includes(taskType)&&<Form.Item name="classification_mode" label="分类方式"><Select options={[{value:'single',label:'单选分类'},{value:'multi',label:'多标签分类'}]}/></Form.Item>}
          <Form.Item name="labels" label="标注类别" extra="输入类别后按回车。描述和向量项目可留空；类别与描述按图片共用。"
            rules={[{ type: 'array', required: !['caption','vector'].includes(taskType), min: ['caption','vector'].includes(taskType)?0:1, message: '请至少设置一个类别' },
              { validator: (_, labels: string[]=[]) => labels.every(label => label.trim()) && new Set(labels.map(label => label.trim())).size === labels.length
                ? Promise.resolve() : Promise.reject(new Error('类别不能空白或重复')) }]}>
            <Select aria-label="项目标注类别" mode="tags" tokenSeparators={[',', '，']} placeholder="例如：人、车；输入后按回车" />
          </Form.Item>

          {labelsError && <Alert type="error" showIcon title={labelsError} />}
          {editingProject && <div style={{ marginBottom: 20 }}>
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
        </Form>
      </Modal>
    </div>
  );
}

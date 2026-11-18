import { useRef, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Select, Space } from 'antd';
import { isAxiosError } from 'axios';
import { createProject, type Project, type TaskType } from '../api/projects';
import { appMessage } from '../ui/appFeedback';

export interface CreateProjectModalProps {
  open: boolean;
  onCancel: () => void;
  onSuccess: (project: Project) => void;
}

interface CreateProjectFormValues {
  project_name: string;
  task_type: TaskType;
}

const TASK_TYPE_OPTIONS: { value: TaskType; label: string; disabled?: boolean }[] = [
  { value: 'detection', label: '目标检测' },
  { value: 'caption', label: '图片描述与属性' },
  { value: 'classification', label: '图片分类' },
  { value: 'vector', label: '向量匹配' },
  { value: 'segmentation', label: '图像分割（暂不支持）', disabled: true },
];

export function CreateProjectModal({ open, onCancel, onSuccess }: CreateProjectModalProps) {
  const [form] = Form.useForm<CreateProjectFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const saving = useRef(false);

  const handleClose = () => {
    if (saving.current) return;
    form.resetFields();
    setError('');
    onCancel();
  };

  const handleSubmit = async () => {
    let values: CreateProjectFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    if (saving.current) return;
    saving.current = true;
    setSubmitting(true);
    setError('');
    try {
      const schema: Record<string, unknown> = { labels: [] };
      if (['classification', 'caption', 'vector'].includes(values.task_type)) {
        schema.classification_mode = 'single';
      }
      const created = await createProject({
        project_name: values.project_name.trim(),
        task_type: values.task_type,
        label_schema: schema,
      });
      form.resetFields();
      setError('');
      appMessage.success('项目创建成功');
      onSuccess(created);
    } catch (cause) {
      const detail = isAxiosError(cause) ? cause.response?.data?.detail : undefined;
      setError(typeof detail === 'string' ? detail : '创建项目失败，请检查项目名称后重试');
    } finally {
      saving.current = false;
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="新建项目"
      open={open}
      destroyOnHidden
      onCancel={handleClose}
      closable={!submitting}
      mask={{ closable: !submitting }}
      width={520}
      footer={
        <Space>
          <Button disabled={submitting} onClick={handleClose}>
            取消
          </Button>
          <Button type="primary" loading={submitting} onClick={() => void handleSubmit()}>
            创建项目
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical" disabled={submitting} preserve={false} initialValues={{ task_type: 'detection' }}>
        <Form.Item
          name="project_name"
          label="项目名称"
          rules={[
            { required: true, whitespace: true, message: '请输入项目名称' },
            { max: 200, message: '项目名称不能超过200个字符' },
          ]}
        >
          <Input aria-label="项目名称" placeholder="请输入项目名称" maxLength={200} />
        </Form.Item>

        <Form.Item
          name="task_type"
          label="任务类型"
          extra="创建后任务类型不能更换；标签类别、描述与人员可在进入项目后按需配置。"
          rules={[{ required: true, message: '请选择任务类型' }]}
        >
          <Select aria-label="任务类型" options={TASK_TYPE_OPTIONS} />
        </Form.Item>

        {error && <Alert type="error" showIcon title={error} />}
      </Form>
    </Modal>
  );
}

export default CreateProjectModal;

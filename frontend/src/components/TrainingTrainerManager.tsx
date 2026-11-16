import { useState } from 'react';
import { Alert, Button, Col, Drawer, Form, Input, List, Popconfirm, Row, Select, Space, Tabs, Typography } from 'antd';
import { deleteTrainingTrainer, saveBuiltinTrainerDefaults, saveTrainingTrainer } from '../api/training';
import type { TrainingTemplate, TrainingTrainer } from '../api/training';
import { ParameterInput, parameterLabels } from './TrainingParameterInput';

const formats = [
  ['yolo', '目标检测 · YOLO'], ['coco', '目标检测 · COCO'], ['swift_sft', '大模型 SFT · Swift JSONL'],
  ['infonce', '向量 · InfoNCE'], ['contrastive', '向量 · Contrastive'],
  ['online_contrastive', '向量 · Online Contrastive'], ['cosine_similarity', '向量 · Cosine Similarity'],
].map(([value, label]) => ({ value, label }));

type Fields = Omit<TrainingTrainer, 'id' | 'parameters'> & { parameters: string };

export function parseTrainingParameters(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text || '{}');
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('训练参数须为 JSON 对象');
  return value as Record<string, unknown>;
}

export default function TrainingTrainerManager({ catalog, onBuiltinChange, trainers, onChange, onClose, errorText }: {
  catalog: Record<string, TrainingTemplate>; onBuiltinChange: (kind: string, template: TrainingTemplate) => void;
  trainers: TrainingTrainer[]; onChange: (rows: TrainingTrainer[]) => void;
  onClose: () => void; errorText: (error: unknown) => string;
}) {
  const [form] = Form.useForm<Fields>();
  const [builtinForm] = Form.useForm<{ parameters: Record<string, unknown> }>();
  const [builtinKind, setBuiltinKind] = useState<string>();
  const [saved, setSaved] = useState('');
  const [editing, setEditing] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const builtin = builtinKind ? catalog[builtinKind] : undefined;

  const selectBuiltin = (kind: string) => {
    setBuiltinKind(kind);
    setError('');
    setSaved('');
    builtinForm.resetFields();
    builtinForm.setFieldValue('parameters', { ...catalog[kind].defaults });
  };

  const saveBuiltin = async (values?: { parameters: Record<string, unknown> }) => {
    if (!builtinKind || !builtin) return;
    const parameters = values ? { ...builtin.overrides } : {};
    for (const [key, value] of Object.entries(values?.parameters || {})) {
      if (value === undefined && builtin.schema.properties[key]?.anyOf?.some(field => field.type === 'null')) {
        parameters[key] = null;
      } else if (value !== undefined && value !== builtin.defaults[key]) {
        parameters[key] = value;
      }
    }
    setBusy(true);
    setError('');
    setSaved('');
    try {
      const result = await saveBuiltinTrainerDefaults(builtinKind, parameters);
      onBuiltinChange(builtinKind, result);
      builtinForm.setFieldValue('parameters', { ...result.defaults });
      setSaved(values ? '默认参数已保存，将用于新建任务。' : '已恢复平台默认参数。');
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };

  const edit = (row?: TrainingTrainer) => {
    setEditing(row?.id);
    setError('');
    form.resetFields();
    if (row) form.setFieldsValue({ name: row.name, image: row.image, module: row.module,
      dataset_format: row.dataset_format, parameters: JSON.stringify(row.parameters, null, 2) });
  };

  const save = async (values: Fields) => {
    setBusy(true);
    setError('');
    try {
      const row = await saveTrainingTrainer({ ...values, parameters: parseTrainingParameters(values.parameters) }, editing);
      onChange(editing ? trainers.map(item => item.id === row.id ? row : item) : [...trainers, row]);
      edit();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };

  const remove = async (id: string) => {
    setBusy(true);
    setError('');
    try {
      await deleteTrainingTrainer(id);
      onChange(trainers.filter(item => item.id !== id));
      if (editing === id) edit();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };

  return <Drawer open title="管理训练器" onClose={onClose} closable={!busy} keyboard={!busy}
    mask={{ closable: !busy }} size="large">
    <Tabs onChange={() => { setError(''); setSaved(''); }} items={[
      { key: 'builtin', label: '内置训练器', forceRender: true, children: <Form form={builtinForm} layout="vertical" disabled={busy} onFinish={saveBuiltin}>
        <Form.Item label="内置训练器" htmlFor="builtin-trainer">
          <Select id="builtin-trainer" value={builtinKind} onChange={selectBuiltin} placeholder="选择训练器查看默认参数"
            options={Object.entries(catalog).map(([value, template]) => ({ value, label: template.name }))} />
        </Form.Item>
        {builtin && <>
          <Typography.Paragraph type="secondary">修改后用于本租户新建任务，创建时仍可按任务调整。</Typography.Paragraph>
          <Typography.Paragraph>默认镜像 <Typography.Text code>{builtin.image}</Typography.Text></Typography.Paragraph>
          {saved && <Alert type="success" showIcon title={saved} style={{ marginBottom: 16 }} />}
          <Row gutter={16}>{Object.entries(builtin.schema.properties).map(([key, field]) => {
            const schema = field.anyOf?.find(value => value.type !== 'null') || field;
            const label = builtinKind === 'dinov3_vits16_ltdetr' && key === 'batch_size' ? '总批次大小（全部GPU）' : parameterLabels[key] || key;
            return <Col xs={24} sm={12} key={key}><Form.Item name={['parameters', key]} label={label}
              valuePropName={schema.type === 'boolean' ? 'checked' : 'value'}
              rules={['integer', 'number'].includes(schema.type || '') ? [{ required: true, message: `请填写${label}` }] : []}>
              <ParameterInput name={key} field={field} />
            </Form.Item></Col>;
          })}</Row>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={busy}>保存默认参数</Button>
            <Button disabled={!Object.keys(builtin.overrides || {}).length} onClick={() => void saveBuiltin()}>恢复平台默认</Button>
          </Space>
        </>}
      </Form> },
      { key: 'custom', label: '已登记训练器', forceRender: true, children: <>
    <List rowKey="id" dataSource={trainers} locale={{ emptyText: '还没有登记的训练器' }}
      renderItem={row => <List.Item actions={[
        <Button key="edit" type="link" disabled={busy} onClick={() => edit(row)}>编辑</Button>,
        <Popconfirm key="remove" title={`移除 ${row.name}？`} description="已保存的任务仍可运行。"
          onConfirm={() => remove(row.id)} okText="移除" cancelText="取消">
          <Button type="link" danger disabled={busy}>移除</Button>
        </Popconfirm>,
      ]}><List.Item.Meta title={row.name} description={formats.find(format => format.value === row.dataset_format)?.label} /></List.Item>} />
    <Form form={form} layout="vertical" disabled={busy} onFinish={save} initialValues={{ parameters: '{}' }}
      style={{ marginTop: 24 }}>
      <Form.Item name="name" label="训练器名称" rules={[{ required: true, whitespace: true }]}>
        <Input maxLength={120} />
      </Form.Item>
      <Form.Item name="dataset_format" label="冻结数据格式" rules={[{ required: true }]}>
        <Select options={formats} />
      </Form.Item>
      <Form.Item name="image" label="训练镜像" rules={[{ required: true }]} extra="填写已在 Docker 训练服务器准备好的镜像。">
        <Input maxLength={500} placeholder="例如 registry.example.com/team/trainer:v1" />
      </Form.Item>
      <Form.Item name="module" label="Python 训练模块" rules={[{ required: true }]}
        extra="模块须安装在镜像中，并支持 --config /output/run_config.json。训练权重写入配置指定的输出子目录。">
        <Input maxLength={200} placeholder="例如 my_trainer.train" />
      </Form.Item>
      <Form.Item name="parameters" label="默认训练参数（JSON）" extra="任务可以调整已登记的参数值。"
        rules={[{ validator: async (_, text: string) => { parseTrainingParameters(text); } }]}>
        <Input.TextArea rows={4} spellCheck={false} />
      </Form.Item>
      <Space>
        <Button type="primary" htmlType="submit" loading={busy}>{editing ? '保存修改' : '登记训练器'}</Button>
        {editing && <Button onClick={() => edit()}>取消编辑</Button>}
      </Space>
    </Form>
      </> },
    ]} />
    {error && <Alert type="error" showIcon title={error} style={{ marginTop: 16 }} />}
  </Drawer>;
}

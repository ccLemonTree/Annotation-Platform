import { useEffect, useRef, useState } from 'react';
import { Link,useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Checkbox, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { getCurrentUser } from '../api/auth';
import { addDefaultModelServices, createModelService, deleteModelService, getModelServices, getPromptDefaults, predictModelService, supportsModelPrompt, testModelService, updateModelService } from '../api/modelServices';
import type { ModelKind, ModelProtocol, ModelService, ModelServiceConfig, PredictionResult, PromptDefaults } from '../api/modelServices';
import { PredictionResultView } from '../components/ModelPrediction';
import JsonInput from '../components/JsonInput';
import TrialImagePicker from '../components/TrialImagePicker';

const kinds: Record<ModelKind, string> = { detection: '目标检测', caption: '图像描述', embedding: 'Embedding', reranker: '重排序' };
const protocols: Record<ModelProtocol, ModelKind[]> = { model_service: ['detection', 'caption'], openai_chat: ['caption'], openai_embedding: ['embedding'], openai_rerank: ['reranker'] };
const defaults: ModelServiceConfig = { key: '', name: '', base_url: '', endpoint: '/detect', protocol: 'model_service', kind: 'detection', model_name: '',
  enabled: true, timeout_seconds: 60, confidence: .25, iou: .45, labels: [], label_mapping: {}, api_key_env: '' };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type ModelForm = Omit<ModelServiceConfig, 'label_mapping'> & { label_mapping_text: string };
type TrialForm = { source: 'image_id' | 'asset_id'; reference_id: string; prompt?: string };

export default function ModelServicesPage() {
  const [params]=useSearchParams();
  const [notice, noticeContext] = message.useMessage();
  const [models, setModels] = useState<ModelService[]>([]), [admin, setAdmin] = useState(false);
  const [loading, setLoading] = useState(false), [working, setWorking] = useState(false), [error, setError] = useState('');
  const [editing, setEditing] = useState<ModelService | null>(), [editorOpen, setEditorOpen] = useState(false), [editorError, setEditorError] = useState('');
  const [defaultsOpen, setDefaultsOpen] = useState(false);
  const [promptDefaults, setPromptDefaults] = useState<PromptDefaults>();
  const [connection, setConnection] = useState<{ ok: boolean; detail: string; models?: string[] }>();
  const [trial, setTrial] = useState<ModelService>(), [trialBusy, setTrialBusy] = useState(false), [trialError, setTrialError] = useState('');
  const [trialResult, setTrialResult] = useState<{ result: PredictionResult; source: TrialForm['source']; id: string }>();
  const trialRequest = useRef(0), trialController = useRef<AbortController | null>(null);
  const [form] = Form.useForm<ModelForm>(), [trialForm] = Form.useForm<TrialForm>();
  const kind = Form.useWatch('kind', form) || 'detection';
  const protocol = Form.useWatch('protocol', form) || 'model_service';
  const source = Form.useWatch('source', trialForm) || 'image_id';
  const referenceId = Form.useWatch('reference_id', trialForm);
  const load = async () => {
    setLoading(true); setError('');
    try { setModels(await getModelServices()); }
    catch { setError('模型列表读取失败，请重试'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); getCurrentUser().then(user => setAdmin(user.role === 'admin')).catch(() => setAdmin(false));
    getPromptDefaults().then(setPromptDefaults).catch(() => setPromptDefaults(undefined));
    return () => { trialRequest.current++; trialController.current?.abort(); };
  }, []);
  const edit = (model?: ModelService) => {
    setEditing(model || null); setEditorError(''); form.resetFields();
    form.setFieldsValue({ ...defaults, ...model, label_mapping_text: JSON.stringify(model?.label_mapping || {}, null, 2),
      chat_prompts: {caption:promptDefaults?.caption||'',review:promptDefaults?.review||'',match_review:promptDefaults?.match_review||'',training_analysis:promptDefaults?.training_analysis||'',...model?.chat_prompts} });
    setEditorOpen(true);
  };
  const save = async () => {
    let values: ModelForm;
    try { values = await form.validateFields(); } catch { return; }
    if (!admin || working) return;
    const config: ModelServiceConfig = {
      key: values.key.trim(), name: values.name.trim(), base_url: values.base_url.trim(), endpoint: values.endpoint.trim(),
      protocol: values.protocol, kind: values.kind, model_name: values.model_name?.trim() || '', enabled: values.enabled,
      timeout_seconds: values.timeout_seconds, confidence: values.confidence, iou: values.iou,
      labels: values.labels || [], label_mapping: JSON.parse(values.label_mapping_text), api_key_env: values.api_key_env?.trim() || '',
      ...(values.protocol === 'openai_chat' ? { chat_prompts: values.chat_prompts } : editing?.chat_prompts ? { chat_prompts: editing.chat_prompts } : {}),
    };
    setWorking(true); setEditorError('');
    try { if (editing) await updateModelService(editing.id, config); else await createModelService(config);
      setEditorOpen(false); notice.success(editing ? '模型已更新' : '模型已创建'); await load();
    } catch { setEditorError('保存失败，请检查模型配置；当前输入已保留'); }
    finally { setWorking(false); }
  };
  const toggle = async (model: ModelService) => {
    if (!admin || working) return;
    const { id, revision: _revision, ...config } = model;
    setWorking(true); setError('');
    try { await updateModelService(id, { ...config, enabled: !model.enabled }); await load(); }
    catch { setError('启停失败，请重试'); }
    finally { setWorking(false); }
  };
  const remove = async (id: string) => {
    if (!admin || working) return;
    setWorking(true); setError('');
    try { await deleteModelService(id); await load(); notice.success('模型已删除'); }
    catch { setError('删除失败，请重试'); }
    finally { setWorking(false); }
  };
  const addDefaults = async () => {
    if (!admin || working) return;
    setWorking(true); setError('');
    try { setModels(await addDefaultModelServices()); setDefaultsOpen(false); notice.success('默认模型已补充，已有配置保留'); }
    catch { setError('添加默认模型失败，请重试'); }
    finally { setWorking(false); }
  };
  const test = async (model: ModelService) => {
    if (!admin || working) return;
    setWorking(true); setError('');
    try { setConnection(await testModelService(model.id)); }
    catch { setConnection({ ok: false, detail: '连接测试失败，请核对地址、模型 ID 或服务鉴权' }); }
    finally { setWorking(false); }
  };
  const clearTrialResult = () => { trialRequest.current++; trialController.current?.abort(); setTrialResult(undefined); setTrialError(''); setTrialBusy(false); };
  const openTrial = (model: ModelService) => {
    if (model.kind === 'reranker') return;
    clearTrialResult(); trialForm.resetFields(); trialForm.setFieldsValue({ source: 'image_id', reference_id: '', prompt: '' });
    setTrial(model);
  };
  const runTrial = async () => {
    let values: TrialForm;
    try { values = await trialForm.validateFields(); } catch { return; }
    if (!trial || trialBusy || !trial.enabled) return;
    const current = ++trialRequest.current;
    trialController.current?.abort(); trialController.current = new AbortController();
    setTrialBusy(true); setTrialError(''); setTrialResult(undefined);
    const input = values.source === 'asset_id' ? { asset_id: values.reference_id.trim() } : { image_id: values.reference_id.trim() };
    try {
      const result = await predictModelService(trial.id, { ...input, ...(supportsModelPrompt(trial) && values.prompt?.trim() ? { prompt: values.prompt.trim() } : {}) }, trialController.current.signal);
      if (current === trialRequest.current) setTrialResult({ result, source: values.source, id: values.reference_id.trim() });
    } catch { if (current === trialRequest.current) setTrialError('试运行失败，请检查图片 ID、权限和模型配置；不会写入标注'); }
    finally { if (current === trialRequest.current) setTrialBusy(false); }
  };
  const closeTrial = () => { clearTrialResult(); setTrial(undefined); };

  return <>
    {noticeContext}
    {params.get('project_id')&&<p><Link to={`/projects/${encodeURIComponent(params.get('project_id')!)}/prepare`}>← 返回当前项目的标注准备</Link></p>}
    <Card title="模型服务" extra={<Space wrap>
      <Link to="/model-preview">上传图片看检测效果 →</Link>
      <Button aria-label="刷新" onClick={load} loading={loading} disabled={working || loading}>刷新</Button>
      {admin && <><Button onClick={() => setDefaultsOpen(true)} disabled={working}>添加默认模型</Button><Button aria-label="添加模型" type="primary" icon={<PlusOutlined />} onClick={() => edit()} disabled={working}>添加模型</Button></>}
    </Space>}>
      <Typography.Paragraph type="secondary">统一配置模型接口，试运行只预览结果。普通用户可查看和预测；管理员可修改、启停和连接测试。</Typography.Paragraph>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
      <Table<ModelService> rowKey="id" loading={loading} dataSource={models} pagination={{ pageSize: 10, showSizeChanger: true }} scroll={{ x: 1085 }} columns={[
        { title: 'ID', dataIndex: 'id', width: 80, render: value => <Typography.Text title={value} copyable={{ text: value }} style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{value.slice(0, 5)}</Typography.Text> },
        { title: 'KEY', dataIndex: 'key', width: 130, ellipsis: true }, { title: '名称', dataIndex: 'name', width: 140 },
        { title: 'URL', dataIndex: 'base_url', width: 190, ellipsis: true }, { title: 'ENDPOINT', dataIndex: 'endpoint', width: 175, ellipsis: true },
        { title: '类型', dataIndex: 'kind', width: 110, render: (value: ModelKind, record) => <><Tag>{kinds[value]}</Tag><Typography.Text type="secondary" style={{ fontSize: 12 }}>{record.protocol}</Typography.Text></> },
        { title: '状态', dataIndex: 'enabled', width: 80, render: value => <Tag color={value ? 'success' : 'default'}>{value ? '已启用' : '已停用'}</Tag> },
        { title: '操作', key: 'actions', width: admin ? 180 : 90, fixed: 'right', render: (_, record) => <Space size="small" wrap>
          <Tooltip title={record.kind === 'reranker' ? '仅保存配置，部署后接入推理' : undefined}><span>
            <Button size="small" onClick={() => openTrial(record)} disabled={!record.enabled || working || record.kind === 'reranker'}>试运行</Button>
          </span></Tooltip>
          {admin && <><Button aria-label="编辑" size="small" onClick={() => edit(record)} disabled={working}>编辑</Button>
            <Button aria-label={record.enabled ? '停用' : '启用'} size="small" onClick={() => toggle(record)} disabled={working}>{record.enabled ? '停用' : '启用'}</Button>
            <Button size="small" onClick={() => test(record)} disabled={working}>连接测试</Button>
            <Popconfirm title="确认删除此模型配置？" description="删除后将无法继续使用该配置预测。" okText="确认删除" cancelText="取消" onConfirm={() => remove(record.id)}>
              <Button aria-label="删除" size="small" danger disabled={working}>删除</Button>
            </Popconfirm></>}
        </Space> },
      ]} />
    </Card>
    <Modal title={editing ? '编辑模型' : '添加模型'} open={editorOpen} forceRender width={680} okText="保存模型" cancelText="取消" confirmLoading={working}
      onOk={save} onCancel={() => { if (!working) setEditorOpen(false); }} okButtonProps={{ disabled: !admin || working, 'aria-label': '保存模型' }} styles={{ body: { maxHeight: '65vh', overflowY: 'auto' } }}>
      {editorError && <Alert type="error" showIcon title={editorError} style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical" disabled={working}>
        <Form.Item label="KEY" name="key" rules={[{ required: true, pattern: /^[a-zA-Z0-9_-]+$/, message: '使用字母、数字、下划线或连字符' }]}><Input aria-label="KEY" maxLength={80} /></Form.Item>
        <Form.Item label="名称" name="name" rules={[{ required: true, whitespace: true }]}><Input aria-label="名称" maxLength={200} /></Form.Item>
        <Form.Item label="URL" name="base_url" rules={[{ required: true }, { validator: (_, value) => {
          try { const url = new URL(value); if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash) return Promise.resolve(); } catch { /* invalid URL */ }
          return Promise.reject(new Error('请输入 HTTP(S) 服务地址，不含凭据、查询或片段'));
        } }]}><Input aria-label="URL" maxLength={500} placeholder="http://服务器:端口/v1" /></Form.Item>
        <Form.Item label="ENDPOINT" name="endpoint" rules={[{ required: true }, { validator: (_, value) => /^\/[A-Za-z0-9_/-]+$/.test(value) && !value.includes('//') && !value.includes('..') ? Promise.resolve() : Promise.reject(new Error('请输入以 / 开头的接口路径，不含域名、查询或跳级')) }]}><Input aria-label="ENDPOINT" maxLength={200} /></Form.Item>
        <Form.Item label="类型" name="kind" rules={[{ required: true }]}><Select aria-label="模型类型" options={Object.entries(kinds).map(([value, label]) => ({ value, label }))}
          onChange={(value: ModelKind) => {
            if (!protocols[protocol].includes(value)) form.setFieldValue('protocol', Object.keys(protocols).find(key => protocols[key as ModelProtocol].includes(value)));
            if (!editing && value === 'reranker') form.setFieldValue('enabled', false);
          }} /></Form.Item>
        <Form.Item label="协议" name="protocol" rules={[{ required: true }]}><Select aria-label="模型协议" options={Object.entries(protocols).filter(([, values]) => values.includes(kind)).map(([value]) => ({ value, label: value }))} /></Form.Item>
        <Form.Item label="模型 ID" name="model_name" rules={[{ required: protocol !== 'model_service', whitespace: true }]} extra="填写服务返回的准确模型 ID；Embedding 例如 /models/wemm_embedding_2b。"><Input aria-label="模型 ID" maxLength={200} /></Form.Item>
        {protocol === 'openai_chat' && <>
          <Typography.Title level={5}>Qwen / 视觉对话提示词</Typography.Title>
          <Typography.Paragraph type="secondary">此模型的完整核心指令。保存后对后续请求生效；已有批次使用配置快照，配置版本变化时需新建批次。恢复默认只修改当前输入，保存后生效。</Typography.Paragraph>
          {!promptDefaults && <Alert type="info" showIcon title="默认提示词暂不可用，可继续手动编辑。" action={<Button onClick={() => { getPromptDefaults().then(setPromptDefaults).catch(() => setPromptDefaults(undefined)); }}>重新读取默认提示词</Button>} />}
          <Form.Item label="描述核心提示词" name={['chat_prompts', 'caption']} rules={[{ required: true, whitespace: true, max: 8000, message: '请输入1–8000字符' }]} extra="用于单图预测和批量图片描述；单次/本批填写的提示词优先于此默认值。">
            <Input.TextArea aria-label="描述核心提示词" maxLength={8000} autoSize={{ minRows: 3, maxRows: 10 }} />
          </Form.Item>
          <Button disabled={!promptDefaults} onClick={() => form.setFieldValue(['chat_prompts', 'caption'], promptDefaults!.caption)} style={{ marginBottom: 16 }}>恢复默认描述提示词</Button>
          <Form.Item label="审核核心提示词" name={['chat_prompts', 'review']} rules={[{ required: true, whitespace: true, max: 8000, message: '请输入1–8000字符' }]} extra="用于预标注后的 AI 审核及已有标注审核。系统追加项目类别、框编号、本轮编号和 JSON 输出约束；任务中的补充审核要求也会追加。解析失败转人工，负样本和置信度规则仍由平台校验。">
            <Input.TextArea aria-label="审核核心提示词" maxLength={8000} autoSize={{ minRows: 6, maxRows: 14 }} />
          </Form.Item>
          <Button disabled={!promptDefaults} onClick={() => form.setFieldValue(['chat_prompts', 'review'], promptDefaults!.review)} style={{ marginBottom: 16 }}>恢复默认审核提示词</Button>
          <Form.Item label="检索匹配审核提示词" name={['chat_prompts','match_review']} rules={[{required:true,whitespace:true,max:8000}]} extra="独立判断查询与候选图是否匹配；不向模型提供人工判定或检索分数。一致自动通过，分歧、无法判断或请求失败转人工复核。">
            <Input.TextArea aria-label="检索匹配审核提示词" maxLength={8000} autoSize={{minRows:4,maxRows:12}}/>
          </Form.Item>
          <Button disabled={!promptDefaults} onClick={()=>form.setFieldValue(['chat_prompts','match_review'],promptDefaults!.match_review)} style={{marginBottom:16}}>恢复默认匹配审核提示词</Button>
          <Form.Item label="训练效果分析提示词" name={['chat_prompts','training_analysis']} rules={[{required:true,whitespace:true,max:8000}]} extra="用于训练指标分析与历史模型对比。平台会追加原始指标、数据一致性警告及证据约束；每次分析也可单独修改提示词。">
            <Input.TextArea aria-label="训练效果分析提示词" maxLength={8000} autoSize={{minRows:4,maxRows:10}}/>
          </Form.Item>
          <Button disabled={!promptDefaults} onClick={()=>form.setFieldValue(['chat_prompts','training_analysis'],promptDefaults!.training_analysis)} style={{marginBottom:16}}>恢复默认训练分析提示词</Button>
        </>}
        <Form.Item label="密钥环境变量名（可选）" name="api_key_env" extra="只引用服务器环境变量名，不填写或保存密钥。" rules={[{ validator: (_, value) => !value || /^(QWEN_API_KEY|EMBEDDING_API_KEY|RERANKER_API_KEY|MODEL_API_KEY_[A-Z0-9_]+)$/.test(value) ? Promise.resolve() : Promise.reject(new Error('只允许指定环境变量或 MODEL_API_KEY_ 开头的名称')) }]}>
          <Input aria-label="密钥环境变量名" maxLength={100} placeholder="QWEN_API_KEY / EMBEDDING_API_KEY / MODEL_API_KEY_…" />
        </Form.Item>
        <Space wrap align="start">
          <Form.Item label="超时（秒）" name="timeout_seconds" rules={[{ required: true }]}><InputNumber aria-label="超时秒数" min={1} max={180} precision={0} /></Form.Item>
          <Form.Item label="置信度" name="confidence" rules={[{ required: true }]}><InputNumber aria-label="置信度" min={0} max={1} step={.05} /></Form.Item>
          <Form.Item label="IoU" name="iou" rules={[{ required: true }]}><InputNumber aria-label="IoU" min={0} max={1} step={.05} /></Form.Item>
        </Space>
        <Form.Item label="类别 / SAM3 检测提示" name="labels" extra="SAM3 使用此列表作为检测提示，每项输入后回车。"><Select aria-label="模型类别列表" mode="tags" tokenSeparators={[',', '，']} maxCount={100} /></Form.Item>
        <Form.Item label="类别映射（JSON）" name="label_mapping_text" rules={[{ required: true }, { validator: (_, value) => {
          try { const parsed = JSON.parse(value); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && Object.keys(parsed).length <= 100 && Object.entries(parsed).every(([key, target]) => key.trim() && key.length <= 100 && typeof target === 'string' && target.trim() && target.length <= 100)) return Promise.resolve(); } catch { /* invalid JSON */ }
          return Promise.reject(new Error('请输入类别到类别的 JSON 对象，例如 {"smoke":"烟雾"}'));
        } }]}><JsonInput aria-label="类别映射" autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item>
        <Form.Item name="enabled" valuePropName="checked"><Checkbox>启用模型</Checkbox></Form.Item>
      </Form>
    </Modal>
    <Modal title="添加默认模型" open={defaultsOpen} onCancel={() => { if (!working) setDefaultsOpen(false); }} onOk={addDefaults} confirmLoading={working} okText="确认添加" cancelText="取消" okButtonProps={{ 'aria-label': '确认添加', disabled: working }}>
      <Alert type="info" showIcon title="幂等补充已知默认模型，不会覆盖已有配置。" />
    </Modal>
    <Modal title="连接测试" open={!!connection} onCancel={() => setConnection(undefined)} footer={<Button aria-label="关闭" onClick={() => setConnection(undefined)}>关闭</Button>}>
      {connection && <><Alert type={connection.ok ? 'success' : 'error'} showIcon title={connection.detail} />
        {!!connection.models?.length && <Typography.Paragraph style={{ marginTop: 16 }}>可用模型：{connection.models.join('、')}</Typography.Paragraph>}</>}
    </Modal>
    <Modal title={`模型试运行 · ${trial?.name || ''}`} open={!!trial} forceRender width={760} onCancel={closeTrial} styles={{ body: { maxHeight: '65vh', overflowY: 'auto' } }}
      footer={<Space><Button aria-label="关闭" onClick={closeTrial}>关闭</Button><Button aria-label="开始试运行" type="primary" loading={trialBusy} onClick={runTrial} disabled={!trial?.enabled || trialBusy}>开始试运行</Button></Space>}>
      <Typography.Paragraph type="secondary">使用图片库或项目图片试运行，仅预览，不写入标注。</Typography.Paragraph>
      <Form form={trialForm} layout="vertical" onValuesChange={clearTrialResult}>
        <Form.Item label="图片来源" name="source"><Select aria-label="图片来源" options={[{ value: 'image_id', label: '图片库图片 ID' }, { value: 'asset_id', label: '项目图片 asset ID' }]}
          onChange={() => trialForm.setFieldValue('reference_id', '')} /></Form.Item>
        {trial && source === 'image_id' && <Form.Item label="从图片库选择" extra="按入库时间筛选；切换筛选或翻页会清除已选图片。">
          <TrialImagePicker value={referenceId} onChange={value => { clearTrialResult(); trialForm.setFieldValue('reference_id', value || ''); }} />
        </Form.Item>}
        <Form.Item label="图片 ID" name="reference_id" rules={[{ required: true, pattern: uuid, message: '请输入有效的 UUID 图片 ID' }]}><Input aria-label="图片 ID" /></Form.Item>
        <Form.Item label="检测类别 / 描述文本（可选）" name="prompt" extra="VLX-Seek、GroundingPI 可填写检测类别，用分号分隔，留空使用模型配置类别；Qwen 留空使用默认描述提示词。"><Input.TextArea aria-label="试运行提示词" disabled={!supportsModelPrompt(trial)} maxLength={2000} autoSize={{ minRows: 1, maxRows: 4 }} /></Form.Item>
      </Form>
      {trialError && <Alert type="error" showIcon title={trialError} />}
      {trialResult && <PredictionResultView key={`${trialResult.source}-${trialResult.id}`} result={trialResult.result}
        {...(trialResult.source === 'asset_id' ? { assetId: trialResult.id } : { imageId: trialResult.id })} />}
    </Modal>
  </>;
}

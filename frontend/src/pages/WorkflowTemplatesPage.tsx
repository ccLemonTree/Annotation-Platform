import { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Space, Table, Typography } from 'antd';
import { isAxiosError } from 'axios';
import { getModelServices, getPromptDefaults, type ModelService } from '../api/modelServices';
import type { AttributeSchema } from '../api/objectAttributes';
import { deleteWorkflowTemplate, getWorkflowTemplates, saveWorkflowTemplate, type WorkflowTemplate } from '../api/projectWork';
import { AttributeFields, validAttributeFields } from '../components/ObjectAttributes';
import WorkflowStepsFields, { cropTargetLabels, draftSteps, operations, serializeSteps, type DraftStep } from '../components/WorkflowStepsFields';

const errorText=(error:unknown)=>isAxiosError(error)&&typeof error.response?.data?.detail==='string'
  ?error.response.data.detail:'操作失败，请重试';
const emptyAttributes:AttributeSchema={target_label:[],fields:[]};

export default function WorkflowTemplatesPage() {
  const [rows,setRows]=useState<WorkflowTemplate[]>([]);
  const [editing,setEditing]=useState<WorkflowTemplate|null>();
  const [loading,setLoading]=useState(true),[error,setError]=useState('');
  const [removing,setRemoving]=useState(false);
  useEffect(()=>{
    let active=true;
    getWorkflowTemplates().then(data=>{if(active)setRows(data);})
      .catch(cause=>{if(active)setError(errorText(cause));})
      .finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[]);
  const remove=async(id:string)=>{
    setError('');setRemoving(true);
    try{await deleteWorkflowTemplate(id);setRows(previous=>previous.filter(row=>row.id!==id));}
    catch(cause){setError(errorText(cause));}
    finally{setRemoving(false);}
  };
  if(editing!==undefined)return <TemplateEditor key={editing?.id||'new'} template={editing}
    onCancel={()=>setEditing(undefined)} onSaved={saved=>{
      setRows(previous=>[...previous.filter(row=>row.id!==saved.id),saved].sort((a,b)=>a.name.localeCompare(b.name,'zh-CN')));
      setEditing(undefined);
    }}/>;
  return <Card title="工作流模板" extra={<Button type="primary" disabled={loading} onClick={()=>{setError('');setEditing(null);}}>新建模板</Button>}>
    <Typography.Paragraph type="secondary">模板供本租户的项目复用。项目内载入模板后，再选择输入图片。</Typography.Paragraph>
    {error&&<Alert showIcon type="error" title={error} style={{marginBottom:16}}/>}
    <Table rowKey="id" dataSource={rows} loading={loading} pagination={{pageSize:20,showSizeChanger:false}}
      locale={{emptyText:'暂无工作流模板'}} scroll={{x:640}} columns={[
        {title:'模板名称',dataIndex:'name',width:250},
        {title:'处理步骤',render:(_,row)=>row.steps.map(step=>operations[step.operation]).join(' → ')},
        {title:'操作',width:170,render:(_,row)=><Space>
          <Button type="link" onClick={()=>setEditing(row)}>编辑</Button>
          <Popconfirm title="删除这个模板？" description="已经启动的工作流会继续执行。"
            okText="删除模板" cancelText="取消" onConfirm={()=>remove(row.id)}>
            <Button type="link" danger disabled={removing}>删除</Button>
          </Popconfirm>
        </Space>},
      ]}/>
  </Card>;
}

function TemplateEditor({template,onCancel,onSaved}:{template:WorkflowTemplate|null;onCancel:()=>void;onSaved:(row:WorkflowTemplate)=>void}) {
  const [form]=Form.useForm<{name:string;steps:DraftStep[]}>();
  const [models,setModels]=useState<ModelService[]>([]),[attributeDefault,setAttributeDefault]=useState<string>();
  const [qualityDefault,setQualityDefault]=useState<string>();
  const [attributes,setAttributes]=useState(template?.attribute_schema);
  const [attributeDraft,setAttributeDraft]=useState(emptyAttributes),[attributeOpen,setAttributeOpen]=useState(false);
  const [cropLabels,setCropLabels]=useState<string[]>([]);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{
    let active=true;
    Promise.all([getModelServices({enabled:true}),getPromptDefaults()]).then(([services,defaults])=>{
      if(active){setModels(services);setAttributeDefault(defaults.attributes);setQualityDefault(defaults.quality);}
    }).catch(cause=>{if(active)setError(errorText(cause));});
    return ()=>{active=false;};
  },[]);
  const submit=async(draft:{name:string;steps:DraftStep[]})=>{
    const needsAttributes=draft.steps.some(step=>step.operation==='attributes'&&step.attribute_templates==null);
    if(needsAttributes&&!attributes){setError('请先配置目标与属性。');return;}
    setBusy(true);setError('');
    try{
      const saved=await saveWorkflowTemplate({name:draft.name,steps:serializeSteps(draft.steps),attribute_schema:needsAttributes?attributes:undefined},template?.id);
      onSaved(saved);
    }catch(cause){setError(errorText(cause));}
    finally{setBusy(false);}
  };
  return <Card title={template?'编辑工作流模板':'新建工作流模板'}>
    {error&&<Alert showIcon type="error" title={error} style={{marginBottom:16}}/>}
    <Form form={form} layout="vertical" disabled={busy} onFinish={submit}
      initialValues={{name:template?.name,steps:template?draftSteps(template.steps):[{operation:'crop'}]}}>
      <Form.Item name="name" label="模板名称" rules={[{required:true,whitespace:true}]}><Input maxLength={100}/></Form.Item>
      <WorkflowStepsFields form={form} models={models} attributeDefault={attributeDefault} qualityDefault={qualityDefault} attributes={attributes} attributeLabel="模板" busy={busy}
        attributeAction={(index,category)=>!category&&<Button onClick={()=>{setCropLabels(cropTargetLabels(form.getFieldValue('steps')||[],index,models));setAttributeDraft(attributes??emptyAttributes);setAttributeOpen(true);}}>配置目标与属性</Button>}>
        <Button type="primary" htmlType="submit" loading={busy}>保存模板</Button>
        <Button onClick={onCancel}>返回模板列表</Button>
      </WorkflowStepsFields>
    </Form>
    <Modal open={attributeOpen} title="模板目标与属性" width={1040} onCancel={()=>setAttributeOpen(false)} okText="应用属性" cancelText="取消"
      okButtonProps={{disabled:!validAttributeFields(attributeDraft)}} onOk={()=>{setAttributes(attributeDraft);setAttributeOpen(false);}}>
      <Space orientation="vertical" style={{width:'100%'}}><AttributeFields config={attributeDraft} setConfig={setAttributeDraft} targetLabels={cropLabels}/></Space>
    </Modal>
  </Card>;
}

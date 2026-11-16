import {useEffect,useState,type Dispatch,type SetStateAction} from 'react';
import {Alert,Button,Input,Modal,Popconfirm,Select,Space,Table,Typography} from 'antd';
import {isAxiosError} from 'axios';
import {updateProject,type Project} from '../api/projects';
import {attributeTargets,resolvedAttributeTemplates,getAttributeTemplates,getCropLabels,saveAttributeTemplate,updateAttributeTemplate,deleteAttributeTemplate,type AttributeTemplate,type AttributeSchema} from '../api/objectAttributes';

export function ProjectAttributeConfigDialog({project,open,onClose,onSaved,cropLabels=[],onTemplateSaved}:{project:Project;open:boolean;onClose:()=>void;onSaved:(updated:Project)=>void;cropLabels?:string[];onTemplateSaved?:()=>void}){
  const saved=project.label_schema?.object_attributes as AttributeSchema|undefined;
  const [config,setConfig]=useState<AttributeSchema>(saved||{target_label:[],fields:[]});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [templates,setTemplates]=useState<AttributeTemplate[]>([]),[templateId,setTemplateId]=useState<string>();
  const [templateOpen,setTemplateOpen]=useState(false),[templateName,setTemplateName]=useState('');
  const [updatingTemplate,setUpdatingTemplate]=useState(false);
  const [loadedTemplateId,setLoadedTemplateId]=useState<string>();
  const [existingCropLabels,setExistingCropLabels]=useState<string[]>([]);
  const hasCropLabels=cropLabels.length>0;
  const dirty=JSON.stringify(saved||{target_label:[],fields:[]})!==JSON.stringify(config);
  const valid=validAttributeFields(config);
  const fail=(error:unknown)=>setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'操作失败，请刷新后重试');
  useEffect(()=>{if(!open)return;let active=true;getAttributeTemplates().then(rows=>{if(active)setTemplates(rows);}).catch(error=>{if(active)fail(error);});return()=>{active=false;};},[open]);
  useEffect(()=>{
    if(!open||hasCropLabels)return;
    let active=true;setExistingCropLabels([]);
    getCropLabels(project.id).then(labels=>{if(active)setExistingCropLabels(labels);}).catch(error=>{if(active)fail(error);});
    return()=>{active=false;};
  },[open,project.id,hasCropLabels]);
  const save=async()=>{setBusy(true);setError('');try{const updated=await updateProject(project.id,{label_schema:{...(project.label_schema||{}),object_attributes:config},base_label_schema:project.label_schema});onSaved(updated);onClose();}catch(error){fail(error);}finally{setBusy(false);}};
  const availableTemplates=resolvedAttributeTemplates(templates);
  const template=availableTemplates.find(item=>item.id===templateId);
  const saveTemplate=async()=>{setBusy(true);setError('');try{
    const row=updatingTemplate&&templateId?await updateAttributeTemplate(templateId,templateName.trim(),config):await saveAttributeTemplate(templateName.trim(),config);
    setTemplates(rows=>[...rows.filter(item=>item.id!==row.id),row]);setTemplateId(row.id);setLoadedTemplateId(row.id);setTemplateOpen(false);onTemplateSaved?.();
  }catch(error){fail(error);}finally{setBusy(false);}};
  const removeTemplate=async()=>{if(!templateId)return;setBusy(true);setError('');try{await deleteAttributeTemplate(templateId);setTemplates(rows=>rows.filter(row=>row.id!==templateId));setTemplateId(undefined);}catch(error){fail(error);}finally{setBusy(false);}};
  return <Modal title="目标与属性配置" open={open} centered width={1320} mask={{closable:false}} closable={!busy} keyboard={!busy}
      onCancel={()=>{if(!busy)onClose();}} afterOpenChange={open=>{if(open)document.getElementById('attribute-target-labels')?.focus();}}
      styles={{container:{display:'flex',flexDirection:'column',maxHeight:'calc(100dvh - 32px)'},header:{flexShrink:0},body:{minHeight:0,overflowY:'auto',overflowX:'hidden'},footer:{flexShrink:0}}}
      footer={<Space orientation="vertical" style={{width:'100%'}}>
        {error&&<Alert type="error" showIcon title={error}/>}
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:12}}>
          <Typography.Text type={dirty?'warning':'secondary'}>{dirty?'尚未保存。关闭后保留草稿，离开页面会丢失。':saved?'当前为已保存配置':'配置完成后保存到当前项目'}</Typography.Text>
          <Space wrap><Button disabled={busy} onClick={onClose}>关闭</Button><Button type="primary" loading={busy} disabled={!dirty||busy||!valid} onClick={save}>保存属性配置</Button></Space>
        </div>
      </Space>}>
      <Space wrap style={{marginBottom:16}}>
      <Button disabled={busy} onClick={()=>{setConfig({target_label:[],fields:[]});setTemplateId(undefined);setLoadedTemplateId(undefined);setTemplateOpen(false);}}>新建空白配置</Button>
      <Select aria-label="属性模板" placeholder="选择属性模板" value={templateId} disabled={busy} style={{width:180}} onChange={id=>{setTemplateId(id);setTemplateOpen(false);}} options={[{label:'内置模板',options:availableTemplates.filter(row=>row.id.startsWith('builtin_')).map(row=>({value:row.id,label:row.name}))},{label:'自定义模板',options:availableTemplates.filter(row=>!row.id.startsWith('builtin_')).map(row=>({value:row.id,label:row.name}))}]}/>
      <Button disabled={busy||!template} onClick={()=>{if(template){setConfig(structuredClone(template.config));setLoadedTemplateId(template.id);}}}>载入模板</Button>
      <Button disabled={busy||!valid} onClick={()=>{setTemplateName('');setUpdatingTemplate(false);setError('');setTemplateOpen(true);}}>保存为模板</Button>
      <Button disabled={busy||!valid||!template||loadedTemplateId!==templateId} onClick={()=>{setTemplateName(template!.name);setUpdatingTemplate(true);setError('');setTemplateOpen(true);}}>更新所选模板</Button>
      {templates.some(row=>row.id===templateId&&!row.id.startsWith('builtin_'))&&<Popconfirm title="删除此自定义模板？已应用到项目的配置和历史标注会保留。" onConfirm={removeTemplate}><Button danger disabled={busy}>删除模板</Button></Popconfirm>}
      </Space>
      {templateOpen&&<div role="group" aria-label="保存属性模板" style={{marginBottom:16}}>
        <Typography.Paragraph>{updatingTemplate?'将当前目标和属性更新到所选模板，供本租户项目后续载入。':'将当前配置保存为模板，供其他项目载入。'}</Typography.Paragraph>
        <Space wrap><Input aria-label="模板名称" value={templateName} maxLength={60} disabled={busy} placeholder="例如：人员服饰、货车属性" onChange={event=>setTemplateName(event.target.value)}/>
          <Button type="primary" loading={busy} disabled={busy||!templateName.trim()||!valid} onClick={saveTemplate}>保存模板</Button><Button disabled={busy} onClick={()=>setTemplateOpen(false)}>取消保存模板</Button></Space>
      </div>}
      <Space orientation="vertical" style={{width:'100%',minWidth:0}}>
        <Typography.Text type="secondary">{saved&&!dirty?'当前显示项目已保存的属性配置，未选择模板不会清空已有配置；创建其他对象配置可点击“新建空白配置”。':'可从空白配置添加所需属性，或选择模板后点击“载入模板”；保存属性配置后才会应用到当前项目。'}</Typography.Text>
        <AttributeFields config={config} setConfig={setConfig} busy={busy} targetLabels={hasCropLabels?cropLabels:existingCropLabels}/>
      </Space>
    </Modal>;
}

export const validAttributeFields=(config:AttributeSchema)=>attributeTargets(config).length>0&&attributeTargets(config).every(label=>label.trim()&&label.length<=100)&&config.fields.length>0&&config.fields.every(field=>field.name.trim());

export function AttributeFields({config,setConfig,busy=false,targetLabels=[]}:{
  config:AttributeSchema;setConfig:Dispatch<SetStateAction<AttributeSchema>>;busy?:boolean;targetLabels?:string[];
}) {
  const fieldChange=(index:number,changes:Partial<AttributeSchema['fields'][number]>)=>setConfig(previous=>({...previous,fields:previous.fields.map((field,i)=>i===index?{...field,...changes}:field)}));
  return <>
    <label htmlFor="attribute-target-labels">属性分析目标（可多选）</label>
    <Select id="attribute-target-labels" aria-label="属性分析目标标签" mode="tags" options={[...new Set([...targetLabels,...attributeTargets(config)])].filter(Boolean).map(label=>({value:label,label}))} value={attributeTargets(config)} tokenSeparators={[',','，']} disabled={busy} onChange={labels=>setConfig({...config,target_label:labels.length===1?labels[0]:labels})} placeholder="选择切图标签，或输入新标签后回车" style={{width:'100%'}}/>
    <Typography.Text type="secondary">这些目标共用下方属性。属性分析直接使用本步骤的输入图，上一步已切图时会直接分析裁剪结果。</Typography.Text>
    <Typography.Paragraph type="secondary">属性名称、判断说明和可选值均可编辑；没有可选值时使用自由文本。修改配置不会改写历史结果，历史标注按原配置编辑和导出。</Typography.Paragraph>
    <Table rowKey="key" pagination={false} dataSource={config.fields} locale={{emptyText:'尚无属性，请点击「添加属性」。'}} scroll={{x:760}} columns={[
      {title:'属性名称',width:160,render:(_,field,index)=><Input aria-label={`属性名称 ${index+1}`} value={field.name} maxLength={60} disabled={busy} onChange={event=>fieldChange(index,{name:event.target.value})}/>},
      {title:'判断说明',render:(_,field,index)=><Input.TextArea aria-label={`属性说明 ${index+1}`} value={field.instruction} maxLength={500} autoSize={{minRows:1,maxRows:4}} disabled={busy} onChange={event=>fieldChange(index,{instruction:event.target.value})}/>},
      {title:'可选值（留空为自由文本）',width:260,render:(_,field,index)=><Select aria-label={`属性可选值 ${index+1}`} mode="tags" style={{width:'100%'}} value={field.options} tokenSeparators={[',','，']} disabled={busy} onChange={options=>fieldChange(index,{options})}/>},
      {title:'操作',width:90,render:(_,field)=><Button danger disabled={busy} onClick={()=>setConfig({...config,fields:config.fields.filter(item=>item.key!==field.key)})}>移除</Button>},
    ]}/>
    <Button disabled={busy||config.fields.length>=30} onClick={()=>setConfig({...config,fields:[...config.fields,{key:'attr_'+crypto.getRandomValues(new Uint32Array(2)).join('_'),name:'',instruction:'',options:[]}]})}>添加属性</Button>
  </>;
}

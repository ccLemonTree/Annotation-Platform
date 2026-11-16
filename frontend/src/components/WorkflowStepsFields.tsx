import { useEffect, useState, type ReactNode } from 'react';
import { Button, Card, Checkbox, Form, Input, InputNumber, Select, Space, Typography, type FormInstance } from 'antd';
import { DEFAULT_DETECTION_CONFIDENCE, isModelOffline, type ModelService } from '../api/modelServices';
import { attributeTargets, builtinAttributeTemplates, getAttributeTemplates, resolvedAttributeTemplates, type AttributeSchema, type AttributeTemplate } from '../api/objectAttributes';
import type { WorkflowOperation, WorkflowStep } from '../api/projectWork';
import PromptEditor from './PromptEditor';

export const operations: Record<WorkflowOperation,string> = {
  crop:'检测切图', detect:'目标检测筛选', preannotate:'目标检测预标注', ai_review:'AI 审核', filter:'大模型筛选', quality:'大模型图片质量分析', caption:'大模型描述', attributes:'目标属性分析',
};
export type DraftStep=Omit<WorkflowStep,'labels'|'auto_approve_confidence'>&{labels?:string;auto_approve_confidence?:number|null};
export const serializeSteps=(steps:DraftStep[])=>steps.map(step=>({...step,confidence:step.confidence??undefined,expansion:step.expansion??undefined,
  min_crop_long_edge:step.min_crop_long_edge??undefined,auto_approve_confidence:step.auto_approve_confidence??undefined,
  save_input_boxes:step.operation==='crop'?step.save_input_boxes??false:undefined,
  labels:step.labels?.split(/[,，、;；\n]+/).map(label=>label.trim()).filter(Boolean)}));
export const draftSteps=(steps:WorkflowStep[])=>steps.map(step=>({...step,labels:step.labels?.join('、')}));
export const cropTargetLabels=(steps:DraftStep[],index:number,models:ModelService[],fallback:string[]=[])=>{
  const crop=steps.slice(0,index).findLast(step=>step.operation==='crop');
  const labels=crop?.labels?.split(/[,，、;；\n]+/).map(label=>label.trim()).filter(Boolean);
  return labels?.length?labels:(crop?models.find(model=>model.id===crop.model_id)?.labels:undefined)??fallback;
};

const builtinTargets:Record<string,string[]>={
  builtin_person:['person','pedestrian','human','人','人员','行人'],
  builtin_vehicle:['car','truck','bus','van','vehicle','车','车辆','汽车','卡车','公交车'],
  builtin_object:['object','物品','物体'],
};
const categoryTemplates=builtinAttributeTemplates.map(template=>({...template,
  config:{...template.config,target_label:builtinTargets[template.id]||template.config.target_label}}));

export default function WorkflowStepsFields({form,models,attributeDefault,qualityDefault,attributes,attributeAction,attributeLabel,attributeRevision=0,busy,children}:{
  form:FormInstance;models:ModelService[];attributeDefault?:string;qualityDefault?:string;attributes?:AttributeSchema;
  attributeAction?:(index:number,category:boolean)=>ReactNode;attributeLabel:string;attributeRevision?:number;busy:boolean;children:ReactNode;
}) {
  const steps:DraftStep[]|undefined=Form.useWatch('steps',{form,preserve:true});
  const [customTemplates,setCustomTemplates]=useState<AttributeTemplate[]>([]);
  const [templateLoadError,setTemplateLoadError]=useState(false);
  useEffect(()=>{
    let active=true;
    setTemplateLoadError(false);
    getAttributeTemplates().then(rows=>{if(active){
      setCustomTemplates(rows);
      if(attributeRevision){
        const current:DraftStep[]=form.getFieldValue('steps')||[];
        form.setFieldValue('steps',current.map(step=>step.attribute_templates?{...step,
          attribute_templates:step.attribute_templates.map(template=>rows.find(row=>row.id===template.id)??template)}:step));
      }
    }})
      .catch(()=>{if(active)setTemplateLoadError(true);});
    return()=>{active=false;};
  },[form,attributeRevision]);
  const defaults=resolvedAttributeTemplates(customTemplates).map(template=>
    template.id.startsWith('builtin_')&&!customTemplates.some(row=>row.id===template.id)
      ?categoryTemplates.find(row=>row.id===template.id)??template:template);
  return <Form.List name="steps">{(fields,{add,remove,move})=><Space orientation="vertical" style={{width:'100%'}}>
      {fields.map((field,index)=>{
        const operation=steps?.[index]?.operation||'crop';
        const detection=operation==='crop'||operation==='detect'||operation==='preannotate';
        const review=operation==='ai_review';
        const quality=operation==='quality';
        const requiresPrompt=operation==='filter'||quality;
        const options=models.filter(model=>model.enabled!==false&&model.kind===(detection?'detection':'caption')&&
          (requiresPrompt||review?model.protocol==='openai_chat':operation==='attributes'?
            ['openai_chat','jev_decide'].includes(model.protocol):model.protocol!=='jev_decide'));
        const model=options.find(row=>row.id===steps?.[index]?.model_id);
        const selectedTemplates=steps?.[index]?.attribute_templates;
        const categoryMode=selectedTemplates!=null;
        const availableTemplates=new Map([...defaults,...(selectedTemplates||[])].map(template=>[template.id,template]));
        const jev=model?.protocol==='jev_decide';
        const hasPrompt=requiresPrompt||operation==='caption'||model?.protocol==='openai_chat'||model?.endpoint==='/detect/grounding';
        const promptTitle=review?'补充审核要求':quality?'图片质量要求':operation==='filter'?'筛选提示词':operation==='caption'?'描述提示词':operation==='attributes'?'属性补充提示词':'检测提示词';
        return <Card key={field.key} title={`第 ${index+1} 步`} size="small" extra={<Space wrap>
          <Button size="small" disabled={!index} onClick={()=>move(index,index-1)}>上移</Button>
          <Button size="small" disabled={index===fields.length-1} onClick={()=>move(index,index+1)}>下移</Button>
          <Button size="small" disabled={fields.length<2} onClick={()=>remove(index)}>移除</Button>
        </Space>}>
          <Space wrap align="start">
            <Form.Item name={[field.name,'operation']} label="处理方式" rules={[{required:true}]}><Select style={{width:170}}
              options={Object.entries(operations).map(([value,label])=>({value,label}))}
              onChange={value=>form.setFieldValue(['steps',index],{operation:value})}/></Form.Item>
            <Form.Item name={[field.name,'model_id']} label="模型" rules={[{required:true}]}><Select showSearch optionFilterProp="label" style={{width:230}}
              onChange={id=>{
                const next=options.find(row=>row.id===id);
                if(next?.protocol!=='openai_chat'&&next?.endpoint!=='/detect/grounding')form.setFieldValue(['steps',index,'prompt'],'');
                if(next?.protocol==='jev_decide')form.setFieldValue(['steps',index,'attribute_prompt'],'');
              }}
              options={options.map(row=>({value:row.id,label:row.name+(isModelOffline(row)?' · 离线':''),disabled:isModelOffline(row)}))}/></Form.Item>
            {(detection||review)&&<Form.Item name={[field.name,'labels']} label={review?'审核类别':'检测类别'}><Input placeholder={review?'留空检查全部项目类别':model?.labels?.length?`默认 ${model.labels.join('、')}`:'留空使用模型默认类别'}/></Form.Item>}
            {detection&&<>
              <Form.Item name={[field.name,'confidence']} label="置信度"><InputNumber min={0} max={1} step={.05} placeholder={String(DEFAULT_DETECTION_CONFIDENCE)}/></Form.Item></>}
            {review&&<Form.Item name={[field.name,'auto_approve_confidence']} label="模型框自动通过置信度"><InputNumber min={0} max={1} step={.05} placeholder="0.9"/></Form.Item>}
            {operation==='crop'&&<><Form.Item name={[field.name,'expansion']} label="每侧外扩比例"><InputNumber min={0} max={1} step={.05} placeholder="0.2"/></Form.Item>
              <Form.Item name={[field.name,'min_crop_long_edge']} label="裁剪图最小长边"><InputNumber min={0} max={32768} precision={0} placeholder="0" suffix="px"/></Form.Item></>}
          </Space>
          {operation==='detect'&&<Typography.Paragraph type="secondary">只把检测命中的图片传给下一步。需要写入检测框时，选择「目标检测预标注」。</Typography.Paragraph>}
          {operation==='preannotate'&&<Typography.Paragraph type="secondary">保存检测框为待审核标注，自动补充新类别，保留已有检测标注。</Typography.Paragraph>}
          {review&&<Typography.Paragraph type="secondary">核查待审核正样本的框和整图漏标。审核通过才会更新状态，其余交给人工审核。</Typography.Paragraph>}
          {quality&&<Typography.Paragraph type="warning">合格图片进入下一步。明确判为不合格的图片及其标注会从本项目删除。模型调用失败时保留图片，可在步骤明细重试。</Typography.Paragraph>}
          {operation==='crop'&&<Form.Item name={[field.name,'save_input_boxes']} getValueProps={value=>({checked:value??false})}
            valuePropName="checked" extra="切图类别会自动加入项目。勾选后，检测框也会保存到本步输入图，状态为待审核。已有检测框或负样本会保留。">
            <Checkbox>将检测框映射到切图前的图片</Checkbox>
          </Form.Item>}
          {operation==='attributes'&&<Form.Item label="属性模板应用方式">
            <Select aria-label="属性模板应用方式" value={categoryMode?'category':'single'} style={{width:220}}
              options={[{value:'single',label:'统一使用一套属性'},{value:'category',label:'按裁剪类别应用模板'}]}
              onChange={value=>form.setFieldValue(['steps',index,'attribute_templates'],value==='category'?(jev?[]:defaults.filter(template=>template.id.startsWith('builtin_'))):undefined)}/>
          </Form.Item>}
          {operation==='attributes'&&categoryMode&&<Form.Item name={[field.name,'attribute_templates']} label="类别属性模板"
            rules={[{required:true,type:'array',min:1,message:'请选择至少一个属性模板'},{
              validator:(_,templates?:AttributeTemplate[])=>{
                const labels=(templates||[]).flatMap(template=>attributeTargets(template.config).map(label=>label.toLocaleLowerCase()));
                return new Set(labels).size===labels.length?Promise.resolve():Promise.reject(new Error('同一类别只能配置一个属性模板'));
              },
            }]}
            getValueProps={(value?:AttributeTemplate[])=>({value:value?.map(template=>template.id)||[]})}
            getValueFromEvent={(ids:string[])=>ids.flatMap(id=>{const template=availableTemplates.get(id);return template?[template]:[];})}
            extra={<>
              <Typography.Text type="secondary">按裁剪图的检测标签匹配模板，兼容 sam3_ 前缀。下一步接收匹配结果，未匹配的裁剪图可在切图结果中查看。</Typography.Text>
              {templateLoadError&&<Typography.Paragraph type="warning">自定义属性模板读取失败，可使用内置模板或重新切换应用方式。</Typography.Paragraph>}
              {jev&&<Typography.Paragraph type="secondary">JEV Choice 需要模板里的每项属性都有可选值。</Typography.Paragraph>}
              {attributeAction?.(index,true)}
            </>}>
            <Select aria-label="类别属性模板" mode="multiple" showSearch optionFilterProp="label" placeholder="选择属性模板"
              labelRender={option=>availableTemplates.get(String(option.value))?.name||option.label}
              options={Array.from(availableTemplates.values(),template=>({value:template.id,
                label:`${template.name} · ${attributeTargets(template.config).join('、')}`,
                disabled:jev&&template.config.fields.some(field=>!field.options.length)}))}/>
          </Form.Item>}
          {operation==='attributes'&&!categoryMode&&<Form.Item label="目标与属性">
            <Space orientation="vertical" size={6} style={{width:'100%',overflowWrap:'anywhere'}}>
              <Space wrap>
                {attributeAction?.(index,false)}
                <Typography.Text>{attributes?`${attributeTargets(attributes).join('、')} · ${attributes.fields.length} 项${attributeLabel}属性`:'尚未配置属性'}</Typography.Text>
              </Space>
              {attributes&&<Typography.Text type="secondary">{attributes.fields.map(field=>field.name).join('、')}</Typography.Text>}
              {model?.protocol==='jev_decide'&&<Typography.Text type="secondary">JEV Choice 使用属性判断说明和可选值。</Typography.Text>}
            </Space>
          </Form.Item>}
          {operation==='attributes'&&model?.protocol==='openai_chat'&&<Form.Item name={[field.name,'attribute_prompt']} label="属性核心提示词">
            <PromptEditor title="属性核心提示词" buttonLabel="编辑属性核心提示词" disabled={busy||attributeDefault===undefined}
              defaultValue={attributeDefault} prefillDefault
              help="可编辑本步骤使用的核心指令。系统会在推理时追加本图采用的属性定义与输出格式。"/>
          </Form.Item>}
          {hasPrompt&&<Form.Item name={[field.name,'prompt']} label={promptTitle} rules={requiresPrompt?[{required:true,whitespace:true,message:`请配置${promptTitle}`}]:[]}>
            <PromptEditor title={promptTitle} buttonLabel={`编辑${promptTitle}`} required={requiresPrompt} maxLength={requiresPrompt?2000:8000}
              disabled={busy||operation==='caption'&&!!model&&model.protocol!=='openai_chat'}
              defaultValue={operation==='caption'?model?.chat_prompts?.caption:undefined}
              referencePrompt={quality?qualityDefault:review?model?.chat_prompts?.review:operation==='attributes'?steps?.[index]?.attribute_prompt||attributeDefault:detection?model?.labels?.join('；'):undefined}
              referenceLabel={quality?'系统质量判断提示词':review?'模型默认审核提示词':operation==='attributes'?'本步骤属性核心提示词':detection?'模型默认检测类别':undefined}
              help={quality?'填写合格条件，例如主体完整、清晰，无严重遮挡或裁断。模型会输出是否合格和可见依据。':
                operation==='filter'?'填写本次命中条件，例如只保留能清楚看见人物正在吸烟的图片。系统会追加结构化判断约束。':
                review||operation==='attributes'?'输入的补充要求会追加到核心提示词之后。':
                detection?'留空时按本步骤的检测类别执行，未指定类别时使用模型默认类别。':'留空使用所选模型的默认描述提示词。'}/>
          </Form.Item>}
          {operation==='caption'&&model?.protocol==='model_service'&&<Typography.Text type="secondary">此模型只支持默认描述。需要自定义提示词时，请选择视觉对话模型。</Typography.Text>}
        </Card>;
      })}
      <Space wrap><Button onClick={()=>add({operation:'filter'})} disabled={fields.length>=20}>添加步骤</Button>{children}</Space>
    </Space>}</Form.List>;
}

import { useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, InputNumber, Modal, Select } from 'antd';
import { createImageCollection, getImageCollections, getImageIngestSettings, saveImageIngestSettings, testImageIngestService } from '../api/images';
import type { ImageCollection, ImageIngestConfig } from '../api/images';
import { getModelServices, getPromptDefaults } from '../api/modelServices';
import type { ModelService } from '../api/modelServices';
import {SceneCategoryEditor,configSceneGroups,sceneGroupFields,validateSceneGroups} from './SceneCategories';
import PromptEditor from './PromptEditor';

export default function ImageIngestSettings({collectionId,collectionName,onSaved,createNamed=false,disabled=false}:{
  collectionId?:string;collectionName?:string;onSaved?:(config:ImageIngestConfig)=>void;createNamed?:boolean;disabled?:boolean
}) {
  const [open,setOpen]=useState(false),[loaded,setLoaded]=useState(false),[busy,setBusy]=useState(false);
  const [testing,setTesting]=useState(false);
  const [collections,setCollections]=useState<ImageCollection[]>([]),[loadingCollections,setLoadingCollections]=useState(false);
  const [target,setTarget]=useState<{id:string;name?:string}>(),[reading,setReading]=useState(false);
  const [error,setError]=useState(''),[connection,setConnection]=useState('');
  const [models,setModels]=useState<ModelService[]>([]),[modelsError,setModelsError]=useState(false);
  const [copiedModel,setCopiedModel]=useState<string>();
  const [defaultPrompt,setDefaultPrompt]=useState<string>();
  const [form]=Form.useForm<ImageIngestConfig>();
  const readSettings=async(group:{id:string;name?:string})=>{
    setTarget(group);setReading(true);setLoaded(false);setError('');setConnection('');setCopiedModel(undefined);
    try{const config=await getImageIngestSettings(group.id);form.setFieldsValue({...config,scene_groups:configSceneGroups(config)});setLoaded(true);}
    catch{setError('入库设置读取失败，请重试');}
    finally{setReading(false);}
  };
  const load=async()=>{
    setOpen(true);setLoaded(false);setTarget(undefined);setError('');setConnection('');
    setModels([]);setModelsError(false);setCopiedModel(undefined);
    void getPromptDefaults().then(defaults=>setDefaultPrompt(defaults.scene)).catch(()=>setDefaultPrompt(undefined));
    void getModelServices({enabled:true,kind:'caption'}).then(items=>setModels(items.filter(item=>item.enabled&&item.protocol==='openai_chat'&&item.kind==='caption')))
      .catch(()=>setModelsError(true));
    if(collectionId){await readSettings({id:collectionId,name:collectionName});return;}
    setCollections([]);setLoadingCollections(true);
    try{
      if(createNamed&&collectionName)await readSettings(await createImageCollection(collectionName));
      else setCollections(await getImageCollections());
    }
    catch{setError(createNamed?'创建目标图片集失败，请重试':'图片集列表读取失败，请重试');}
    finally{setLoadingCollections(false);}
  };
  const save=async()=>{
    if(!target)return;
    try{
      const values=await form.validateFields();setBusy(true);setError('');
      const result=await saveImageIngestSettings({...values,...sceneGroupFields(values.scene_groups!)},target.id);onSaved?.(result);setOpen(false);
    }catch{setError('设置保存失败，请检查输入；仅管理员可修改图片集配置');}
    finally{setBusy(false);}
  };
  const test=async()=>{
    try{
      const values=await form.validateFields();setTesting(true);setError('');setConnection('');
      const result=await testImageIngestService({...values,...sceneGroupFields(values.scene_groups!)});
      setConnection(result.model_available?'模型服务已连接，配置的模型可用':`服务已连接，但模型未找到；可用模型：${result.models.join('、')}`);
    }catch{setError('模型服务连接失败，请检查地址、模型名称或服务鉴权');}
    finally{setTesting(false);}
  };
  return <>
    <Button onClick={load} disabled={disabled}>{createNamed&&!collectionId&&!target?'创建图片集并设置':'图片集设置'}</Button>
    <Modal title="图片集入库与场景设置" open={open} forceRender width={640} okText="保存设置" cancelText="取消" styles={{body:{maxHeight:'65dvh',overflowY:'auto'}}}
      onOk={save} onCancel={()=>{if(!busy&&!testing&&!reading&&!loadingCollections)setOpen(false);}} confirmLoading={busy} okButtonProps={{disabled:!target||!loaded||testing,'aria-label':'保存设置'}}>
      {!collectionId&&!createNamed&&<div style={{marginBottom:16}}>
        <p>选择要设置的图片集，分别保存入库与场景规则。</p>
        <Select aria-label="设置目标图片集" placeholder="请选择图片集" showSearch optionFilterProp="label" style={{width:'100%'}}
          value={target?.id} loading={loadingCollections||reading} disabled={loadingCollections||reading||busy||testing}
          options={collections.map(group=>({value:group.id,label:group.name}))} onChange={id=>{
            const group=collections.find(item=>item.id===id);if(group)void readSettings(group);
          }}/>
      </div>}
      {error&&<Alert type="error" showIcon title={error} action={!loaded?<Button onClick={()=>target?readSettings(target):load()}>重新读取</Button>:undefined} style={{marginBottom:16}}/>}
      <Form form={form} layout="vertical" disabled={busy||testing} style={{display:loaded?'block':'none'}}>
        <p className="workspace-help">当前图片集：{target?.name}。本配置只影响此图片集的后续入库，其他图片集不变。任意一条边大于等于阈值即可；0表示不过滤。原图与场景结果共享，重复图片复用已有场景；手动重分类会更新所有所属图片集中的场景。</p>
        <Form.Item name="min_long_edge" label="此图片集分辨率阈值（像素）" rules={[{required:true}]}>
          <InputNumber aria-label="默认长边阈值" min={0} max={32768} precision={0} style={{width:'100%'}}/>
        </Form.Item>
        <Form.Item name="classification_enabled" valuePropName="checked"><Checkbox>启用自动场景分类</Checkbox></Form.Item>
        <Form.Item label="从模型管理复制（可选）" extra="只复制地址和模型 ID，不动态覆盖各图片集规则；复制后仍可手动修改。仅支持空鉴权或 QWEN_API_KEY，其他环境变量暂不支持复制鉴权。">
          <Select aria-label="复制模型管理配置" placeholder="选择已启用的 OpenAI Chat 模型" allowClear value={copiedModel}
            options={models.map(model=>({value:model.id,label:model.api_key_env&&model.api_key_env!=='QWEN_API_KEY'?`${model.name}（暂不支持复制鉴权）`:model.name,disabled:!!model.api_key_env&&model.api_key_env!=='QWEN_API_KEY'}))} onChange={id=>{
              setCopiedModel(id);const model=models.find(item=>item.id===id&&(!item.api_key_env||item.api_key_env==='QWEN_API_KEY'));
              if(model){form.setFieldsValue({base_url:model.base_url,model:model.model_name});setConnection('');}
            }}/>
        </Form.Item>
        {modelsError&&<Alert type="info" showIcon title="模型管理配置暂不可用，可继续手动配置。" style={{marginBottom:16}}/>}
        <Form.Item name="base_url" label="vLLM服务地址" rules={[{required:true,message:'请输入服务地址'}]}>
          <Input aria-label="模型服务地址" placeholder="http://服务器:端口/v1"/>
        </Form.Item>
        <Form.Item name="model" label="模型名称" rules={[{required:true,message:'请输入模型名称'}]}>
          <Input aria-label="场景模型名称" placeholder="使用/v1/models返回的准确名称" maxLength={200}/>
        </Form.Item>
        <Button onClick={test} loading={testing} style={{marginBottom:16}}>连接测试</Button>
        {connection&&<Alert type="info" showIcon title={connection} style={{marginBottom:16}}/>}
        <Form.Item name="scene_groups" label="场景大类与具体场景" rules={[{validator:(_,value)=>Promise.resolve().then(()=>validateSceneGroups(value))}]} extra="先配置大类，再添加具体场景；允许同时匹配多个具体场景，最多100个。">
          <SceneCategoryEditor disabled={busy||testing}/>
        </Form.Item>
        <Form.Item name="scene_prompt" label="场景分类提示词" rules={[{required:true,whitespace:true,max:8000,message:'请输入1–8000字符'}]}>
          <PromptEditor key={target?.id??'none'} title="场景分类提示词" buttonLabel="编辑提示词" maxLength={8000} required defaultValue={defaultPrompt||undefined} disabled={busy||testing} help={`完整场景分类指令，仅作用于此图片集的后续入库。系统追加候选场景和 JSON labels 输出约束，名单外标签不会被采纳。已保存场景不自动重算。恢复默认后仍需保存设置。${!defaultPrompt?'默认提示词暂未读取，可重新打开设置重试。':''}`}/>
        </Form.Item>
      </Form>
      {!loaded&&!error&&<p>{reading||loadingCollections?'正在读取设置…':'请选择要设置的图片集。'}</p>}
    </Modal>
  </>;
}

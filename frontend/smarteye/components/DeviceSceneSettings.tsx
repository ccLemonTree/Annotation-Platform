import {useEffect,useState} from 'react';
import {Alert,Checkbox,Form,Input,Modal} from 'antd';
import {SceneCategoryEditor,configSceneGroups,sceneGroupFields,validateSceneGroups} from '../../src/components/SceneCategories';
import {getImageIngestSettings,saveImageIngestSettings,type ImageIngestConfig} from '../../src/api/images';
import {getPromptDefaults} from '../../src/api/modelServices';
import PromptEditor from '../../src/components/PromptEditor';

export default function DeviceSceneSettings({onClose,onSaved}:{onClose:()=>void;onSaved:(config:ImageIngestConfig)=>void}){
  const [form]=Form.useForm();
  const [config,setConfig]=useState<ImageIngestConfig>(),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [defaultPrompt,setDefaultPrompt]=useState<string>();
  useEffect(()=>{let active=true;getPromptDefaults().then(value=>{if(active)setDefaultPrompt(value.scene);}).catch(()=>{if(active)setDefaultPrompt(undefined);});return()=>{active=false;};},[]);
  useEffect(()=>{let active=true;getImageIngestSettings().then(value=>{if(active){setConfig(value);form.setFieldsValue({...value,scene_groups:configSceneGroups(value)});}}).catch(()=>{if(active)setError('读取分类设置失败，请关闭后重试');});return()=>{active=false;};},[form]);
  const save=async()=>{
    if(!config||busy)return;
    let values;try{values=await form.validateFields();}catch{return;}
    setBusy(true);setError('');
    try{const result=await saveImageIngestSettings({...config,...values,...sceneGroupFields(values.scene_groups)});onSaved(result);}
    catch{setError('保存分类设置失败，请检查输入后重试');}
    finally{setBusy(false);}
  };
  return <Modal open width={680} styles={{body:{maxHeight:'70dvh',overflowY:'auto'}}} title="设备场景分类设置" okText="保存分类设置" cancelText="取消" confirmLoading={busy} okButtonProps={{disabled:!config}} closable={!busy} mask={{closable:!busy}} onCancel={()=>{if(!busy)onClose();}} onOk={save}>
    <p>有本地抓图时优先使用图片判断场景，无图时才根据设备名称判断；抓图读取失败会提示失败，人工修正不会被自动覆盖。</p>
    <Alert type="info" showIcon title="保存后，已有设备不会自动重分类；需要时点击“全部设备分类”。" description="此全局配置也作为新建图片集的默认规则；已有图片集保留各自设置。" style={{marginBottom:16}}/>
    {error&&<Alert type="error" showIcon title={error}/>}
    <Form form={form} layout="vertical" disabled={!config||busy}>
      <Form.Item name="classification_enabled" valuePropName="checked"><Checkbox>启用设备自动场景分类</Checkbox></Form.Item>
      <Form.Item name="scene_groups" label="场景大类与具体场景" rules={[{validator:(_,value)=>Promise.resolve().then(()=>validateSceneGroups(value))}]} extra="先建大类，再添加具体场景；一个设备可匹配多个具体场景。修改归属不重写已有结果。"><SceneCategoryEditor disabled={!config||busy}/></Form.Item>
      <Form.Item name="base_url" label="分类模型服务地址" rules={[{required:true,whitespace:true}]}><Input aria-label="设备分类模型地址"/></Form.Item>
      <Form.Item name="model" label="模型名称" rules={[{required:true,whitespace:true}]}><Input aria-label="设备分类模型名称"/></Form.Item>
      <Form.Item name="scene_prompt" label="设备场景分类提示词" rules={[{required:true,whitespace:true,max:8000,message:'请输入1–8000字符'}]}>
        <PromptEditor title="设备场景分类提示词" buttonLabel="编辑设备场景提示词" disabled={!config||busy} defaultValue={defaultPrompt} required help="用于后续设备分类：有图按图片判断，无图仅按设备名称判断。系统追加候选场景与 JSON 输出约束，已有结果不自动重算。应用后仍需保存分类设置。"/>
      </Form.Item>
    </Form>
  </Modal>;
}

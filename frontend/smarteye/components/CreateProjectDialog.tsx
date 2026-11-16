import {useRef,useState} from 'react';
import {isAxiosError} from 'axios';
import {Alert,Form,Input,Modal,Select} from 'antd';
import {createProject,type Project,type ProjectCreate,type TaskType} from '../../src/api/projects';

type Values={project_name:string;task_type:TaskType;labels:string[];classification_mode:'single'|'multi';description?:string};
export default function CreateProjectDialog({onClose,onCreated}:{onClose:()=>void;onCreated:(project:Project)=>void}){
  const [form]=Form.useForm<Values>();
  const taskType=Form.useWatch('task_type',form)||'detection';
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const saving=useRef(false);
  const create=async()=>{
    let values:Values;
    try{values=await form.validateFields();}catch{return;}
    if(saving.current)return;
    saving.current=true;setBusy(true);setError('');
    try{
      const schema:ProjectCreate['label_schema']={labels:(values.labels||[]).map(label=>label.trim())};
      if(['classification','caption','vector'].includes(values.task_type))schema.classification_mode=values.classification_mode||'single';
      const project=await createProject({project_name:values.project_name.trim(),task_type:values.task_type,description:values.description,label_schema:schema});
      onCreated(project);
    }catch(error){const detail=isAxiosError(error)?error.response?.data?.detail:undefined;setError(typeof detail==='string'?detail:'创建项目失败，请检查配置后重试');}
    finally{saving.current=false;setBusy(false);}
  };
  return <Modal open title="新建项目" okText="创建项目" cancelText="取消" confirmLoading={busy} onOk={create} onCancel={()=>{if(!saving.current)onClose();}} closable={!busy} mask={{closable:!busy}} width={600} styles={{body:{maxHeight:'65dvh',overflowY:'auto'}}}>
    <p>创建后直接选为当前拉取目标，图片共享原图、项目标注独立。</p>
    <Form form={form} layout="vertical" disabled={busy} initialValues={{task_type:'detection',labels:[],classification_mode:'single'}}>
      <Form.Item name="project_name" label="项目名称" rules={[{required:true,whitespace:true,message:'请输入项目名称'},{max:200,message:'项目名称不能超过200个字符'}]}><Input aria-label="项目名称" maxLength={200}/></Form.Item>
      <Form.Item name="task_type" label="任务类型" rules={[{required:true,message:'请选择任务类型'}]}><Select options={[{value:'detection',label:'目标检测'},{value:'classification',label:'图像分类'},{value:'segmentation',label:'图像分割'},{value:'caption',label:'大模型描述标注'},{value:'vector',label:'向量匹配标注'}]}/></Form.Item>
      {['classification','caption','vector'].includes(taskType)&&<Form.Item name="classification_mode" label="分类方式"><Select options={[{value:'single',label:'单选分类'},{value:'multi',label:'多标签分类'}]}/></Form.Item>}
      <Form.Item name="labels" label="标注类别" extra="输入后按回车；描述和向量项目可留空。" rules={[{type:'array',required:!['caption','vector'].includes(taskType),min:['caption','vector'].includes(taskType)?0:1,message:'请至少设置一个类别'},
        {validator:(_,labels:string[]=[])=>labels.every(label=>label.trim())&&new Set(labels.map(label=>label.trim())).size===labels.length?Promise.resolve():Promise.reject(new Error('类别不能空白或重复'))}]}>
        <Select aria-label="项目标注类别" mode="tags" tokenSeparators={[',','，']} placeholder="例如：人、车；输入后按回车"/>
      </Form.Item>
      <Form.Item name="description" label="项目描述" rules={[{max:500,message:'项目描述不能超过500个字符'}]}><Input.TextArea maxLength={500} autoSize={{minRows:2,maxRows:5}}/></Form.Item>
      {error&&<Alert type="error" showIcon title={error}/>}
    </Form>
  </Modal>;
}

import { useEffect,useRef,useState } from 'react';
import { Alert,Button,Checkbox,Form,Input,Select,Space } from 'antd';
import type { FormInstance } from 'antd';
import type { Project } from '../api/projects';
import { freezeTrainingData,getTrainingSnapshots,type TrainingSnapshot } from '../api/training';

const sources={descriptions:'图片描述配对',labels:'分类标签配对',matches:'检索匹配判定（正确 / 错误）'};
export default function TrainingPlatformData({form,projects,format}:{form:FormInstance;projects:Project[];format:string}){
  const projectId=Form.useWatch('project_id',form) as string|undefined;
  const [result,setResult]=useState<{projectId:string;rows:TrainingSnapshot[];error?:string}>(),[freezing,setFreezing]=useState(false),[error,setError]=useState('');
  const rows=result&&result.projectId===projectId?result.rows:[],loading=!!projectId&&result?.projectId!==projectId;
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const [source,setSource]=useState(format==='swift_sft'?'descriptions':'matches'),[pending,setPending]=useState(false),[instruction,setInstruction]=useState('请描述这张图片。');
  const project=projects.find(row=>row.id===projectId),selected=Form.useWatch('snapshot_id',form) as string|undefined;
  const sampleSource=source==='matches'&&(format==='swift_sft'||project?.task_type!=='vector')?'descriptions':source;
  const fail=(cause:unknown)=>{const error=cause as {response?:{data?:{detail?:unknown}}};setError(typeof error.response?.data?.detail==='string'?error.response.data.detail:'读取或冻结失败，请重试');};
  useEffect(()=>{if(!projectId)return;let active=true;
    getTrainingSnapshots(projectId).then(rows=>{if(active)setResult({projectId,rows});}).catch(()=>{if(active)setResult({projectId,rows:[],error:'读取冻结版本失败，可重新选择项目重试'});});
    return()=>{active=false;};
  },[projectId]);
  const freeze=async()=>{if(!projectId)return;setFreezing(true);setError('');const id=projectId;
    try{const row=await freezeTrainingData(id,{dataset_format:format,sample_source:sampleSource,approved_only:!pending,instruction});
      if(mounted.current&&form.getFieldValue('project_id')===id){setResult({projectId:id,rows:[row,...rows]});form.setFieldValue('snapshot_id',row.id);}
    }catch(cause){if(mounted.current)fail(cause);}finally{if(mounted.current)setFreezing(false);}};
  const snapshot=rows.find(row=>row.id===selected);
  return <Space orientation="vertical" style={{width:'100%',marginBottom:16}}>
    <Alert type="info" title="冻结平台标注用于训练" description={`数据格式：${format}。冻结后使用固定图片和标注，启动时自动传输；不自动划分训练/验证集。默认仅包含审核通过的匹配关系，AI分歧需先人工复核，已驳回项不参与。`}/>
    <Form.Item name="project_id" label="平台标注项目" rules={[{required:true}]}><Select aria-label="平台标注项目" disabled={freezing} showSearch optionFilterProp="label" options={projects.filter(p=>['classification','caption','vector'].includes(p.task_type)).map(p=>({value:p.id,label:p.project_name}))}
      onChange={()=>form.setFieldValue('snapshot_id',undefined)}/></Form.Item>
    <label htmlFor="platform-training-source">标注数据来源</label><Select id="platform-training-source" aria-label="标注数据来源" style={{width:'100%'}} value={sampleSource} disabled={freezing} onChange={setSource}
      options={Object.entries(sources).filter(([key])=>key!=='matches'||(format!=='swift_sft'&&project?.task_type==='vector')).map(([value,label])=>({value,label}))}/>
    <Checkbox checked={pending} disabled={freezing} onChange={e=>setPending(e.target.checked)}>包含待审核标注（含匹配判定，不含AI分歧待复核项）</Checkbox>
    {format==='swift_sft'&&<><label htmlFor="platform-sft-instruction">训练指令</label><Input.TextArea id="platform-sft-instruction" aria-label="平台SFT训练指令" value={instruction} disabled={freezing} maxLength={2000} onChange={e=>setInstruction(e.target.value)}/></>}
    <Button aria-label="冻结当前标注" disabled={!projectId||loading} loading={freezing} onClick={freeze}>冻结当前标注</Button>
    {(error||result&&result.projectId===projectId&&result.error)&&<Alert type="error" title={error||result?.error}/>}
    <Form.Item name="snapshot_id" label="平台数据冻结版本" rules={[{required:true}]} extra="以上来源和审核选项用于新冻结版本；训练使用下方选中的固定版本。修改标注后需重新冻结。"><Select aria-label="平台数据冻结版本" loading={loading} disabled={freezing} options={rows.filter(row=>row.format===format).map(row=>({value:row.id,label:`${row.format} · ${sources[row.manifest.training?.source as keyof typeof sources]||'标注'} · ${row.manifest.training?.samples||0} 条 · ${new Date(row.created_at).toLocaleString('zh-CN')} · ${row.id.slice(0,8)}`}))}/></Form.Item>
    {snapshot&&<p>已选择固定版本：{snapshot.manifest.training?.samples} 条训练样本。保存任务不会启动训练。</p>}
  </Space>;
}

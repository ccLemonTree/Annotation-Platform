import { useEffect,useState } from 'react';
import { Alert,Button,Form,Select,Space } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import type { FormInstance } from 'antd';
import type { Project } from '../api/projects';
import { getTrainingSnapshots,type TrainingSnapshot } from '../api/training';

const sources={descriptions:'图片描述配对',labels:'分类标签配对',attributes:'目标属性（结构化 JSON）',attribute_descriptions:'目标属性转自然语言',single_attribute:'单属性文图配对',matches:'检索匹配判定（正确 / 错误）'};
export default function TrainingPlatformData({form,projects,format,projectLocked=false,showSwiftBatchNote=true,onOpenFreeze}:{form:FormInstance;projects:Project[];format:string;projectLocked?:boolean;showSwiftBatchNote?:boolean;onOpenFreeze:()=>void}){
  const projectId=Form.useWatch('project_id',form) as string|undefined;
  const [params]=useSearchParams();
  const scope=new URLSearchParams();
  if(projectLocked)for(const key of ['work_job_id','image_kind','kind'])if(params.get(key))scope.set(key,params.get(key)!);
  const [reload,setReload]=useState(0);
  const [result,setResult]=useState<{projectId:string;rows:TrainingSnapshot[];error?:string}>();
  const rows=result&&result.projectId===projectId?result.rows:[],loading=!!projectId&&result?.projectId!==projectId;
  const selected=Form.useWatch('snapshot_id',form) as string|undefined;
  useEffect(()=>{if(!projectId)return;const controller=new AbortController();
    getTrainingSnapshots(projectId,true,controller.signal).then(rows=>{if(!controller.signal.aborted)setResult({projectId,rows});}).catch(()=>{if(!controller.signal.aborted)setResult({projectId,rows:[],error:'读取冻结版本失败，请点击“重新读取冻结版本”重试'});});
    return()=>controller.abort();
  },[projectId,reload]);
  const snapshot=rows.find(row=>row.id===selected);
  const source=snapshot?.manifest.training?.source;
  const textPairs=source==='descriptions'||source==='attributes'||source==='attribute_descriptions'||source==='single_attribute';
  const compatible=rows.filter(row=>row.format===format&&row.manifest.training?.format===format&&row.manifest.training.samples>0);
  return <Space orientation="vertical" style={{width:'100%',marginBottom:16}}>
    <Alert type="info" title={`选择已冻结的 ${format} 数据`} description={projectId&&<Link to={`/projects/${projectId}/deliver${scope.size?'?'+scope:''}#freeze-data`} onClick={onOpenFreeze}>前往项目导出与冻结</Link>}/>
    <Form.Item name="project_id" label="平台标注项目" rules={[{required:true}]}><Select aria-label="平台标注项目" disabled={projectLocked} showSearch optionFilterProp="label" options={projects.map(p=>({value:p.id,label:p.project_name}))}
      onChange={()=>form.setFieldValue('snapshot_id',undefined)}/></Form.Item>
    {result?.projectId===projectId&&result?.error&&<Alert type="error" title={result.error}/>}
    <Button disabled={!projectId} loading={loading} onClick={()=>{setResult(undefined);setReload(value=>value+1);}}>重新读取冻结版本</Button>
    <Form.Item name="snapshot_id" label="平台数据冻结版本" rules={[{required:true}]}><Select aria-label="平台数据冻结版本" loading={loading} options={compatible.map(row=>({value:row.id,label:`${row.version_label ? row.version_label+' · ' : ''}${row.format} · ${sources[row.manifest.training?.source as keyof typeof sources]||'标注'} · ${row.manifest.training?.samples||0} 条 · ${new Date(row.created_at).toLocaleString('zh-CN')} · ${row.id.slice(0,8)}`}))}/></Form.Item>
    {snapshot&&<p>已选择固定版本：{snapshot.manifest.training?.samples} 条训练样本。保存任务不会启动训练。</p>}
    {showSwiftBatchNote&&textPairs&&format==='infonce'&&<Alert type="info" title={snapshot?.manifest.training?.negative_strategy==='explicit'?
      '训练使用冻结版本中记录的负图。':'文图配对使用批内负样本，训练时自动配置。'}/>}
  </Space>;
}

import { useState } from 'react';
import { Alert,Select,Space,Typography } from 'antd';
import { isAxiosError } from 'axios';
import { updateProject,type Project } from '../api/projects';

export default function ProjectAnnotationScope({project,onSaved}:{project:Project;onSaved:(project:Project)=>void}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const save=async(value:string)=>{setBusy(true);setError('');try{
    onSaved(await updateProject(project.id,{base_label_schema:project.label_schema,label_schema:{...project.label_schema,annotation_scope:value}}));
  }catch(error){setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'标注范围保存失败，请刷新后重试');}finally{setBusy(false);}};
  return <Space orientation="vertical" style={{width:'100%',marginBottom:16}}>
    <Space wrap><Typography.Text>项目标注范围</Typography.Text><Select aria-label="项目标注范围" style={{width:200}} value={project.label_schema?.annotation_scope==='crops'?'crops':'all'} disabled={busy} loading={busy} onChange={save}
      options={[{value:'all',label:'原图与裁剪图'},{value:'crops',label:'仅标注裁剪图'}]}/>
      <Typography.Text type="secondary">仅标注裁剪图时，原图保留作来源；没有检测目标的原图无需标注。裁剪图仅属于当前项目。</Typography.Text></Space>
    {error&&<Alert type="error" title={error}/>}
  </Space>;
}

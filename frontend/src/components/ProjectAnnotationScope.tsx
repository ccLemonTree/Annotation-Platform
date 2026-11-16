import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert,Select,Space,Typography } from 'antd';
import { isAxiosError } from 'axios';
import { updateProject,type Project } from '../api/projects';

export default function ProjectAnnotationScope({project,onSaved,disabled=false}:{project:Project;onSaved?:(project:Project)=>void;disabled?:boolean}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const crops=project.label_schema?.annotation_scope==='crops';
  if(!onSaved)return <Space wrap style={{marginBottom:16}}>
    <Typography.Text>项目标注范围：{crops?'仅标注裁剪图':'原图与裁剪图'}</Typography.Text>
    <Link to={`/annotations?project_id=${project.id}&prepare=1`}>去标注准备修改</Link>
  </Space>;
  const save=async(value:string)=>{setBusy(true);setError('');try{
    onSaved(await updateProject(project.id,{base_label_schema:project.label_schema,label_schema:{...project.label_schema,annotation_scope:value}}));
  }catch(error){setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'标注范围保存失败，请刷新后重试');}finally{setBusy(false);}};
  return <Space orientation="vertical" style={{width:'100%',marginBottom:16}}>
    <Space wrap><Typography.Text>项目标注范围</Typography.Text><Select aria-label="项目标注范围" style={{width:200}} value={crops?'crops':'all'} disabled={busy||disabled} loading={busy} onChange={save}
      options={[{value:'all',label:'原图与裁剪图'},{value:'crops',label:'仅标注裁剪图'}]}/>
      <Typography.Text type="secondary">此设置决定哪些图片进入人工标注、审核和导出。</Typography.Text></Space>
    {error&&<Alert type="error" title={error}/>}
  </Space>;
}

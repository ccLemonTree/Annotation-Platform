import { useEffect,useState } from 'react';
import { Link,useParams,useSearchParams } from 'react-router-dom';
import { Alert,Button,Card,Pagination,Radio,Space,Spin,Table,Tag,Typography } from 'antd';
import { getProject,type Project } from '../api/projects';
import { getAssets,getAllAssetIds,type Asset } from '../api/assets';
import VectorAnnotation from '../components/VectorAnnotation';
import ProjectWorkflow from '../components/ProjectWorkflow';
import ProjectAnnotationScope from '../components/ProjectAnnotationScope';
import AssetImage from '../components/AssetImage';
import DeleteCropButton from '../components/DeleteCropButton';
import DeleteSelectedImages from '../components/DeleteSelectedImages';
import { useCurrentUser } from '../components/currentUser';

function DescriptionImages({project}:{project:Project}){
  const admin=useCurrentUser()?.role==='admin';
  const [page,setPage]=useState(1),[reload,setReload]=useState(0);
  const [selected,setSelected]=useState<React.Key[]>([]);
  const [result,setResult]=useState<{key:string;items:Asset[];total:number;failed?:boolean}>();
  const key=JSON.stringify([project.id,project.label_schema?.annotation_scope,page,reload]);
  const loading=result?.key!==key,rows=loading?[]:result.items,total=result?.total||0,error=!loading&&result.failed;
  useEffect(()=>{let active=true;
    getAssets({project_id:project.id,scope:'annotation',page,page_size:20}).then(data=>{if(active)setResult({key,items:data.items,total:data.total});}).catch(()=>{if(active)setResult({key,items:[],total:0,failed:true});});
    return()=>{active=false;};
  },[project.id,page,key]);
  return <Card title="图片描述标注">
    <Typography.Paragraph type="secondary">直接打开图片编辑描述。{admin&&<>也可在<Link to={`/projects/${project.id}/prepare`}>标注准备</Link>中调用大模型批量生成描述。</>}类别与描述按图片保存。</Typography.Paragraph>
    {error&&<Alert type="error" title="图片读取失败" action={<Button onClick={()=>setReload(v=>v+1)}>重试</Button>}/>}
    <Space wrap style={{marginBottom:12}}><DeleteSelectedImages ids={selected.map(String)} disabled={loading} onDeleted={()=>{setSelected([]);setPage(1);setReload(v=>v+1);}}/>
      <DeleteSelectedImages disabled={loading||!total} allLabel={project.label_schema?.annotation_scope==='crops'?'删除全部裁剪图':'删除全部图片'} rangeLabel={`${project.project_name} · ${project.label_schema?.annotation_scope==='crops'?'全部裁剪图':'全部标注图片'}`} loadAll={()=>getAllAssetIds({project_id:project.id,scope:'annotation'})} onDeleted={()=>{setSelected([]);setPage(1);setReload(v=>v+1);}}/>
    </Space>
    <Table<Asset> rowKey="id" loading={loading} dataSource={rows} rowSelection={{selectedRowKeys:selected,preserveSelectedRowKeys:true,onChange:setSelected,getCheckboxProps:()=>({disabled:loading})}} scroll={{x:500}} pagination={false} locale={{emptyText:admin?<span>当前标注范围内暂无图片，可前往<Link to={`/projects/${project.id}/prepare`}>标注准备检测切图</Link>或添加项目图片。</span>:'当前标注范围内暂无图片，请联系管理员添加图片或完成检测切图。'}} columns={[
      {title:'图片',width:100,render:(_,row)=><AssetImage assetId={row.id} alt={row.original_name} width={72} height={54} style={{objectFit:'contain'}}/>},
      {title:'名称',dataIndex:'original_name',ellipsis:true},{title:'来源',width:100,render:(_,row)=><Tag>{row.source_type==='model_crop'?'裁剪图':'原图'}</Tag>},
      {title:'操作',width:250,render:(_,row)=><Space wrap><Link to={`/annotate/${row.id}?from=vector&mode=description`}>编辑图片描述</Link>{row.source_type==='model_crop'&&<DeleteCropButton assetId={row.id} name={row.original_name} onDeleted={()=>{setSelected(keys=>keys.filter(key=>key!==row.id));if(rows.length===1&&page>1)setPage(page-1);setReload(v=>v+1);}}/>}</Space>},
    ]}/>
    <Pagination current={page} pageSize={20} total={total} showSizeChanger={false} onChange={setPage} style={{marginTop:16}}/>
  </Card>;
}

export default function VectorAnnotationWorkspace(){
  const admin=useCurrentUser()?.role==='admin';
  const {projectId}=useParams();
  const [project,setProject]=useState<Project>(),[error,setError]=useState<{id:string;message:string}>();
  const [params,setParams]=useSearchParams();
  const mode=params.get('mode')==='text'?'text':params.get('mode')==='image'?'image':'description';
  useEffect(()=>{let active=true;
    getProject(projectId!).then(row=>{if(active)setProject(row);})
      .catch(()=>{if(active)setError({id:projectId!,message:'项目读取失败或无权访问，请返回项目管理重新选择'});});
    return()=>{active=false;};
  },[projectId]);
  if(error&&error.id===projectId)return <Alert type="error" title={error.message} action={<Link to="/annotations">返回标注项目</Link>}/>;
  if(!project||project.id!==projectId)return <Spin/>;
  if(project.task_type!=='vector')return <Alert type="warning" title="此项目不属于向量标注项目" action={<Link to={`/annotations?project_id=${project.id}`}>查看项目审核</Link>}/>;
  return <Space orientation="vertical" size="large" style={{width:'100%'}}>
    <ProjectWorkflow project={project} active="annotate"/>
    {admin&&<ProjectAnnotationScope project={project} onSaved={setProject}/>}
    <Radio.Group aria-label="标注方式" optionType="button" buttonStyle="solid" value={mode} onChange={e=>{const next=new URLSearchParams(params);next.set('mode',e.target.value);setParams(next);}} options={[{value:'description',label:'图片描述标注'},{value:'text',label:'文搜图标注'},{value:'image',label:'以图搜图标注'}]}/>
    {mode==='description'?<DescriptionImages key={project.id+String(project.label_schema?.annotation_scope)} project={project}/>:<VectorAnnotation key={project.id+String(project.label_schema?.annotation_scope)} project={project} mode={mode}/>}
  </Space>;
}

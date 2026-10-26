import { useEffect,useRef,useState } from 'react';
import JsonView from '../components/JsonView';
import { Link,useBlocker,useNavigate,useSearchParams } from 'react-router-dom';
import { Alert,Button,Card,Checkbox,Col,Input,Modal,Radio,Row,Space,Tag,Typography } from 'antd';
import { isAxiosError } from 'axios';
import type { Asset } from '../api/assets';
import { getAssetNeighbors } from '../api/assets';
import type { Project } from '../api/projects';
import { createAnnotation,getAnnotations,updateAnnotation,reviewAnnotation,type Annotation } from '../api/annotations';
import AssetImage from '../components/AssetImage';
import { rememberAnnotationProgress } from '../components/annotationProgress';
import useAnnotationShortcuts from '../components/useAnnotationShortcuts';
import { useCurrentUser } from '../components/currentUser';

type Data={description:string;labels:string[];skipped?:boolean};
const empty:Data={description:'',labels:[]};
export default function ImageAnnotationWorkspace({asset,project}:{asset:Asset;project:Project}){
  const admin=useCurrentUser()?.role==='admin';
  const [params]=useSearchParams();
  const navigate=useNavigate(),writing=useRef(false);
  const returnParams=new URLSearchParams(params);returnParams.delete('from');
  const fromReview=params.get('from')==='review';
  const fromVector=params.get('from')==='vector';
  const back=fromReview?`/annotations?${returnParams}`:project.task_type==='vector'?`/annotate/project/${project.id}${fromVector?`?${returnParams}`:''}`:admin?`/projects/${project.id}/images`:`/annotations?project_id=${project.id}`;
  const [versions,setVersions]=useState<Annotation[]>([]),[current,setCurrent]=useState<Annotation>(),[draft,setDraft]=useState<Data>(empty);
  const [original,setOriginal]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [neighbors,setNeighbors]=useState<{previous_id:string|null;next_id:string|null}>(),[comment,setComment]=useState('');
  const [showShortcuts,setShowShortcuts]=useState(false);
  const dirty=!!original&&JSON.stringify(draft)!==original;
  const blocker=useBlocker(dirty);
  useEffect(()=>{let active=true;rememberAnnotationProgress(asset);
    Promise.all([getAnnotations({asset_id:asset.id,latest_only:false,page_size:100}),getAssetNeighbors(asset.id)])
      .then(([response,next])=>{if(!active)return;const latest=response.items.find(a=>a.is_latest);const data={...empty,...latest?.annotation_data};
        setVersions(response.items);setCurrent(latest);setDraft(data);setOriginal(JSON.stringify(data));setNeighbors(next);})
      .catch(()=>{if(active)setError('读取标注失败，请重新打开');}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[asset]);
  useEffect(()=>{if(!dirty)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty]);
  const fail=(error:unknown)=>setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'操作失败，请重试');
  const save=async()=>{if(loading||writing.current||(!dirty&&current))return;writing.current=true;setBusy(true);setError('');try{
    const data=draft.skipped?{labels:[],description:'',skipped:true}:draft;
    const ann=current?await updateAnnotation(current.id,{base_version:current.version,annotation_data:data}):await createAnnotation({asset_id:asset.id,base_version:0,annotation_data:data});
    setCurrent(ann);setVersions(rows=>[ann,...rows.map(row=>({...row,is_latest:false}))]);setOriginal(JSON.stringify(data));setDraft(data);
  }catch(error){fail(error);}finally{writing.current=false;setBusy(false);}};
  const review=async(approved:boolean)=>{if(!current||loading||writing.current||dirty||(approved&&draft.skipped)||current.review_status===(approved?'approved':'rejected'))return;writing.current=true;setBusy(true);setError('');try{
    const updated=await reviewAnnotation(current.id,{approved,review_comment:comment});setCurrent(updated);setVersions(rows=>rows.map(a=>a.id===updated.id?updated:a));
  }catch(error){fail(error);}finally{writing.current=false;setBusy(false);}};
  useAnnotationShortcuts(event=>{
    const key=event.key.toLowerCase();
    if((event.ctrlKey||event.metaKey)&&!event.shiftKey&&key==='s'){event.preventDefault();void save();return;}
    if(event.ctrlKey||event.metaKey||writing.current)return;
    if(key==='r'){event.preventDefault();void review(!event.shiftKey);}
    else if(!event.shiftKey&&(key==='arrowleft'||key==='a'||key==='arrowright'||key==='d')){
      event.preventDefault();const id=key==='arrowleft'||key==='a'?neighbors?.previous_id:neighbors?.next_id;
      if(id)void navigate(`/annotate/${id}?${params}`);
    }
  },!loading&&!showShortcuts,true);
  const labels=Array.isArray(project.label_schema?.labels)?project.label_schema.labels as string[]:[];
  const multiple=project.label_schema?.classification_mode==='multi';
  return <Card loading={loading} title={<Space wrap><span>{project.project_name}</span><Tag>{({classification:'图片分类',caption:'描述标注',vector:'图片描述标注'} as Record<string,string>)[project.task_type]}</Tag></Space>}>
    <Space wrap style={{marginBottom:16}}><Link to={back}>{fromReview?'返回审核列表':project.task_type==='vector'?'返回向量标注':admin?'返回项目图片':'返回标注与审核'}</Link>
      {neighbors?.previous_id&&<Link to={`/annotate/${neighbors.previous_id}?${params}`}>上一张</Link>}{neighbors?.next_id&&<Link to={`/annotate/${neighbors.next_id}?${params}`}>下一张</Link>}
      <Button onClick={()=>setShowShortcuts(true)}>快捷键</Button><Typography.Text type="secondary">← / → 切图 · Ctrl+S 保存 · R 通过 · Shift+R 驳回</Typography.Text></Space>
    {error&&<Alert type="error" showIcon title={error} style={{marginBottom:16}}/>}
    <Row gutter={[24,24]}><Col xs={24} lg={15}><AssetImage assetId={asset.id} width="100%" style={{maxHeight:'65vh',objectFit:'contain',background:'var(--ui-bg)'}} alt={asset.original_name}/>
      <Typography.Paragraph type="secondary" style={{marginTop:12,overflowWrap:'anywhere'}}>{asset.original_name} · {asset.width} × {asset.height}{asset.source_type==='model_crop'?' · 模型裁剪图':''}</Typography.Paragraph>
      {!!asset.device_info?.crop&&<Typography.Paragraph type="secondary">裁剪来源已保存，框坐标：<code>{(asset.device_info.crop as {region:number[]}).region.join(', ')}</code></Typography.Paragraph>}
    </Col><Col xs={24} lg={9}><Space orientation="vertical" size="middle" style={{width:'100%'}}>
      <Checkbox checked={!!draft.skipped} disabled={busy} onChange={e=>setDraft(e.target.checked?{labels:[],description:'',skipped:true}:{...empty})}>无法判断 / 跳过（不作负样本）</Checkbox>
      {labels.length>0&&<><Typography.Text strong>分类 · {multiple?'多标签':'单选'}</Typography.Text>
        {multiple?<Checkbox.Group aria-label="图片分类" options={labels} value={draft.labels} disabled={busy||draft.skipped} onChange={values=>setDraft({...draft,labels:values as string[]})}/>:
          <Radio.Group aria-label="图片分类" options={labels} value={draft.labels[0]} disabled={busy||draft.skipped} onChange={e=>setDraft({...draft,labels:[e.target.value]})}/>}</>}
      <label htmlFor="image-annotation-description">{project.task_type==='classification'?'补充描述（可选）':'图片描述'}</label>
      <Input.TextArea id="image-annotation-description" value={draft.description} maxLength={20000} showCount autoSize={{minRows:6,maxRows:16}} disabled={busy||draft.skipped} onChange={e=>setDraft({...draft,description:e.target.value})}/>
      <Space wrap><Button type="primary" onClick={save} loading={busy} disabled={loading||busy||(!dirty&&!!current)}>保存标注</Button><Tag>{current?`v${current.version} · ${{pending:'待审核',approved:'已通过',rejected:'已驳回'}[current.review_status]}`:'尚未保存'}</Tag>{dirty&&<Typography.Text type="warning">有未保存修改</Typography.Text>}</Space>
      <Input aria-label="审核意见" value={comment} maxLength={2000} placeholder="审核意见（可选）" onChange={e=>setComment(e.target.value)}/>
      <Space><Button disabled={loading||dirty||busy||!current||!!draft.skipped||current.review_status==='approved'} onClick={()=>review(true)}>审核通过</Button><Button danger disabled={loading||dirty||busy||!current||current.review_status==='rejected'} onClick={()=>review(false)}>驳回</Button></Space>
      <details><summary>历史版本（{versions.length}）</summary>{versions.map(ann=><div key={ann.id}><Tag>v{ann.version}</Tag>{new Date(ann.created_at).toLocaleString('zh-CN')}<JsonView label={`标注历史 v${ann.version}`} value={ann.annotation_data}/></div>)}</details>
    </Space></Col></Row>
    <Modal title="有未保存的标注" open={blocker.state==='blocked'} onCancel={()=>blocker.reset?.()} onOk={()=>blocker.proceed?.()} okText="放弃修改并离开" cancelText="继续编辑"><p>请先保存，或确认放弃当前修改。</p></Modal>
    <Modal title="标注快捷键" open={showShortcuts} onCancel={()=>setShowShortcuts(false)} footer={null}>
      <p>← / → 或 A / D 切换图片；有未保存修改时会先提示，确认前保留草稿。</p>
      <p>Ctrl / Cmd + S 保存标注，在描述和审核意见输入框内也可使用。R 审核通过；Shift + R 驳回，使用当前审核意见。</p>
      <p>审核须先保存；跳过的图片不能通过审核。保存、审核进行中或已是对应审核状态时不重复提交。</p>
      <p>普通快捷键在输入框、选择框和弹窗中停用；弹窗内不执行保存快捷键。</p>
    </Modal>
  </Card>;
}

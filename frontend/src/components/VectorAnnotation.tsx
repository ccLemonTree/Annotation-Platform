import { useEffect,useRef,useState } from 'react';
import { Link,useSearchParams } from 'react-router-dom';
import { Alert,Button,Card,Checkbox,Col,Input,InputNumber,Modal,Radio,Row,Select,Space,Tag,Typography } from 'antd';
import { isAxiosError } from 'axios';
import { getImages,uploadImages,type LibraryImage } from '../api/images';
import { getModelServices,type ModelService } from '../api/modelServices';
import type { Project } from '../api/projects';
import { searchMatches,getMatchQueries,judgeMatch,getSmartSearchPlatforms,searchSmartMatches,type MatchQuery,type Judgment,type MatchHit,type SmartSearchReference } from '../api/projectWork';
import AssetImage from './AssetImage';
import DeleteCropButton from './DeleteCropButton';
import DeleteSelectedImages from './DeleteSelectedImages';
import useAnnotationShortcuts from './useAnnotationShortcuts';
import SmartSearchCrop from './SmartSearchCrop';

const judgments={correct:'正确',incorrect:'错误',unknown:'无法判断'};
const localTime=(date:Date)=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}T${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}:${String(date.getSeconds()).padStart(2,'0')}`;
export default function VectorAnnotation({project,mode}:{project:Project;mode:'text'|'image'}){
  const [params,setParams]=useSearchParams();
  const historyPage=Math.max(1,parseInt(params.get('query_page')||'1',10)||1),queryId=params.get('query_id');
  const [models,setModels]=useState<ModelService[]>([]);
  const [source,setSource]=useState<'smart'|'local'>('smart'),[platforms,setPlatforms]=useState<{id:string;name:string}[]>([]),[platform,setPlatform]=useState<string>();
  const [platformLoading,setPlatformLoading]=useState(true),[platformReload,setPlatformReload]=useState(0);
  const [start,setStart]=useState(()=>localTime(new Date(new Date().setHours(0,0,0,0)))),[end,setEnd]=useState(()=>localTime(new Date()));
  const [reference,setReference]=useState<SmartSearchReference>(),[cropTarget,setCropTarget]=useState<{queryId:string;hit:MatchHit}>();
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[info,setInfo]=useState(''),[reload,setReload]=useState(0);
  const [text,setText]=useState(''),[queryImage,setQueryImage]=useState<string>(),[images,setImages]=useState<LibraryImage[]>([]),[topK,setTopK]=useState(20);
  const [embedding,setEmbedding]=useState<string>(),[query,setQuery]=useState<MatchQuery>(),[history,setHistory]=useState<MatchQuery[]>([]);
  const [view,setView]=useState<'description'|'classification'>('description');
  const [selected,setSelected]=useState<string[]>([]);
  const [focused,setFocused]=useState<{queryId:string;assetId:string}>(),[showShortcuts,setShowShortcuts]=useState(false);
  const writing=useRef(false);
  const mounted=useRef(true),searchGeneration=useRef(0);
  const queryFile=useRef<HTMLInputElement>(null);
  const fail=(error:unknown)=>{if(mounted.current)setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'操作失败，请重试');};
  useEffect(()=>{mounted.current=true;getModelServices({enabled:true}).then(rows=>{if(mounted.current)setModels(rows);}).catch(fail);return()=>{mounted.current=false;};},[]);
  useEffect(()=>{let active=true;setPlatformLoading(true);
    getSmartSearchPlatforms(project.id).then(rows=>{if(active){setPlatforms(rows);setPlatform(current=>current||rows.find(p=>p.name==='杭州移动消防平台')?.id);}})
      .catch(fail).finally(()=>{if(active)setPlatformLoading(false);});return()=>{active=false;};
  },[project.id,platformReload]);
  useEffect(()=>{if(project.task_type!=='vector')return;let active=true;
    getMatchQueries(project.id,historyPage).then(rows=>{if(active){setHistory(rows);setQuery(rows.find(row=>row.id===queryId));}}).catch(fail);return()=>{active=false;};
  },[project.id,project.task_type,historyPage,queryId,reload]);
  const openQuery=(id?:string,page=historyPage,nextMode=mode)=>{const next=new URLSearchParams(params);next.set('mode',nextMode);next.set('query_page',String(page));if(id)next.set('query_id',id);else next.delete('query_id');setParams(next);setSelected([]);};
  const imageSearch=async(value='')=>{try{const result=await getImages({page:1,page_size:100,q:value||undefined});if(mounted.current)setImages(result.items);}catch(error){fail(error);}};
  const search=async()=>{if(!embedding||writing.current)return;writing.current=true;const generation=++searchGeneration.current;setBusy(true);setError('');setQuery(undefined);setSelected([]);
    try{const result=await searchMatches(project.id,{model_id:embedding,top_k:topK,...(mode==='text'?{text:text.trim()}:{image_id:queryImage})});
      if(generation===searchGeneration.current&&mounted.current){setQuery(result);openQuery(result.id,1);setReload(v=>v+1);}
    }catch(error){fail(error);}finally{writing.current=false;if(mounted.current)setBusy(false);}};
  const smartSearch=async(chosen?:SmartSearchReference)=>{if(!platform||writing.current)return;
    const target=chosen||reference;if((chosen||mode==='image')&&!target)return;
    writing.current=true;const generation=++searchGeneration.current;setBusy(true);setError('');setInfo('');setSelected([]);
    try{const result=await searchSmartMatches(project.id,{platform_id:platform,limit:topK,start_time:start.replace('T',' '),end_time:end.replace('T',' '),...((chosen||mode==='image')?target:{text:text.trim()})});
      if(generation===searchGeneration.current&&mounted.current){setQuery(result);openQuery(result.id,1,chosen?'image':mode);setReload(v=>v+1);}
    }catch(error){fail(error);}finally{writing.current=false;if(mounted.current)setBusy(false);}};
  const judge=async(assetId:string,judgment:Judgment)=>{if(!query||writing.current||query.hits.find(hit=>hit.asset_id===assetId)?.judgment===judgment)return;writing.current=true;setBusy(true);setError('');try{const updated=await judgeMatch(project.id,query,assetId,judgment);if(mounted.current){setQuery(updated);setHistory(rows=>rows.map(row=>row.id===updated.id?updated:row));}}catch(error){fail(error);}finally{writing.current=false;if(mounted.current)setBusy(false);}};
  const uploadQuery=async(file?:File)=>{if(!file||writing.current)return;writing.current=true;setBusy(true);setError('');
    try{const response=await uploadImages([file],undefined,0);if(mounted.current){setImages(response.images);setQueryImage(response.images[0]?.id);setInfo('查询图片已存入图片库，可直接检索。');}}
    catch(error){fail(error);}finally{writing.current=false;if(mounted.current)setBusy(false);}};
  const removeResults=(ids:string[])=>{
    const deleted=new Set(ids),without=(row:MatchQuery)=>({...row,hits:row.hits.filter(hit=>!deleted.has(hit.asset_id))});
    setSelected(current=>current.filter(id=>!deleted.has(id)));setQuery(row=>row?without(row):row);setHistory(rows=>rows.map(without));setReload(v=>v+1);
  };
  const active=query?.hits.find(hit=>focused?.queryId===query.id&&focused.assetId===hit.asset_id)||query?.hits[0];
  const activeIndex=query&&active?query.hits.indexOf(active):-1;
  const move=(offset:number)=>{
    if(!query||writing.current)return;const hit=query.hits[activeIndex+offset];if(!hit)return;
    setFocused({queryId:query.id,assetId:hit.asset_id});
    const card=document.getElementById('match-'+hit.asset_id);card?.focus({preventScroll:true});card?.scrollIntoView({block:'nearest'});
  };
  useAnnotationShortcuts(event=>{
    if(event.ctrlKey||event.metaKey||event.shiftKey||writing.current)return;
    if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();move(event.key==='ArrowLeft'?-1:1);}
    else if(active&&['1','2','3'].includes(event.key)){event.preventDefault();void judge(active.asset_id,(['correct','incorrect','unknown'] as Judgment[])[Number(event.key)-1]);}
  },!!active&&!busy&&!showShortcuts&&!cropTarget);
  return <Space orientation="vertical" size="middle" style={{width:'100%'}}>
    {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>setReload(v=>v+1)}>刷新重试</Button>}/>}{info&&<Alert type="success" title={info}/>}
    <Card title="向量检索与匹配标注" extra={<Button onClick={()=>setShowShortcuts(true)}>快捷键</Button>}>
      <Radio.Group aria-label="匹配检索来源" value={source} disabled={busy} onChange={e=>setSource(e.target.value)} options={[{value:'smart',label:'智搜平台'},{value:'local',label:'本地向量索引'}]} style={{marginBottom:16}}/>
      <Typography.Paragraph type="secondary">{source==='smart'?'直接搜索智搜平台，结果按目标框切图后保存到当前项目，可立即标注，无需建立本地索引。':'图片先建立 Milvus 索引，再用文本或图片检索。'}正确 / 错误仅针对当前查询；描述与类别按图片共用。</Typography.Paragraph>
      {source==='smart'&&<Space wrap style={{marginBottom:16}}>
        <Select aria-label="智搜平台" showSearch optionFilterProp="label" placeholder="选择智搜平台" style={{width:280,maxWidth:'100%'}} loading={platformLoading} value={platform} disabled={busy} onChange={value=>{setPlatform(value);setReference(undefined);}} options={platforms.map(p=>({value:p.id,label:p.name}))}/>
        <Button disabled={busy||platformLoading} onClick={()=>setPlatformReload(v=>v+1)}>刷新平台</Button>
        <label>开始时间 <Input aria-label="智搜开始时间" type="datetime-local" step={1} value={start} disabled={busy} onChange={e=>setStart(e.target.value)} style={{width:235}}/></label>
        <label>结束时间 <Input aria-label="智搜结束时间" type="datetime-local" step={1} value={end} disabled={busy} onChange={e=>setEnd(e.target.value)} style={{width:235}}/></label>
      </Space>}
      <Space wrap style={{marginBottom:16}}>
        {source==='local'&&<Select aria-label="检索向量模型" placeholder="选择已建立索引的模型" style={{width:260,maxWidth:'100%'}} value={embedding} disabled={busy} onChange={value=>{setEmbedding(value);setQuery(undefined);setSelected([]);}} options={models.filter(m=>m.kind==='embedding').map(m=>({value:m.id,label:`${m.name} · r${m.revision}`}))}/>}
        {mode==='text'?<Input aria-label="匹配查询文本" value={text} disabled={busy} maxLength={2000} placeholder="例如：穿黑色衣服的人" style={{width:300,maxWidth:'100%'}} onChange={e=>setText(e.target.value)}/>:
          source==='smart'?<Typography.Text>{reference?'已选择智搜目标框，可调整时间或数量后再次搜索。':'先在文搜图结果卡片点击“用此图搜图”，框选查询目标。'}</Typography.Text>:<><Select aria-label="匹配查询图片" showSearch filterOption={false} onSearch={imageSearch} placeholder="搜索图片库文件名" style={{width:280,maxWidth:'100%'}} value={queryImage} onChange={setQueryImage} disabled={busy} options={images.map(i=>({value:i.id,label:i.original_name}))}/>
            <Button disabled={busy} onClick={()=>queryFile.current?.click()}>上传查询图片</Button><input ref={queryFile} type="file" aria-label="上传匹配查询图片" accept="image/jpeg,image/png,image/webp,image/bmp" disabled={busy} style={{display:'none'}} onChange={e=>void uploadQuery(e.target.files?.[0])}/></>}
        <InputNumber aria-label="匹配返回数量" value={topK} min={1} max={100} disabled={busy} onChange={v=>setTopK(v||20)}/>
        <Button aria-label="检索并标注" type="primary" disabled={busy||(source==='smart'?(!platform||!start||!end||end<start||(mode==='text'?!text.trim():!reference)):(!embedding||(mode==='text'?!text.trim():!queryImage)))} loading={busy} onClick={()=>source==='smart'?void smartSearch():void search()}>检索并标注</Button>
      </Space>
      {busy&&<Alert type="info" showIcon title="正在处理，请稍候…" description={source==='smart'?'正在登录平台、搜索并裁剪图片。完成后显示可标注的目标切图。':undefined} style={{marginBottom:16}}/>}
      <Space wrap style={{marginBottom:16}}><Select aria-label="历史匹配查询" placeholder="打开已保存的查询与判定" value={query?.id} style={{width:320,maxWidth:'100%'}} disabled={busy} onChange={id=>{setQuery(history.find(q=>q.id===id));openQuery(id);}} options={history.map(q=>({value:q.id,label:`${q.query.platform_name||'本地'} · ${q.query.text||'以图搜图'} · ${q.id.slice(0,8)}`}))}/>
        <Button disabled={historyPage===1||busy} onClick={()=>openQuery(undefined,historyPage-1)}>上一页查询</Button><Button disabled={history.length<20||busy} onClick={()=>openQuery(undefined,historyPage+1)}>下一页查询</Button>
        <Radio.Group aria-label="匹配标注视图" value={view} onChange={e=>setView(e.target.value)} options={[{value:'description',label:'描述视图'},{value:'classification',label:'分类视图'}]}/></Space>
      {query&&active&&<Space wrap style={{marginBottom:16}}>
        <Button aria-label="上一匹配图片" disabled={busy||activeIndex<=0} onClick={()=>move(-1)}>← 上一张</Button>
        <Typography.Text style={{overflowWrap:'anywhere'}}>当前 {activeIndex+1} / {query.hits.length} · {active.original_name}</Typography.Text>
        <Button aria-label="下一匹配图片" disabled={busy||activeIndex>=query.hits.length-1} onClick={()=>move(1)}>下一张 →</Button>
        <Typography.Text type="secondary">1 正确 · 2 错误 · 3 无法判断（自动保存）</Typography.Text>
      </Space>}
      {!!query?.space.failures?.length&&<Alert type="warning" showIcon title={`${query.space.failures.length} 张图片下载或裁剪失败，其他结果可正常标注`} description={query.space.failures.slice(0,5).map(f=>`第 ${f.index} 条：${f.error}`).join('；')} style={{marginBottom:16}}/>}
      {query&&<><Space wrap style={{marginBottom:16}}><Checkbox checked={query.hits.length>0&&selected.length===query.hits.length} indeterminate={selected.length>0&&selected.length<query.hits.length} disabled={busy||!query.hits.length} onChange={e=>setSelected(e.target.checked?query.hits.map(hit=>hit.asset_id):[])}>全选本次结果</Checkbox><DeleteSelectedImages ids={selected} disabled={busy} onDeleted={removeResults}/><DeleteSelectedImages disabled={busy||!query.hits.length} allLabel="删除本次全部结果" rangeLabel="当前查询已返回的全部图片" loadAll={async()=>query.hits.map(hit=>hit.asset_id)} onDeleted={removeResults}/></Space><Typography.Paragraph>当前查询：{query.query.text||'图片查询'} · {query.query.source==='smart_search'?query.query.platform_name:`模型 r${query.space.revision}`} · {query.hits.length} 个结果</Typography.Paragraph>{query.query.query_asset_id&&<Space style={{marginBottom:16}}><Typography.Text>查询目标</Typography.Text><AssetImage assetId={query.query.query_asset_id} width={100} height={80} style={{objectFit:'contain'}}/></Space>}<Row gutter={[16,16]}>
        {query.hits.map(hit=><Col xs={24} sm={12} xl={8} key={hit.asset_id}><Card size="small" id={'match-'+hit.asset_id} role="group" aria-label={`匹配图片 ${hit.original_name}`} tabIndex={0} data-match-active={active?.asset_id===hit.asset_id?'true':undefined}
          style={{borderColor:active?.asset_id===hit.asset_id?'var(--ui-primary)':undefined}}
          onFocusCapture={()=>{if(!writing.current)setFocused({queryId:query.id,assetId:hit.asset_id});}} onPointerDown={()=>{if(!writing.current)setFocused({queryId:query.id,assetId:hit.asset_id});}}
          title={<Space>{active?.asset_id===hit.asset_id&&<Tag color="blue">当前图片</Tag>}<Tag color={hit.judgment==='correct'?'success':hit.judgment==='incorrect'?'error':'default'}>{judgments[hit.judgment]}</Tag></Space>}>
          <Checkbox aria-label={`选择图片 ${hit.original_name}`} checked={selected.includes(hit.asset_id)} disabled={busy} onChange={e=>setSelected(ids=>e.target.checked?[...ids,hit.asset_id]:ids.filter(id=>id!==hit.asset_id))} style={{marginBottom:12}}>选择图片</Checkbox>
          <AssetImage assetId={hit.asset_id} width="100%" height={180} style={{objectFit:'contain'}} alt={hit.original_name}/>
          <Typography.Paragraph type="secondary">{hit.smart_search?'平台分值':'相似度'} {hit.score.toFixed(4)}{hit.smart_search&&<><br/>{hit.smart_search.record.deviceName} · {hit.smart_search.record.timestamp}</>}</Typography.Paragraph>
          {view==='description'?<Typography.Paragraph style={{minHeight:44}}>{hit.description||'尚无描述，可打开图片编辑'}</Typography.Paragraph>:<Space wrap style={{marginBottom:16}}>{hit.labels?.length?hit.labels.map(label=><Tag key={label}>{label}</Tag>):<Tag>尚无分类</Tag>}</Space>}
          <Radio.Group aria-label={`判定 ${hit.asset_id}`} value={hit.judgment} disabled={busy} onChange={e=>judge(hit.asset_id,e.target.value)} options={Object.entries(judgments).map(([value,label])=>({value,label}))}/>
          <Space wrap style={{marginTop:12}}>{hit.smart_search&&<Button disabled={busy||!platform||source!=='smart'} onClick={()=>setCropTarget({queryId:query.id,hit})}>用此图搜图</Button>}<Link to={`/annotate/${hit.asset_id}?from=vector&${params}`}>编辑描述 / 分类</Link>{hit.source_type==='model_crop'&&<DeleteCropButton assetId={hit.asset_id} name={hit.original_name} disabled={busy} onDeleted={()=>removeResults([hit.asset_id])}/>}</Space>
        </Card></Col>)}
      </Row>{!query.hits.length&&<Alert type="info" title="当前查询没有可用结果，请检查索引或调整查询。"/>}</>}
    </Card>
    {cropTarget&&<SmartSearchCrop key={cropTarget.hit.asset_id} projectId={project.id} queryId={cropTarget.queryId} hit={cropTarget.hit} onCancel={()=>setCropTarget(undefined)} onSearch={chosen=>{setCropTarget(undefined);setReference(chosen);void smartSearch(chosen);}}/>}
    {query&&<Alert type="info" showIcon title={`判定自动保存 · 已判定 ${query.hits.filter(hit=>hit.judgment!=='unknown').length} / ${query.hits.length}`} description="完成正确 / 错误判定后，进入审核调用大模型；一致自动通过，分歧交人工复核。审核后从“导出与训练”继续。" action={<Link to={`/annotations?project_id=${project.id}`}>下一步：审核匹配 →</Link>}/>}
    <Modal title="向量标注快捷键" open={showShortcuts} onCancel={()=>setShowShortcuts(false)} footer={null}>
      <p>← / → 选择上一个 / 下一个匹配结果，也可点击卡片选择当前图片，蓝色边框标示操作对象。</p>
      <p>1 判定正确；2 判定错误；3 标记无法判断。判定立即保存到当前查询，不自动切图。</p>
      <p>保存期间及已是对应状态时不重复提交；输入框、选择框、图片预览和弹窗内不触发快捷键。编辑图片描述后，可用 Ctrl / Cmd + S 保存、R 通过、Shift + R 驳回。</p>
    </Modal>
  </Space>;
}

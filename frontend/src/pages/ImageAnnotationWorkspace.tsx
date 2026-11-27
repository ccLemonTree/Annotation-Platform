import { useEffect,useMemo,useRef,useState } from 'react';
import JsonView from '../components/JsonView';
import AnnotationLabelFilter from '../components/AnnotationLabelFilter';
import { Link,useBlocker,useNavigate,useSearchParams } from 'react-router-dom';
import { Alert,App,AutoComplete,Button,Card,Checkbox,Input,InputNumber,Modal,Radio,Select,Space,Tag,Typography } from 'antd';
import { isAxiosError } from 'axios';
import type { Asset,AssetNeighbors,AssetAttributeFilters,AssetImageKind } from '../api/assets';
import { getAssetNeighbors,getAssets,getProjectAssetCollections,readAssetImageKind } from '../api/assets';
import type { Project } from '../api/projects';
import { createAnnotation,getAnnotations,updateAnnotation,annotationReviewStatus, reviewAnnotation,type Annotation } from '../api/annotations';
import AnnotationImageViewer, {AnnotationCropLabelSelect,AnnotationImageKindSelect} from '../components/AnnotationImageViewer';
import type {ImageView} from '../components/useAnnotationImageView';
import useFittedImageColumn from '../components/useFittedImageColumn';
import { rememberAnnotationProgress } from '../components/annotationProgress';
import useAnnotationShortcuts from '../components/useAnnotationShortcuts';
import { useCurrentUser } from '../components/currentUser';
import AttributeEditor from '../components/AttributeEditor';
import CurrentImageAnalysis from '../components/CurrentImageAnalysis';
import {AnnotationFullscreenButton} from '../components/AnnotationStage';
import type {AttributeSchema,AttributeValues} from '../api/objectAttributes';
import type {ImageAnalysis} from '../api/annotationAnalysis';
import './ImageAnnotationWorkspace.css';

type Data={description:string;labels:string[];skipped?:boolean;attribute_schema?:AttributeSchema;attributes?:AttributeValues};
type QueueFilters=AssetAttributeFilters&{label?:string;crop_label?:string[];review_status?:Annotation['review_status'];collection_id?:string;image_kind?:AssetImageKind};
const empty:Data={description:'',labels:[]};
export default function ImageAnnotationWorkspace({asset,project,cropLabels,suggestions={},suggestionsError,onRefreshSuggestions,imageView,setImageView}:{asset:Asset;project:Project;cropLabels:string[];suggestions?:Record<string,string[]>;suggestionsError?:string;onRefreshSuggestions?:()=>void;imageView:ImageView;setImageView:(view:ImageView)=>void}){
  const canvasPanels=useFittedImageColumn();
  const admin=useCurrentUser()?.role==='admin';
  const [params,setParams]=useSearchParams();
  const { message: notice } = App.useApp();
  const navigate=useNavigate(),writing=useRef(false);
  const navigationRequest=useRef(0);
  const classification=project.task_type==='classification';
  const annotationLabel=params.get('label')||undefined,collectionId=params.get('collection_id')||undefined;
  const cropLabel=useMemo(()=>params.getAll('crop_label'),[params]);
  const workJobId=params.get('work_job_id')||undefined;
  const imageKind=readAssetImageKind(params.get('image_kind'));
  const attributeKey=classification?undefined:params.get('attribute_key')||undefined,attributeValue=attributeKey?params.get('attribute_value')||undefined:undefined;
  const attributeState=(attributeKey?params.get('attribute_state')||'all':'all') as AssetAttributeFilters['attribute_state'];
  const attributeFilters:AssetAttributeFilters={attribute_key:attributeKey,attribute_value:attributeValue,attribute_state:attributeState};
  const reviewStatus=['pending','approved','rejected'].includes(params.get('review_status')||'')?params.get('review_status') as Annotation['review_status']:undefined;
  const returnParams=new URLSearchParams(params);returnParams.delete('from');
  const fromReview=params.get('from')==='review';
  const fromAnnotation=params.get('from')==='annotation';
  const fromVector=params.get('from')==='vector';
  const fromAttributes=params.get('from')==='attributes'&&admin;
  const back=workJobId&&admin?`/projects/${project.id}/images?work_job_id=${workJobId}&kind=image`:fromAttributes?`/projects/${project.id}/prepare`:(fromReview||fromAnnotation)?`/annotations?${returnParams}`:project.task_type==='vector'?`/annotate/project/${project.id}${fromVector?`?${returnParams}`:''}`:admin?`/projects/${project.id}/images`:`/annotations?project_id=${project.id}`;
  const [versions,setVersions]=useState<Annotation[]>([]),[current,setCurrent]=useState<Annotation>(),[draft,setDraft]=useState<Data>(empty);
  const [descriptionCollapsed,setDescriptionCollapsed]=useState(false);
  const [attributesCollapsed,setAttributesCollapsed]=useState(false);
  const savedData=current?.annotation_data;
  const importedLabels:string[]=!classification&&savedData?.import_source?.source==='json_labels'?savedData.labels||[]:[];
  const hasImageAnnotation=!!savedData&&(!!savedData.skipped||(classification?!!savedData.labels?.length:
    !!savedData.description?.trim()||!!savedData.attributes||!!importedLabels.length));
  const [original,setOriginal]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [neighbors,setNeighbors]=useState<AssetNeighbors>(),[comment,setComment]=useState('');
  const [showShortcuts,setShowShortcuts]=useState(false);
  const [collections,setCollections]=useState<{id:string;name:string}[]>([]),[collectionError,setCollectionError]=useState('');
  const [navigationError,setNavigationError]=useState(''),[navigationReload,setNavigationReload]=useState(0);
  const [jumpPosition,setJumpPosition]=useState<number|null>(null),[jumping,setJumping]=useState(false);
  const [reviewDestination,setReviewDestination]=useState<string>();
  const [attributeValueInput,setAttributeValueInput]=useState(attributeValue||'');
  const dirty=!!original&&JSON.stringify(draft)!==original;
  const blocker=useBlocker(dirty);
  useEffect(()=>{setAttributeValueInput(attributeValue||'');},[attributeKey,attributeValue]);
  useEffect(()=>{if(reviewDestination&&!dirty&&!busy)void navigate(`/annotate/${reviewDestination}?${params}`);},[reviewDestination,dirty,busy,navigate,params]);
  useEffect(()=>{let active=true;rememberAnnotationProgress(asset);
    getAnnotations({asset_id:asset.id,latest_only:false,page_size:100})
      .then(response=>{if(!active)return;const latest=response.items.find(a=>a.is_latest);
        const saved=latest?.annotation_data||{};
        const data:Data={description:classification?'':saved.description||'',labels:classification?saved.labels||[]:[],
          ...(saved.skipped?{skipped:true}:{}),...(!classification&&saved.attribute_schema?{attribute_schema:saved.attribute_schema,attributes:saved.attributes}:{})};
        setDescriptionCollapsed(!!data.attribute_schema&&!!data.attributes&&!data.description.trim());
        setAttributesCollapsed(false);
        setVersions(response.items);setCurrent(latest);setDraft(data);setOriginal(JSON.stringify(data));})
      .catch(()=>{if(active)setError('读取标注失败，请重新打开');}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[asset,classification]);
  useEffect(()=>{let active=true;setCollectionError('');
    getProjectAssetCollections(project.id,true).then(rows=>{if(active)setCollections(rows);})
      .catch(()=>{if(active)setCollectionError('图片集读取失败，请重试');});
    return()=>{active=false;};
  },[project.id,navigationReload]);
  useEffect(()=>{let active=true;setNeighbors(undefined);setNavigationError('');setJumpPosition(null);
    getAssetNeighbors(asset.id,annotationLabel,reviewStatus,collectionId,{attribute_key:attributeKey,attribute_value:attributeValue,attribute_state:attributeState,work_job_id:workJobId,image_kind:imageKind,crop_label:cropLabel,annotation_kind:'image'}).then(rows=>{if(active){setNeighbors(rows);setJumpPosition(rows.matched===false?null:rows.position);}})
      .catch(()=>{if(active)setNavigationError('筛选图片顺序读取失败，请重试');});
    return()=>{active=false;};
  },[asset.id,annotationLabel,cropLabel,reviewStatus,collectionId,attributeKey,attributeValue,attributeState,workJobId,imageKind,navigationReload,current?.version,current&&annotationReviewStatus(current,'image')]);
  useEffect(()=>()=>{navigationRequest.current++;},[asset.id]);
  useEffect(()=>{if(!dirty)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty]);
  const fail=(error:unknown)=>setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'操作失败，请重试');
  const saveDraft=async()=>{
    const data=draft.skipped?{labels:[],description:'',skipped:true}:draft;
    const ann=current?await updateAnnotation(current.id,{kind:'image',base_version:current.version,annotation_data:data}):await createAnnotation({asset_id:asset.id,kind:'image',base_version:0,annotation_data:data});
    setCurrent(ann);setVersions(rows=>[ann,...rows.map(row=>({...row,is_latest:false}))]);setOriginal(JSON.stringify(data));setDraft(data);
    return ann;
  };
  const savingSkip=blocker.state==='blocked'&&!!draft.skipped&&/^\/annotate\/[^/]+$/.test(blocker.location.pathname);
  useEffect(()=>{
    if(!savingSkip||writing.current||blocker.state!=='blocked')return;
    writing.current=true;setBusy(true);setError('');
    void saveDraft().then(()=>blocker.proceed()).catch(error=>{fail(error);blocker.reset();})
      .finally(()=>{writing.current=false;setBusy(false);});
  },[savingSkip,blocker]);
  const save=async()=>{if(loading||writing.current||(!dirty&&hasImageAnnotation))return;writing.current=true;setBusy(true);setError('');try{
    await saveDraft();
  }catch(error){fail(error);}finally{writing.current=false;setBusy(false);}};
  const review=async(approved:boolean)=>{if(!current||!hasImageAnnotation||loading||writing.current||dirty||annotationReviewStatus(current,'image')===(approved?'approved':'rejected'))return;writing.current=true;setBusy(true);setError('');try{
    const updated=await reviewAnnotation(current.id,{kind:'image', approved,review_comment:comment});setCurrent(updated);setVersions(rows=>rows.map(a=>a.id===updated.id?updated:a));
  }catch(error){fail(error);}finally{writing.current=false;setBusy(false);}};
  const go=(id:string,query=params)=>navigate(`/annotate/${id}?${query}`);
  const canApproveNext=!loading&&!busy&&!jumping&&(!!draft.skipped||(classification?!!draft.labels.length:
    !!draft.attributes||!!draft.description.trim()||!!importedLabels.length));
  const approveNext=async()=>{
    if(!canApproveNext||writing.current)return;
    writing.current=true;setBusy(true);setError('');
    try{
      const ann=dirty||!current||!hasImageAnnotation?await saveDraft():current;
      if(annotationReviewStatus(ann,'image')!=='approved'){
        const updated=await reviewAnnotation(ann.id,{kind:'image', approved:true,review_comment:comment});
        setCurrent(updated);setVersions(rows=>rows.map(row=>row.id===updated.id?updated:row));
      }
      const queue=await getAssetNeighbors(asset.id,annotationLabel,reviewStatus,collectionId,{...attributeFilters,work_job_id:workJobId,image_kind:imageKind,crop_label:cropLabel,annotation_kind:'image'});setNeighbors(queue);
      if(queue.next_id)setReviewDestination(queue.next_id);
      else notice.info('已通过，当前筛选已无下一张');
    }catch(error){fail(error);}finally{writing.current=false;setBusy(false);}
  };
  const filterQueue=async(changes:QueueFilters)=>{
    if(loading||dirty||busy||jumping)return false;
    const query=new URLSearchParams(window.location.search);query.delete('page');
    const {crop_label,...filters}=changes;
    for(const [key,value] of Object.entries(filters)){
      if(value)query.set(key,value);else query.delete(key);
    }
    if(crop_label){
      query.delete('crop_label');
      crop_label.forEach(label=>query.append('crop_label',label));
    }
    if(query.get('image_kind')==='originals')query.delete('crop_label');
    const nextFilters:QueueFilters={label:query.get('label')||undefined,review_status:(query.get('review_status')||undefined) as QueueFilters['review_status'],
      crop_label:query.getAll('crop_label'),
      collection_id:query.get('collection_id')||undefined,image_kind:readAssetImageKind(query.get('image_kind')),attribute_key:query.get('attribute_key')||undefined,attribute_value:query.get('attribute_value')||undefined,
      attribute_state:(query.get('attribute_state')||'all') as QueueFilters['attribute_state']};
    if(!nextFilters.label&&!nextFilters.crop_label?.length&&!nextFilters.review_status&&!nextFilters.collection_id&&!nextFilters.attribute_key&&nextFilters.image_kind==='all'){setParams(query);return true;}
    const request=++navigationRequest.current;setJumping(true);
    try{
      const result=await getAssets({project_id:project.id,work_job_id:workJobId,scope:'annotation', annotation_kind:'image',...nextFilters,page:1,page_size:1});
      if(request!==navigationRequest.current)return false;
      if(!result.items[0]){notice.info('当前筛选没有匹配图片，已保留当前图片和筛选');return false;}
      await go(result.items[0].id,query);
      return true;
    }catch{if(request===navigationRequest.current)notice.error('筛选失败，已保留当前图片和筛选，请重试');}
    finally{if(request===navigationRequest.current)setJumping(false);}
    return false;
  };
  const applyAttributeValue=async(value:string)=>{
    const previous=new URLSearchParams(window.location.search).get('attribute_value')||'';
    if(!await filterQueue({attribute_value:value.trim()||undefined}))setAttributeValueInput(previous);
  };
  const jump=async()=>{
    if(loading||!neighbors||busy||jumping)return;
    if(jumpPosition===null||!Number.isInteger(jumpPosition)||jumpPosition<1||jumpPosition>neighbors.total){notice.warning(`请输入1到${neighbors.total}之间的整数`);return;}
    if(jumpPosition===neighbors.position)return;
    const request=++navigationRequest.current;setJumping(true);
    try{
      const result=await getAssets({project_id:project.id,work_job_id:workJobId,scope:'annotation', annotation_kind:'image',image_kind:imageKind,label:annotationLabel,crop_label:cropLabel,review_status:reviewStatus,collection_id:collectionId,...attributeFilters,page:jumpPosition,page_size:1});
      if(request!==navigationRequest.current)return;
      if(!result.items[0]){notice.warning('此序号已不存在，请重试切图读取');return;}
      await go(result.items[0].id);
    }catch{if(request===navigationRequest.current)notice.error('跳转失败，当前图片与草稿已保留，请重试');}
    finally{if(request===navigationRequest.current)setJumping(false);}
  };
  const attributeMode=!classification&&!!(draft.attribute_schema||project.label_schema?.object_attributes||fromAttributes);
  const advanceOnReview=classification||attributeMode||!!draft.skipped;
  const labels=Array.isArray(project.label_schema?.labels)?project.label_schema.labels as string[]:[];
  const multiple=project.label_schema?.classification_mode==='multi';
  useAnnotationShortcuts((event,key)=>{
    if((event.ctrlKey||event.metaKey)&&!event.shiftKey&&key==='s'){event.preventDefault();void save();return;}
    if(event.ctrlKey||event.metaKey||writing.current||jumping)return;
    if(key==='r'){event.preventDefault();if(!event.shiftKey&&advanceOnReview)void approveNext();else void review(!event.shiftKey);}
    else if(!event.shiftKey&&(key==='arrowleft'||key==='a'||key==='arrowright'||key==='d')){
      event.preventDefault();const id=key==='arrowleft'||key==='a'?neighbors?.previous_id:neighbors?.next_id;
      if(id)void go(id);
    }
    else if(classification&&!multiple&&!draft.skipped&&!event.shiftKey&&/^[1-9]$/.test(key)&&labels[Number(key)-1]){
      event.preventDefault();setDraft(prev=>({...prev,skipped:false,labels:[labels[Number(key)-1]]}));
    }
  },!loading&&!showShortcuts,true);
  useEffect(()=>{
    if(!classification||multiple||loading||busy||jumping||showShortcuts||draft.skipped||!labels.length)return;
    const onDigit=(event:KeyboardEvent)=>{
      if(event.defaultPrevented||event.repeat||event.isComposing||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return;
      if(!/^[1-9]$/.test(event.key))return;
      const target=event.target instanceof Element?event.target:null;
      if(target?.closest('input:not([type="radio"]),textarea,select,[contenteditable="true"],[role="textbox"],[role="combobox"],[role="dialog"],.ant-select,.ant-modal-wrap,.ant-drawer'))return;
      const picked=labels[Number(event.key)-1];
      if(picked){event.preventDefault();setDraft(prev=>({...prev,skipped:false,labels:[picked]}));}
    };
    window.addEventListener('keydown',onDigit);
    return()=>window.removeEventListener('keydown',onDigit);
  },[classification,multiple,loading,busy,jumping,showShortcuts,draft.skipped,labels]);
  const preannotations=versions.find(row=>row.annotation_source==='auto'&&JSON.stringify(row.annotation_data.attribute_schema)===JSON.stringify(draft.attribute_schema))?.annotation_data.attributes as AttributeValues|undefined;
  const projectFields=(project.label_schema?.object_attributes as AttributeSchema|undefined)?.fields||[];
  const attributeFields=[...projectFields,...(draft.attribute_schema?.fields||[]).filter(field=>!projectFields.some(current=>current.key===field.key))];
  const attributeValueDisabled=loading||dirty||busy||jumping||!attributeKey||attributeState==='unknown'||attributeState==='missing';
  const applyAnalysis=(result:ImageAnalysis)=>{
    if(result.attribute_schema&&result.attributes)setAttributesCollapsed(false);
    else if(result.description!=null)setDescriptionCollapsed(false);
    setDraft(previous=>{
      if(result.attribute_schema&&result.attributes){
        const sameSchema=JSON.stringify(previous.attribute_schema)===JSON.stringify(result.attribute_schema);
        const attributes=Object.fromEntries(Object.entries(result.attributes).map(([key,item])=>[key,{...item,
          ...(sameSchema&&previous.attributes?.[key]?.probabilities?{probabilities:previous.attributes[key].probabilities}:{})}]));
        return {...previous,skipped:false,attribute_schema:result.attribute_schema,attributes};
      }
      return {...previous,skipped:false,description:result.description??previous.description};
    });
    notice.success('分析结果已填入，请核对并保存');
  };
  const imageFields=<Space orientation="vertical" size="middle" style={{width:'100%'}}>
    {classification?<><Typography.Text strong>图片分类 · {multiple?'可多选':'单选（快捷键 1–9）'}</Typography.Text>
      {!labels.length&&<Typography.Text type="secondary">请在项目设置中添加标注类别。</Typography.Text>}
      {multiple?<Checkbox.Group aria-label="图片分类" options={labels} value={draft.labels} disabled={loading||busy||draft.skipped} onChange={values=>setDraft({...draft,labels:values as string[]})}/>:
        <div data-annotation-view><Radio.Group aria-label="图片分类" options={labels.map((item,idx)=>({value:item,label:idx<9?`${idx+1}. ${item}`:item}))} value={draft.labels[0]} disabled={loading||busy||draft.skipped} onChange={e=>setDraft({...draft,labels:[e.target.value]})}/></div>}</>:<>
      <div className="image-annotation-attribute-heading">
        <label htmlFor={descriptionCollapsed?undefined:'image-annotation-description'}>图片描述</label>
        {attributeMode&&<Button size="small" aria-expanded={!descriptionCollapsed} onClick={()=>setDescriptionCollapsed(value=>!value)}>{descriptionCollapsed?'展开图片描述':'收起图片描述'}</Button>}
      </div>
      {!descriptionCollapsed&&<Input.TextArea id="image-annotation-description" value={draft.description} maxLength={20000} showCount autoSize={{minRows:6,maxRows:16}} disabled={loading||busy||draft.skipped} onChange={e=>setDraft({...draft,description:e.target.value})}/>}
    </>}
  </Space>;
  return <Card className="image-annotation-workspace" title={<Space wrap><span>{project.project_name}</span><Tag>{classification?'图片分类':attributeMode?'图片描述与属性':'图片描述标注'}</Tag></Space>}
    extra={<Space wrap><Button onClick={()=>setShowShortcuts(true)}>快捷键</Button><AnnotationFullscreenButton/>{!classification&&<CurrentImageAnalysis assetId={asset.id} name={asset.original_name} attributesAvailable={!!project.label_schema?.object_attributes} disabled={loading||busy||jumping} onApply={applyAnalysis}/>}</Space>}>
    <div className="image-annotation-navigation"><Link to={back}>{workJobId&&admin?'返回处理结果':fromAttributes?'返回批量处理':fromAnnotation?'返回人工标注':fromReview?'返回审核列表':project.task_type==='vector'?'返回向量标注':admin?'返回项目图片':'返回标注与审核'}</Link>
      <Space wrap>
        {neighbors?.previous_id&&<Link aria-disabled={busy||jumping} onClick={event=>{if(busy||jumping)event.preventDefault();}} to={`/annotate/${neighbors.previous_id}?${params}`}>上一张</Link>}
        {neighbors&&<Space size={6}><InputNumber aria-label="跳转图片序号" min={1} max={neighbors.total||1} precision={0} value={jumpPosition} placeholder={neighbors.matched===false?'未匹配':''} style={{width:88}} disabled={loading||busy||jumping||!neighbors.total} onChange={setJumpPosition} onPressEnter={()=>void jump()} title="输入图片序号，按回车跳转"/><Typography.Text aria-live="polite">{neighbors.matched===false?`未匹配 / ${neighbors.total} 张`:`第 ${neighbors.position} / ${neighbors.total} 张`}</Typography.Text></Space>}
        {neighbors?.next_id&&<Link aria-disabled={busy||jumping} onClick={event=>{if(busy||jumping)event.preventDefault();}} to={`/annotate/${neighbors.next_id}?${params}`}>下一张</Link>}
      </Space>
    </div>
    <Space wrap className="image-annotation-toolbar">
      <AnnotationImageKindSelect value={imageKind} cropsOnly={project.label_schema?.annotation_scope==='crops'}
        disabled={loading||dirty||busy||jumping} onChange={value=>void filterQueue({image_kind:value==='all'?undefined:value})}/>
      <Select aria-label="筛选图片集" placeholder="全部图片集" allowClear showSearch optionFilterProp="label" value={collectionId} options={collections.map(row=>({value:row.id,label:row.name}))} style={{minWidth:170}} disabled={loading||dirty||busy||jumping} onChange={value=>void filterQueue({collection_id:value})}/>
      <AnnotationCropLabelSelect labels={cropLabels} value={cropLabel} disabled={loading||dirty||busy||jumping} onChange={crop_label=>void filterQueue({crop_label,...(crop_label.length&&imageKind==='originals'?{image_kind:'crops'}:{})})}/>
      <AnnotationLabelFilter schema={project.label_schema} value={annotationLabel}
        disabled={loading||dirty||busy||jumping} onChange={value=>void filterQueue({label:value})}/>
      <Select<Annotation['review_status']|''> aria-label="筛选审核状态" prefix="审核：" value={reviewStatus||''} style={{minWidth:170}} disabled={loading||dirty||busy||jumping} options={[{value:'',label:'全部状态'},{value:'pending',label:'待审核'},{value:'approved',label:'已通过'},{value:'rejected',label:'已驳回'}]} onChange={value=>void filterQueue({review_status:value||undefined})}/>
    </Space>
    {attributeMode&&<Space wrap className="image-annotation-toolbar">
      <Select aria-label="筛选目标属性" prefix="属性：" placeholder="全部属性" allowClear showSearch optionFilterProp="label" value={attributeKey} style={{minWidth:180}} disabled={loading||dirty||busy||jumping} options={attributeFields.map(field=>({value:field.key,label:field.name}))} onChange={attribute_key=>void filterQueue({attribute_key,attribute_value:undefined,attribute_state:attribute_key?'analyzed':undefined})}/>
      <AutoComplete value={attributeValueInput} onChange={setAttributeValueInput} onSelect={value=>void applyAttributeValue(value)} defaultActiveFirstOption={false} options={(attributeFields.find(field=>field.key===attributeKey)?.options||[]).map(value=>({value}))} filterOption={(input,option)=>String(option?.value).includes(input)} style={{width:260,maxWidth:'100%'}} disabled={attributeValueDisabled}>
        <Input.Search aria-label="属性值筛选" placeholder="输入精确值，回车筛选" enterButton="筛选" maxLength={500} onSearch={value=>void applyAttributeValue(value)}/>
      </AutoComplete>
      <Select aria-label="筛选属性状态" value={attributeState} style={{minWidth:190}} disabled={loading||dirty||busy||jumping||!attributeKey} options={[{value:'all',label:'全部（含尚未分析）'},{value:'analyzed',label:'已分析（含无法判断）'},{value:'known',label:'已确认值'},{value:'unknown',label:'无法判断'},{value:'missing',label:'尚未分析此属性'}]} onChange={attribute_state=>void filterQueue({attribute_state,...(['unknown','missing'].includes(attribute_state)?{attribute_value:undefined}:{})})}/>
    </Space>}
    {error&&<Alert type="error" showIcon title={error} style={{marginBottom:16}}/>}
    {navigationError&&<Alert type="error" showIcon title={navigationError} action={<Button size="small" onClick={()=>setNavigationReload(value=>value+1)}>重试切图读取</Button>}/>}
    {collectionError&&<Alert type="error" showIcon title={collectionError} action={<Button size="small" onClick={()=>setNavigationReload(value=>value+1)}>重试图片集读取</Button>}/>}
    {neighbors?.matched===false&&<Alert type="info" showIcon title={neighbors.total?'当前图片已不符合筛选，上一张 / 下一张仍在筛选结果中切换':'当前筛选已无匹配图片，可清除筛选查看全部'} action={<Button size="small" disabled={dirty||busy||jumping} onClick={()=>void filterQueue({label:undefined,crop_label:[],review_status:undefined,collection_id:undefined,attribute_key:undefined,attribute_value:undefined,attribute_state:undefined,image_kind:undefined})}>清除筛选</Button>}/>}
    <div className="image-annotation-panels" ref={canvasPanels}><section className="image-annotation-picture" aria-label="当前标注图片"><AnnotationImageViewer asset={asset} view={imageView} setView={setImageView}/>
    </section><div className="image-annotation-sidebar"><section className="image-annotation-fields" aria-label="标注内容" tabIndex={0}><Space orientation="vertical" size={attributeMode?'small':'middle'} style={{width:'100%'}}>
      <Checkbox data-annotation-skip checked={!!draft.skipped} disabled={loading||busy} onChange={e=>setDraft(e.target.checked?{labels:[],description:'',skipped:true}:{...empty})}>无法判断 / 跳过（不作负样本）</Checkbox>
      {draft.skipped&&<Typography.Text type="secondary">切图会保存跳过状态。按 R 可同时审核通过。</Typography.Text>}
      {suggestionsError&&<Alert type="warning" showIcon title={suggestionsError}/>}
      {!draft.skipped&&!!importedLabels.length&&<Space wrap><Typography.Text>整图标签</Typography.Text>
        {importedLabels.map(label=><Tag key={label}>{label}</Tag>)}</Space>}
      {imageFields}
      {attributeMode&&(draft.attribute_schema&&draft.attributes?<>
        <div className="image-annotation-attribute-heading">
          <Typography.Text strong>目标属性 · {Array.isArray(draft.attribute_schema.target_label)?draft.attribute_schema.target_label.join('、'):draft.attribute_schema.target_label}</Typography.Text>
          {current?.annotation_source==='auto'&&<Tag color="blue">模型预标注 · 请核对</Tag>}
          <Button size="small" aria-expanded={!attributesCollapsed} onClick={()=>setAttributesCollapsed(value=>!value)}>{attributesCollapsed?'展开属性':'收起属性'}</Button>
          {!attributesCollapsed&&<Button size="small" disabled={busy} onClick={onRefreshSuggestions}>更新候选值</Button>}
        </div>
        {!attributesCollapsed&&<AttributeEditor schema={draft.attribute_schema} values={draft.attributes} disabled={busy||!!draft.skipped} suggestions={suggestions} preannotations={preannotations} onChange={attributes=>setDraft({...draft,attributes})}/>}
      </>:!!project.label_schema?.object_attributes&&<Button disabled={busy||draft.skipped} onClick={()=>{
        const schema=project.label_schema!.object_attributes as AttributeSchema;
        setDraft({...draft,attribute_schema:schema,attributes:Object.fromEntries(schema.fields.map(field=>[field.key,{value:null,reason:'待人工核验'}]))});
        setAttributesCollapsed(false);
      }}>填写目标属性</Button>)}
      <details><summary>历史版本（{versions.length}）</summary>{versions.map(ann=><div key={ann.id}><Tag>v{ann.version}</Tag>{new Date(ann.created_at).toLocaleString('zh-CN')}<JsonView label={`标注历史 v${ann.version}`} value={ann.annotation_data}/></div>)}</details>
    </Space></section><div className="image-annotation-review-actions" aria-label="审核操作">
      {advanceOnReview&&<Button block type="primary" aria-label="保存并通过，下一张" aria-busy={busy} onClick={()=>void approveNext()} loading={busy} disabled={!canApproveNext} title={draft.skipped?'R：保存跳过状态、审核通过并进入下一张，不作负样本':'R：保存修改、人工通过并转到当前筛选的下一张'}>保存并通过，下一张</Button>}
      <Space wrap><Button type={classification||attributeMode?'default':'primary'} onClick={save} loading={busy} disabled={loading||busy||(!dirty&&hasImageAnnotation)}>保存标注</Button><Button disabled={loading||dirty||busy||!current||!hasImageAnnotation||annotationReviewStatus(current,'image')==='approved'} onClick={()=>review(true)}>审核通过</Button><Button danger disabled={loading||dirty||busy||!current||!hasImageAnnotation||annotationReviewStatus(current,'image')==='rejected'} onClick={()=>review(false)}>驳回</Button></Space>
      <div className="image-annotation-review-meta"><Tag>{current&&hasImageAnnotation?`v${current.version} · ${{pending:'待审核',approved:'已通过',rejected:'已驳回'}[annotationReviewStatus(current,'image')]}`:'尚未保存'}</Tag>{dirty&&<Typography.Text type="warning">有未保存修改</Typography.Text>}<details><summary>审核意见（可选）</summary><Input aria-label="审核意见" value={comment} maxLength={2000} placeholder="审核意见（可选）" onChange={e=>setComment(e.target.value)}/></details></div>
    </div></div></div>
    <div className="image-annotation-source" title={asset.original_name}>{asset.original_name} · {asset.width} × {asset.height}{asset.source_type==='model_crop'?' · 模型裁剪图':''}{!!asset.device_info?.crop&&<> · 裁剪框：<code>{(asset.device_info.crop as {region:number[]}).region.join(', ')}</code></>}</div>
    <Modal title="有未保存的标注" open={blocker.state==='blocked'&&!savingSkip} onCancel={()=>blocker.reset?.()} onOk={()=>blocker.proceed?.()} okText="放弃修改并离开" cancelText="继续编辑"><p>请先保存，或确认放弃当前修改。</p></Modal>
    <Modal title="标注快捷键" open={showShortcuts} onCancel={()=>setShowShortcuts(false)} footer={null}>
      <dl className="workspace-shortcuts">
        <div><dt>上一张 / 下一张</dt><dd><kbd>← / →</kbd> 或 <kbd>A / D</kbd></dd></div>
        <div><dt>保存标注</dt><dd><kbd>Ctrl / ⌘ + S</kbd></dd></div>
        {classification&&!multiple&&<div><dt>选择类别</dt><dd><kbd>1–9</kbd></dd></div>}
        <div><dt>{advanceOnReview?'保存并通过，下一张':'审核通过'}</dt><dd><kbd>R</kbd></dd></div>
        <div><dt>驳回并提交审核意见</dt><dd><kbd>Shift + R</kbd></dd></div>
      </dl>
      <p className="workspace-help">编辑文本时仅支持保存。勾选跳过后切图会自动保存，按 R 会同时审核通过。</p>
    </Modal>
  </Card>;
}

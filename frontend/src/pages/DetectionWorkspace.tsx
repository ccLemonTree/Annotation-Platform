import { useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Link, useNavigate, useParams, useBlocker, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Checkbox, Input, InputNumber, Select, Space, Tag, message, Modal, Radio } from 'antd';
import DetectionCanvas from '../components/DetectionCanvas';
import { AnnotationCropLabelSelect, AnnotationImageKindSelect, AnnotationSourceViewer, FullImageStatus, ImagePreviewError } from '../components/AnnotationImageViewer';
import useAnnotationImage from '../components/useAnnotationImage';
import type {ImageView} from '../components/useAnnotationImageView';
import useFittedImageColumn from '../components/useFittedImageColumn';
import { draftHistory, changeDraft, undoDraft, redoDraft, duplicatePairs, labelColor } from '../components/detectionEditor';
import type { Box, Draft } from '../components/detectionEditor';
import apiClient from '../api/client';
import { getAssetNeighbors, getAssets, getProjectAssetCollections, readAssetImageKind } from '../api/assets';
import type { Asset, AssetImageKind, AssetNeighbors } from '../api/assets';
import { addProjectLabels, getProject, getYoloMappingBatches, mapYoloClasses } from '../api/projects';
import type { Project, YoloMappingBatch } from '../api/projects';
import { createAnnotation, getAnnotations, annotationReviewStatus, reviewAnnotation, updateAnnotation, previewProjectLabelRemoval, removeProjectLabel } from '../api/annotations';
import type { Annotation, LabelRemovalPreview } from '../api/annotations';
import { rememberAnnotationProgress } from '../components/annotationProgress';
import ModelPrediction from '../components/ModelPrediction';
import WorkActivityTracker from '../components/WorkActivityTracker';
import {AnnotationFullscreenButton} from '../components/AnnotationStage';
import { useCurrentUser } from '../components/currentUser';

type Event = { id: string; action: string; user_id: number; created_at: string; detail: { version?: number;source?:string } };

export default function DetectionWorkspace({initial,cropLabels,imageView,setImageView}:{initial:{asset:Asset;project:Project};cropLabels:string[];imageView:ImageView;setImageView:(view:ImageView)=>void}) {
  const [notice, noticeContext] = message.useMessage();
  const { assetId = '' } = useParams();
  const navigate = useNavigate();
  const [params,setParams] = useSearchParams();
  const annotationLabel=params.get('label')||undefined;
  const cropLabel=useMemo(()=>params.getAll('crop_label'),[params]);
  const collectionId=params.get('collection_id')||undefined;
  const workJobId=params.get('work_job_id')||undefined;
  const reviewValue=params.get('review_status');
  const reviewStatus=['pending','approved','rejected'].includes(reviewValue||'')?reviewValue as Annotation['review_status']:undefined;
  const fromAutomationReview=params.get('from')==='automation-review';
  const fromReview = params.get('from') === 'review'||fromAutomationReview;
  const fromAnnotation = params.get('from') === 'annotation';
  const isAdmin = useCurrentUser()?.role === 'admin';
  const returnParams = new URLSearchParams(params);
  returnParams.delete('from');

  const [baseline, setBaseline] = useState<Draft>({ boxes: [], negative: false });
  const [asset, setAsset] = useState<Asset>();
  const [project, setProject] = useState<Project>();
  const imageKind=readAssetImageKind(params.get('image_kind'));
  const isCrop=asset?.source_type==='model_crop'&&!asset.device_info?.uploaded_crop;
  const showingSource=isCrop&&imageView==='source';
  const initialCrop=initial.asset.source_type==='model_crop'&&!initial.asset.device_info?.uploaded_crop;
  const imageState=useAnnotationImage(initial.asset.id===assetId&&!(initialCrop&&imageView==='source')?`/api/assets/${assetId}/content`:undefined);
  const image=imageState.src||'';
  const [history, setHistory] = useState<Annotation[]>([]);
  const [latest, setLatest] = useState<Annotation>();
  const hasDetection=Array.isArray(latest?.annotation_data.boxes);
  const [viewed, setViewed] = useState<Annotation>();
  const [events, setEvents] = useState<Event[]>([]);
  const [draft, setDraft] = useState(() => draftHistory(baseline));
  const { boxes, negative } = draft.present;
  const [selected, setSelected] = useState(-1);
  const [label, setLabel] = useState('');
  const [tool, setTool] = useState<'draw' | 'select'>('draw');
  const dirty = JSON.stringify(draft.present) !== JSON.stringify(baseline);
  const [drawing, setDrawing] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && (
    currentLocation.pathname !== nextLocation.pathname ||
    new URLSearchParams(currentLocation.search).get('kind') !== new URLSearchParams(nextLocation.search).get('kind')));
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const [error, setError] = useState('');
  const [labelsOpen,setLabelsOpen]=useState(false),[labelInput,setLabelInput]=useState('');
  const [labelsSaving,setLabelsSaving]=useState(false),[labelsError,setLabelsError]=useState('');
  const [yoloBatches,setYoloBatches]=useState<YoloMappingBatch[]>([]);
  const [yoloBatch,setYoloBatch]=useState<string|null>();
  const [batchesLoading,setBatchesLoading]=useState(false);
  const [removalOpen,setRemovalOpen]=useState(false),[removalLabel,setRemovalLabel]=useState('');
  const [removalPreview,setRemovalPreview]=useState<LabelRemovalPreview>(),[removalError,setRemovalError]=useState('');
  const [removalPhase,setRemovalPhase]=useState<'preview'|'delete'>();
  const [neighbors, setNeighbors] = useState<AssetNeighbors>();
  const [collections,setCollections]=useState<{id:string;name:string}[]>([]);
  const [collectionError,setCollectionError]=useState('');
  const [navigationError, setNavigationError] = useState('');
  const [navigationReload, setNavigationReload] = useState(0);
  const [jumpPosition, setJumpPosition] = useState<number | null>(null);
  const [jumping, setJumping] = useState(false);
  const jumpRequest = useRef(0);
  const loading=asset?.id!==assetId;
  const historical=!!viewed&&!viewed.is_latest;
  const readonly = loading||historical;
  const negativeReviewRestricted = latest?.annotation_data.boxes?.length === 0 && !isAdmin;
  const width = asset?.width || 1, height = asset?.height || 1;
  const canvasPanels=useFittedImageColumn();
  const declared = project?.label_schema?.labels;
  const labels: string[] = Array.isArray(declared) ? declared.filter((label): label is string => typeof label === 'string' && !!label.trim()) : [];
  const duplicates = duplicatePairs(boxes);
  const pendingCategories = boxes.some(box => box.yolo_class_id !== undefined);
  const invalidLabels = boxes.some(box => box.yolo_class_id === undefined && !labels.includes(box.label));
  const newLabels=labelInput.split(/[，,；;\n]+/).map(value=>value.trim()).filter(Boolean);
  const openLabels=async()=>{
    if(!project)return;
    setLabelInput('');setLabelsError('');setYoloBatch(undefined);setYoloBatches([]);setLabelsOpen(true);
    setBatchesLoading(true);
    try{setYoloBatches(await getYoloMappingBatches(project.id));}
    catch{setLabelsError('读取 YOLO 标签批次失败，请重新打开。');}
    finally{setBatchesLoading(false);}
  };
  const saveLabels=async()=>{
    if(!project||labelsSaving||!newLabels.length)return;
    setLabelsSaving(true);setLabelsError('');
    try{
      if(yoloBatch===undefined){
        setProject(await addProjectLabels(project.id,newLabels));
      }else{
        const result=await mapYoloClasses(project.id,yoloBatch,newLabels);
        await refresh().catch(()=>setError('类别已保存，当前图片读取失败，请重新加载。'));
        notice.success(`已设置 ${result.updated_images} 张图片的 ${result.updated_boxes} 个框`);
      }
      setLabel(newLabels[0]);setTool('draw');setImageView('crop');
      setLabelsOpen(false);
    }catch(cause){
      const detail=isAxiosError(cause)?cause.response?.data?.detail:undefined;
      setLabelsError(typeof detail==='string'?detail:'保存类别失败，请重试。');
    }finally{setLabelsSaving(false);}
  };

  const accept = (annotation?: Annotation) => {
    setViewed(annotation);
    const initial = { boxes: annotation?.annotation_data.boxes?.map((box: Box) => ({ ...box })) || [],
      negative: annotation?.annotation_data.boxes?.length === 0 };
    setBaseline(initial); setDraft(draftHistory(initial)); setSelected(-1);
  };
  const refresh = async () => {
    if (!asset||loading) return;
    const [records, currentProject] = await Promise.all([
      getAnnotations({ asset_id: assetId, latest_only: false, page_size: 100 }), getProject(asset.project_id),
    ]);
    setProject(currentProject);
    const configured = Array.isArray(currentProject.label_schema?.labels) ? currentProject.label_schema.labels as string[] : [];
    setLabel(previous => configured.includes(previous) ? previous : configured[0] || '');
    const current = records.items.find(a => a.is_latest);
    setHistory(records.items); setLatest(current); accept(current);
    setEvents((await apiClient.get(`/api/annotations/assets/${assetId}/events`)).data);
    setNavigationReload(value=>value+1);
  };
  useEffect(() => {
    if(initial.asset.id!==assetId)return;
    let active = true;
    setError(''); setDrawing(false);setRemovalOpen(false);setRemovalPreview(undefined);
    (async () => {
      const {asset:item,project:p}=initial;
      const [records, audit] = await Promise.all([
        getAnnotations({ asset_id: assetId, latest_only: false, page_size: 100 }), apiClient.get(`/api/annotations/assets/${assetId}/events`),
      ]);
      if (!active) return;
      if(p.label_schema?.annotation_scope==='crops'&&item.source_type!=='model_crop'){setError('此项目仅标注裁剪图，请从项目裁剪图继续');return;}
      setAsset(item); setProject(p);
      const configured = Array.isArray(p.label_schema?.labels) ? p.label_schema.labels as string[] : [];
      setLabel(previous => configured.includes(previous) ? previous : configured[0] || '');
      const current = records.items.find(a => a.is_latest);
      setHistory(records.items); setLatest(current); accept(current); setEvents(audit.data);
    })().catch(() => { if (active) setError('工作台读取失败，请刷新或检查图片'); });
    return () => { active = false; };
  }, [assetId,initial]);
  useEffect(() => {
    if(!asset?.project_id)return;
    let active=true;setCollectionError('');
    getProjectAssetCollections(asset.project_id,true).then(result=>{if(active)setCollections(result);})
      .catch(()=>{if(active)setCollectionError('图片集读取失败，请重试');});
    return()=>{active=false;};
  },[asset?.project_id,navigationReload]);
  useEffect(() => {
    let active = true;
    setNeighbors(undefined); setNavigationError(''); setJumpPosition(null);
    getAssetNeighbors(assetId,annotationLabel,reviewStatus,collectionId,{work_job_id:workJobId,image_kind:imageKind,crop_label:cropLabel,annotation_kind:'detection',ai_pending:params.get('ai_pending')==='1'||undefined}).then(result => { if (active) { setNeighbors(result); setJumpPosition(result.matched===false?null:result.position); } })
      .catch(() => { if (active) setNavigationError(reviewStatus?'审核筛选队列读取失败，请重试并确认后台支持审核状态筛选':'图片顺序读取失败，请重试'); });
    return () => { active = false; };
  }, [assetId, annotationLabel, cropLabel, reviewStatus, collectionId, workJobId, imageKind, navigationReload]);
  useEffect(() => {
    if (asset && !rememberAnnotationProgress(asset)) notice.warning('浏览器存储不可用，无法记住本次标注位置');
  }, [asset, notice]);
  useEffect(() => {
    return () => { jumpRequest.current++; };
  }, [assetId]);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty]);

  const change = (next: Draft) => {
    if (readonly || busy || drawing) return;
    setDraft(previous => changeDraft(previous, next));
  };
  const edit = (key: keyof Box, value: number | string | null) => {
    if (value === null || selected < 0) return;
    const edited = {...boxes[selected], [key]: value};
    if (key === 'label') delete edited.yolo_class_id;
    change({ ...draft.present, boxes: boxes.map((b, i) => i === selected ? edited : b) });
  };
  const removeBox = () => {
    if (selected < 0 || readonly || busy || drawing) return;
    change({ boxes: boxes.filter((_, i) => i !== selected), negative: false });
    setSelected(Math.min(selected, boxes.length - 2));
  };
  const undo = () => { if (!readonly && !busy && !drawing) { setDraft(undoDraft); setSelected(-1); } };
  const redo = () => { if (!readonly && !busy && !drawing) { setDraft(redoDraft); setSelected(-1); } };
  const markNegative = () => {
    if (!asset || readonly || busy || drawing || jumping || !labels.length) return;
    change({ boxes: [], negative: true }); setSelected(-1);
  };
  async function save() {
    if (!asset || !labels.length || invalidLabels || readonly || busy || drawing || saving.current) {
      setError('无法自动保存：请检查项目类别或当前标注状态'); return false;
    }
    if (!boxes.length && !negative) { setError('无框时请先确认此图没有项目中的目标，自动保存未完成'); return false; }
    saving.current = true; setBusy(true); setError('');
    try {
      const data = {boxes:boxes.map(b=>({...b})),negative};
      if (latest) await updateAnnotation(latest.id, { kind:'detection', annotation_data: data, base_version: latest.version });
      else await createAnnotation({ asset_id: assetId, kind:'detection', annotation_data: data, base_version: 0 });
      await refresh(); notice.success('标注已保存，等待审核'); return true;
    } catch { setError('保存失败；若版本冲突，请先记录当前修改，再重新加载最新版本'); return false; }
    finally { saving.current = false; setBusy(false); }
  }
  // Run once for each blocked navigation; draft/progress renders must not resubmit.
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (blocker.location.pathname.startsWith('/annotate/')) {
      void save().then(success => { if (success) blocker.proceed(); else blocker.reset(); });
    } else if (window.confirm('还有未保存标注，确定离开吗？')) blocker.proceed();
    else blocker.reset();
  }, [blocker.state]);

  const review = async (approved: boolean) => {
    if (!latest || !hasDetection || approved&&pendingCategories || dirty || readonly || negativeReviewRestricted || busy || drawing || jumping || saving.current || ((annotationLabel||reviewStatus||collectionId)&&!neighbors)) return;
    saving.current=true;setBusy(true);setError('');
    let reviewed=false;
    try {
      await reviewAnnotation(latest.id, { kind:'detection', approved, review_comment: approved ? '人工核对通过' : '需要修改' });
      reviewed=true;
      if (approved) {
        try {
          const queue=await getAssetNeighbors(assetId,annotationLabel,reviewStatus,collectionId,{work_job_id:workJobId,image_kind:imageKind,crop_label:cropLabel,annotation_kind:'detection',ai_pending:params.get('ai_pending')==='1'||undefined});
          if (queue.next_id) { go(queue.next_id); return; }
          notice.info('审核已通过，当前筛选没有下一张图片');
        } catch { notice.warning('审核已通过，但下一张读取失败，请手动切图或重新加载'); }
      }
      await refresh();
    }
    catch { setError(reviewed?'审核已完成，但页面刷新失败，请重新加载最新版本':'审核失败，请重试或重新加载最新版本'); }
    finally { saving.current=false;setBusy(false); }
  };
  const previewRemoval=async()=>{
    if(!project||!removalLabel||saving.current||dirty)return;
    saving.current=true;setBusy(true);setRemovalPhase('preview');setRemovalPreview(undefined);setRemovalError('');
    try{setRemovalPreview(await previewProjectLabelRemoval(project.id,removalLabel));}
    catch(cause){setRemovalError(isAxiosError(cause)&&typeof cause.response?.data?.detail==='string'?cause.response.data.detail:'预览读取失败，请重试');}
    finally{saving.current=false;setBusy(false);setRemovalPhase(undefined);}
  };
  const applyRemoval=async()=>{
    if(!project||!removalPreview||removalPreview.project_id!==project.id||!removalPreview.affected_images||saving.current||dirty)return;
    saving.current=true;setBusy(true);setRemovalPhase('delete');setRemovalError('');let applied=false;
    try{
      const result=await removeProjectLabel(project.id,removalPreview);applied=true;
      setRemovalOpen(false);setRemovalPreview(undefined);
      if(annotationLabel===result.label){const query=new URLSearchParams(params);query.delete('label');setParams(query);}
      await refresh();
      notice.success(`已从 ${result.affected_images} 张图片删除 ${result.removed_boxes} 个 ${result.label} 框，受影响标注已重新待审核`);
    }catch(cause){
      setRemovalPreview(undefined);
      if(applied){setRemovalOpen(false);setError('批量删除已完成，但工作台刷新失败，请重新加载最新版本');}
      else setRemovalError(isAxiosError(cause)&&typeof cause.response?.data?.detail==='string'?cause.response.data.detail:'执行结果尚未确认，请重新预览核对后再操作');
    }finally{saving.current=false;setBusy(false);setRemovalPhase(undefined);}
  };
  if (asset) returnParams.set('project_id', asset.project_id);
  const go = (id: string,query=params) => navigate(`/annotate/${id}${query.size ? `?${query.toString()}` : ''}`);
  const filterQueue=async(nextLabel?:string,nextReview?:Annotation['review_status'],nextCollection?:string,nextImageKind:AssetImageKind=imageKind,nextCropLabel=cropLabel)=>{
    if(!asset||loading||dirty||busy||drawing||jumping)return;
    const query=new URLSearchParams(params);
    const cropCategory=nextImageKind==='originals'?[]:nextCropLabel;
    if(nextLabel!==annotationLabel||nextReview!==reviewStatus||nextCollection!==collectionId||nextImageKind!==imageKind||cropCategory!==cropLabel)query.delete('page');
    if(nextLabel)query.set('label',nextLabel);else query.delete('label');
    query.delete('crop_label');
    cropCategory.forEach(label=>query.append('crop_label',label));
    if(nextReview)query.set('review_status',nextReview);else query.delete('review_status');
    if(nextReview!=='pending')query.delete('ai_pending');
    if(nextCollection)query.set('collection_id',nextCollection);else query.delete('collection_id');
    if(nextImageKind==='all')query.delete('image_kind');else query.set('image_kind',nextImageKind);
    if(!nextLabel&&!cropCategory.length&&!nextReview&&!nextCollection&&nextImageKind==='all'){setParams(query);return;}
    const request=++jumpRequest.current;setJumping(true);
    try{
      const result=await getAssets({project_id:asset.project_id,work_job_id:workJobId,scope:'annotation', annotation_kind:'detection',ai_pending:query.get('ai_pending')==='1'||undefined,image_kind:nextImageKind,label:nextLabel,crop_label:cropCategory,review_status:nextReview,collection_id:nextCollection,page:1,page_size:1});
      if(request!==jumpRequest.current)return;
      if(!result.items[0]){notice.info(nextCollection?'当前图片集与其他筛选条件没有匹配图片':'当前筛选没有匹配图片');return;}
      go(result.items[0].id,query);
    }catch{if(request===jumpRequest.current)notice.error('筛选失败，当前图片已保留，请重试并确认后台支持所选筛选');}
    finally{setJumping(false);}
  };
  const jump = async () => {
    if (!asset || loading || !neighbors || busy || drawing || jumping) return;
    if (jumpPosition === null || !Number.isInteger(jumpPosition) || jumpPosition < 1 || jumpPosition > neighbors.total) {
      notice.warning(`请输入1到${neighbors.total}之间的整数`); return;
    }
    if (jumpPosition === neighbors.position) return;
    const request = ++jumpRequest.current;
    setJumpPosition(neighbors.position);
    setJumping(true);
    try {
      const result = await getAssets({ project_id: asset.project_id, work_job_id:workJobId, scope: 'annotation', annotation_kind:'detection', ai_pending:params.get('ai_pending')==='1'||undefined, image_kind:imageKind,label:annotationLabel,crop_label:cropLabel,review_status:reviewStatus,collection_id:collectionId,page: jumpPosition, page_size: 1 });
      if (request !== jumpRequest.current) return;
      if (!result.items[0]) { notice.warning('此序号已不存在，请重试切图读取'); return; }
      go(result.items[0].id);
    } catch { if (request === jumpRequest.current) notice.error('跳转失败，当前图片与草稿已保留，请重试'); }
    finally { setJumping(false); }
  };
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      const target = event.target as Element | null;
      if (event.repeat || event.isComposing || loading || drawing || busy || jumping || showShortcuts || removalOpen || labelsOpen || target?.closest('input, textarea, select, [contenteditable="true"], [role="combobox"], [role="dialog"], .ant-select, .ant-modal-wrap, .ant-drawer')) return;
      const key = event.key.toLowerCase(), modified = event.ctrlKey || event.metaKey;
      if (modified && key === 's') { event.preventDefault(); if (dirty) void save(); }
      else if (modified && key === 'z') { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
      else if (modified && key === 'y') { event.preventDefault(); redo(); }
      else if (!modified && !event.altKey) {
        if (key === 'delete' || key === 'backspace') { event.preventDefault(); removeBox(); }
        else if (key === 'b') setTool('draw');
        else if (key === 'v') setTool('select');
        else if (key === 'n') { event.preventDefault(); markNegative(); }
        else if (key === 'r') { event.preventDefault(); void review(true); }
        else if (key === 'a' || key === 'arrowleft') { event.preventDefault(); if (asset && neighbors?.previous_id) go(neighbors.previous_id); }
        else if (key === 'd' || key === 'arrowright') { event.preventDefault(); if (asset && neighbors?.next_id) go(neighbors.next_id); }
        else if (/^[1-9]$/.test(key) && !readonly && labels[Number(key) - 1]) {
          const next = labels[Number(key) - 1]; setLabel(next); if (selected >= 0) edit('label', next);
        }
        else if (key === 'escape') setSelected(-1);
      }
    };
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  });
  const viewSwitcher=isCrop&&<Radio.Group aria-label="图片视图" value={imageView} disabled={drawing} onChange={event=>setImageView(event.target.value)}>
    <Radio.Button value="crop">标注切图</Radio.Button><Radio.Button value="source">来源原图</Radio.Button>
  </Radio.Group>;
  return <Card className="detection-workspace" title={project ? `${project.project_name} · ${asset?.original_name || ''}` : '检测标注工作台'}
    extra={asset&&<Space wrap><Link to={fromAutomationReview?`/review-queue?project_id=${asset.project_id}`:(fromReview || fromAnnotation) ? `/annotations?${returnParams.toString()}` : isAdmin ? `/projects/${asset.project_id}/images${workJobId?`?work_job_id=${workJobId}`:''}` : `/annotations?project_id=${asset.project_id}`}>{fromAutomationReview?'返回管理员待审队列':`返回 ${project?.project_name || '当前项目'} ${fromAnnotation ? '人工标注' : fromReview || !isAdmin ? '标注与审核' : '项目图片'}`}</Link><AnnotationFullscreenButton/></Space>}>
    {noticeContext}
    {asset && <WorkActivityTracker key={asset.id} projectId={asset.project_id} assetId={asset.id}
      enabled={!!image && !error && !readonly && !busy && !jumping && !removalOpen && !!labels.length}
      initialActivity={fromReview ? 'review' : 'annotation'} />}
    <div className="workspace-alerts">
    {(!labels.length || pendingCategories) && project && <Alert type="warning" showIcon title={pendingCategories?(labels.length?'请在框列表中为导入的框设置类别。':isAdmin?'请先添加类别，再为导入的框设置类别。':'请联系管理员添加类别，再为导入的框设置类别。'):isAdmin?'添加标注类别后即可画框。':'此项目尚未配置标注类别，请联系管理员设置。'}
      action={isAdmin&&<Button disabled={readonly||busy||drawing||dirty} onClick={()=>void openLabels()}>设置类别</Button>}
      style={{ marginBottom: 12 }} />}
    {invalidLabels && <Alert type="warning" title="部分框的类别已不在项目配置中，请修改或删除这些框后保存；历史版本和冻结包不会自动改名。" style={{ marginBottom: 12 }} />}
    {duplicates.length > 0 && <Alert type="warning" showIcon title={`可能重复的同类框：${duplicates.map(([a, b]) => `${a + 1} / ${b + 1}`).join('、')}。真实遮挡允许重叠，请自行核对，不会自动删框。`} style={{ marginBottom: 12 }} />}
    {error && <Alert type="error" title={error} style={{ marginBottom: 12 }} />}
    {negativeReviewRestricted && <Alert type="info" title="负样本须由管理员最终审核，当前账号无法通过或驳回。" style={{ marginBottom: 12 }} />}
    {navigationError && <Alert type="error" title={navigationError} style={{ marginBottom: 12 }}
      action={<Button onClick={() => setNavigationReload(value => value + 1)}>重试切图读取</Button>} />}
    {collectionError&&<Alert type="error" title={collectionError} style={{marginBottom:12}}
      action={<Button onClick={()=>setNavigationReload(value=>value+1)}>重试图片集读取</Button>}/>}
    {neighbors?.matched===false&&<Alert type="info" style={{marginBottom:12}} title={neighbors.total?'当前图片已不符合筛选，上一张 / 下一张仍在筛选结果中切换':'当前筛选已无匹配图片，可清除筛选查看全部'}
      action={<Button disabled={dirty||busy||drawing||jumping} onClick={()=>void filterQueue(undefined,undefined,undefined,'all',[])}>清除筛选</Button>}/>}
    </div>
    <Space className="workspace-toolbar" wrap style={{ marginBottom: 12 }}>
      <AnnotationImageKindSelect value={imageKind} cropsOnly={project?.label_schema?.annotation_scope==='crops'}
        disabled={!asset||loading||dirty||busy||drawing||jumping} onChange={value=>void filterQueue(annotationLabel,reviewStatus,collectionId,value)}/>
      <AnnotationCropLabelSelect labels={cropLabels} value={cropLabel} disabled={!asset||loading||dirty||busy||drawing||jumping}
        onChange={value=>void filterQueue(annotationLabel,reviewStatus,collectionId,value.length&&imageKind==='originals'?'crops':imageKind,value)}/>
      <Select aria-label="筛选图片集" placeholder="全部图片集" allowClear showSearch optionFilterProp="label" value={collectionId}
        onChange={value=>void filterQueue(annotationLabel,reviewStatus,value)} options={collections.map(group=>({value:group.id,label:group.name}))} style={{minWidth:170}}
        disabled={!asset||loading||dirty||busy||drawing||jumping} title={dirty?'请先保存修改再筛选':'按当前项目关联的图片集筛选图片'}/>
      <Select aria-label="筛选标注标签" prefix="筛选：" placeholder="全部标签" allowClear showSearch optionFilterProp="label" value={annotationLabel}
        onChange={value=>void filterQueue(value,reviewStatus,collectionId)} options={labels.map(label=>({value:label,label}))} style={{minWidth:160}}
        disabled={!asset||loading||dirty||busy||drawing||jumping} title={dirty?'请先保存修改再筛选':'按最新已保存标注中的框类别筛选图片'}/>
      <Select<Annotation['review_status']|''> aria-label="筛选审核状态" prefix="审核：" value={reviewStatus||''} style={{minWidth:170}} title="按最新已保存标注的审核状态筛选"
        options={[{value:'',label:'全部状态'},{value:'pending',label:'待审核'},{value:'approved',label:'已通过'},{value:'rejected',label:'已驳回'}]}
        onChange={value=>void filterQueue(annotationLabel,value||undefined,collectionId)} disabled={!asset||loading||dirty||busy||drawing||jumping}/>
      <Button disabled={!asset || loading || !neighbors?.previous_id || busy || drawing || jumping} onClick={() => neighbors?.previous_id && go(neighbors.previous_id)}>上一张</Button>
      {neighbors ? <Space size={6}>
        <InputNumber aria-label="跳转图片序号" min={1} max={neighbors.total||1} precision={0} placeholder={neighbors.matched===false?'未匹配':''}
          value={jumpPosition} onChange={setJumpPosition} onPressEnter={() => void jump()}
          disabled={!asset || loading || busy || drawing || jumping||!neighbors.total} style={{ width: 88 }} title="输入图片序号，按回车跳转" />
        <span>/ {neighbors.total}</span>
      </Space> : <span>{navigationError ? '位置读取失败' : '定位中…'}</span>}
      <Button disabled={!asset || loading || !neighbors?.next_id || busy || drawing || jumping} onClick={() => neighbors?.next_id && go(neighbors.next_id)}>下一张</Button>
      <Select aria-label="绘图工具" value={tool} onChange={setTool} options={[{ value: 'draw', label: '绘制矩形' }, { value: 'select', label: '选择 / 移动' }]} />
      <Select aria-label="框类别" value={label} onChange={setLabel} options={labels.map(l => ({ value: l, label: l }))} />
      {isAdmin&&!!labels.length&&!pendingCategories&&<Button disabled={!project||readonly||busy||drawing||dirty} onClick={()=>void openLabels()}>设置类别</Button>}
      <Button type="primary" loading={busy} disabled={!asset || readonly || !dirty || drawing || !labels.length || invalidLabels} onClick={save}>保存标注</Button>
      <Button disabled={busy || drawing || !asset || loading} onClick={() => { if (!dirty || window.confirm('放弃未保存修改并重新加载吗？')) refresh(); }}>重新加载</Button>
      {isAdmin&&<Button danger disabled={!project||readonly||dirty||busy||drawing||jumping||!labels.length} title={dirty?'请先保存当前修改':'批量删除当前项目全部标注图片中的指定类别框'} onClick={()=>{
        setRemovalLabel(annotationLabel||(labels.includes('person')?'person':label||labels[0]||''));setRemovalPreview(undefined);setRemovalError('');setRemovalOpen(true);
      }}>批量删除类别框</Button>}
      <Button aria-label="审核通过" aria-keyshortcuts="R" disabled={!hasDetection || pendingCategories || dirty || readonly || busy || drawing || jumping || negativeReviewRestricted || !!(annotationLabel||reviewStatus||collectionId)&&!neighbors} onClick={() => review(true)}>审核通过（R）</Button>
      <Button danger disabled={!hasDetection || dirty || readonly || busy || drawing || negativeReviewRestricted} onClick={() => review(false)}>驳回</Button>
      <Button aria-label="撤销标注操作" disabled={!draft.past.length || readonly || busy || drawing} onClick={undo}>撤销</Button>
      <Button aria-label="重做标注操作" disabled={!draft.future.length || readonly || busy || drawing} onClick={redo}>重做</Button>
      <Button onClick={() => setShowShortcuts(true)}>快捷键</Button>
      <Tag>{dirty ? '未保存' : latest&&hasDetection ? `v${latest.version} · ${{pending:'待审核',approved:'已通过',rejected:'已驳回'}[annotationReviewStatus(latest,'detection')]}` : '待标注'}</Tag>
    </Space>
    <p className="workspace-help">滚轮缩放；Ctrl/空格拖动平移。Shift拖动强制画框；重叠处默认选小框，Alt点击循环选择，也可用右侧框列表。坐标按当前标注图片像素保存。{historical && '当前为历史版本，只读。'}</p>
    <div className="workspace-grid" ref={canvasPanels}>
      <div className="workspace-image-column">
        <div style={{position:'relative',display:'flex',flexDirection:'column',flex:1,minHeight:0}} aria-busy={loading}>
        {(loading||!showingSource&&!image&&!imageState.previewError)&&<div role="status" style={{position:'absolute',top:12,right:12,zIndex:1,padding:'6px 12px',background:'var(--ui-surface)',borderRadius:6}}>正在加载图片…</div>}
        {showingSource&&asset?<AnnotationSourceViewer key={asset.id} asset={asset} toolbarExtra={viewSwitcher}/>:image&&asset?.id===assetId?<DetectionCanvas key={asset.id} image={image} width={width} height={height} boxes={boxes} labels={labels}
          toolbarExtra={<>{viewSwitcher}<FullImageStatus image={imageState}/></>} onZoomIn={imageState.loadFull}
          selected={selected} label={label} tool={tool} disabled={readonly || busy || !labels.length}
          onSelect={setSelected} onDragging={setDrawing}
          onChange={next => {if(!readonly)setDraft(previous => changeDraft(previous, { boxes: next, negative: false }));}} />:imageState.previewError&&<>{viewSwitcher}<ImagePreviewError image={imageState}/></>}
        </div>
        <Space wrap><Button onClick={markNegative} disabled={!asset || readonly || busy || drawing || jumping || !labels.length || negative}
          title="清空当前框并标记负样本，可用Ctrl/Cmd+Z撤销">标记负样本（N）</Button>
        <Checkbox disabled={readonly || !!boxes.length || busy || drawing} checked={negative}
          onChange={e => change({ boxes, negative: e.target.checked })}>此图没有项目中的目标（负样本）</Checkbox>
        </Space>
      </div>
      <div className="workspace-inspector">
        {isAdmin && asset && <ModelPrediction key={asset.id} assetId={asset.id} projectId={asset.project_id} labels={labels} disabled={readonly || busy || drawing || jumping}
          onApply={(next, updated) => { if(updated){setProject(updated);if(!label)setLabel(next[0].label);} change({ boxes: next, negative: false }); setSelected(-1); }} />}
        <h3>框列表（{boxes.length}）</h3>
        <Button disabled={!asset||readonly||busy||drawing||jumping||showingSource||!label||!labels.length} onClick={()=>{
          change({...draft.present,boxes:[...boxes,{x:0,y:0,width:Math.min(100,width),height:Math.min(100,height),label}],negative:false});
          setSelected(boxes.length);requestAnimationFrame(()=>document.getElementById('box-coordinate-x')?.focus());
        }}>新增框</Button><p className="workspace-help">新增框后可输入坐标和尺寸。</p>
        {boxes.map((b, i) => <Button key={i} block style={{ marginBottom: 6 }} type={i === selected ? 'primary' : 'default'} onClick={() => setSelected(i)}><span aria-hidden="true" style={{display:'inline-block',width:12,height:12,background:labelColor(b.label, labels),border:'1px solid #333',marginRight:8}}/>{i + 1} · {b.label}</Button>)}
        {selected >= 0 && boxes[selected] && <Space orientation="vertical">
          <Select aria-label="选中框类别" disabled={readonly} value={boxes[selected].yolo_class_id===undefined?boxes[selected].label:undefined} placeholder={boxes[selected].label} onChange={v => edit('label', v)} options={[...new Set([...labels, ...(boxes[selected].yolo_class_id===undefined?[boxes[selected].label]:[])])].map(l => ({ value: l, label: l, disabled: !labels.includes(l) }))} />
          {(['x', 'y', 'width', 'height'] as const).map(k => <label key={k}>{k}: <InputNumber id={k==='x'?'box-coordinate-x':undefined} aria-label={`框${k}`} disabled={readonly}
            value={boxes[selected][k]} min={k === 'x' || k === 'y' ? 0 : 1} onChange={v => edit(k, v)} /></label>)}
          <Button danger disabled={readonly} onClick={removeBox}>删除选中框</Button>
        </Space>}
        <h3>标注历史</h3>
        <Select disabled={busy || drawing} aria-label="标注历史版本" style={{ width: '100%' }} value={viewed?.id}
          onChange={id => { if (!dirty || window.confirm('放弃未保存修改并查看历史吗？')) accept(history.find(a => a.id === id)); }}
          options={history.map(a => ({ value: a.id, label: `v${a.version} · ${{pending:'待审核',approved:'已通过',rejected:'已驳回'}[annotationReviewStatus(a,'detection')]}${a.is_latest ? ' · 最新' : ''}` }))} />
        {historical && <Button disabled={loading} onClick={() => accept(latest)}>返回最新版本</Button>}
        <h3>操作记录</h3>
        <div style={{ maxHeight: 180, overflow: 'auto', fontSize: 12 }}>{events.map(e => <p key={e.id}>{e.detail.source==='bulk_label_delete'?'批量删除类别框':{create:'创建标注',update:'修改标注',approve:'审核通过',reject:'驳回',delete:'删除标注'}[e.action] || e.action} · 用户{e.user_id} · v{e.detail.version || '-'}<br />{new Date(e.created_at).toLocaleString()}</p>)}</div>
      </div>
    </div>
    <Modal title="设置标注类别" open={labelsOpen} okText={yoloBatch===undefined?'保存类别':'应用到所选批次'} cancelText="取消" confirmLoading={labelsSaving}
      okButtonProps={{disabled:batchesLoading||!newLabels.length||newLabels.length>100||newLabels.some(value=>value.length>100)}}
      cancelButtonProps={{disabled:labelsSaving}} closable={!labelsSaving} keyboard={!labelsSaving} mask={{closable:!labelsSaving}}
      onCancel={()=>{if(!labelsSaving)setLabelsOpen(false);}} onOk={()=>void saveLabels()}>
      <label htmlFor="new-project-labels">输入类别名称，多个类别用逗号或换行分隔。</label>
      <Input.TextArea id="new-project-labels" aria-label="标注类别" value={labelInput} disabled={labelsSaving}
        placeholder="例如：人、车（用逗号分隔）" autoSize={{minRows:2,maxRows:6}} onChange={event=>setLabelInput(event.target.value)}/>
      {(batchesLoading||yoloBatches.length>0)&&<Space orientation="vertical" style={{width:'100%',marginTop:12}}>
        <Select aria-label="YOLO 标签批次" style={{width:'100%'}} value={yoloBatch===undefined?'none':yoloBatch??'legacy'}
          loading={batchesLoading} disabled={batchesLoading||labelsSaving}
          onChange={value=>setYoloBatch(value==='none'?undefined:value==='legacy'?null:value)}
          options={[{value:'none',label:'仅添加项目类别'},...yoloBatches.map(batch=>({value:batch.batch_id??'legacy',
            label:`${batch.batch_id?`${new Date(batch.latest_at).toLocaleString()} · ${batch.batch_id.slice(0,8)}`:'旧版上传（无批次号）'} · ${batch.images} 张待设置图片`}))]}/>
        {yoloBatch!==undefined&&<><span>按输入顺序从 YOLO 编号 0 开始，仅设置所选批次里的待设置框。</span>
          {newLabels.length>0&&<div style={{maxHeight:120,overflowY:'auto'}}>{newLabels.map((name,index)=><Tag key={index}>{index} → {name}</Tag>)}</div>}</>}
      </Space>}
      {labelsError&&<Alert type="error" showIcon title={labelsError}/>}
    </Modal>
    <Modal title="批量删除项目类别框" open={removalOpen} okText="确认删除" cancelText="取消" confirmLoading={removalPhase==='delete'}
      style={{top:24}} styles={{body:{maxHeight:'calc(100dvh - 180px)',overflowY:'auto'}}}
      okButtonProps={{danger:true,disabled:busy||!removalPreview?.affected_images}} cancelButtonProps={{disabled:busy}} closable={!busy} maskClosable={!busy} keyboard={!busy}
      onCancel={()=>{if(!busy)setRemovalOpen(false);}} onOk={()=>void applyRemoval()}>
      <Alert type="warning" showIcon title={`范围：${project?.project_name} · ${project?.label_schema?.annotation_scope==='crops'?'全部参与标注的裁剪图':'全部标注图片'}`} description="按类别名称精确删除最新已保存标注中的框，忽略工作台当前图片筛选。"/>
      <Space wrap style={{marginTop:16}}><Select aria-label="待删除框类别" value={removalLabel} options={labels.map(value=>({value,label:value}))} style={{minWidth:180}} disabled={busy}
        onChange={value=>{setRemovalLabel(value);setRemovalPreview(undefined);setRemovalError('');}}/><Button disabled={busy||!removalLabel} loading={removalPhase==='preview'} onClick={()=>void previewRemoval()}>预览影响</Button></Space>
      {removalPreview&&<><p>将从 <strong>{removalPreview.affected_images}</strong> 张图片中删除 <strong>{removalPreview.removed_boxes}</strong> 个 <strong>{removalPreview.label}</strong> 框。</p>
        <p>其中 {removalPreview.empty_images} 张将变为空标注（负样本）；{removalPreview.reviewed_images} 张当前已通过审核。所有受影响图片会建立新版本并重新待审核，负样本须由管理员审核。</p>
        {!removalPreview.affected_images&&<Alert type="info" title="当前项目没有此类别的框，无需删除"/>}</>}
      <p>图片、其他类别框、历史版本及冻结导出包保留；项目类别配置和图库原始导入标注不变。</p>
      {removalError&&<Alert type="error" showIcon title={removalError}/>}
    </Modal>
    <Modal title="标注快捷键" open={showShortcuts} onCancel={() => setShowShortcuts(false)} footer={null}>
      <p>Ctrl/Cmd+S 保存；Ctrl/Cmd+Z 撤销；Ctrl/Cmd+Shift+Z 或 Ctrl+Y 重做。</p>
      <p>Delete/Backspace 删除选中框；B 画框；V 选择；1–9 选择项目类别并修改选中框类别。</p>
      <p>R 审核通过后自动进入当前筛选的下一张，末张会提示；须为最新已保存版本，负样本仍需管理员审核。标签筛选按最新已保存标注生效，前后切图和序号跳转保持该范围。</p>
      <p>N 清空当前图片的全部框并标记负样本，可撤销；手动保存或切图时保存。在上一张与下一张之间输入图片序号，回车跳转，有修改时先自动保存。</p>
      <p>A/D 或左右方向键切换图片（先自动保存，失败不切换）；Tab/Shift+Tab 在聚焦的画布中选择下一个/上一个框，到边缘后继续按可离开画布；Esc 取消拖动或选择。</p>
      <p>滚轮缩放；Ctrl/空格拖动平移；Shift 拖动在已有框上强制画框；Alt 点击循环选择重叠框。</p>
      <p>快捷键不打断输入框、类别选择和弹窗。本地撤销最多保留100步；保存/重新加载/查看历史/切换图片后清空。撤销不直接删除服务器历史。</p>
    </Modal>
  </Card>;
}

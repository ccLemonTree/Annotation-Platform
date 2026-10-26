import { useEffect, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Link, useNavigate, useParams, useBlocker, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Checkbox, InputNumber, Select, Space, Tag, message, Modal } from 'antd';
import DetectionCanvas from '../components/DetectionCanvas';
import { draftHistory, changeDraft, undoDraft, redoDraft, duplicatePairs } from '../components/detectionEditor';
import type { Box, Draft } from '../components/detectionEditor';
import apiClient from '../api/client';
import { getAsset, getAssetNeighbors, getAssets } from '../api/assets';
import type { Asset, AssetNeighbors } from '../api/assets';
import { getProject } from '../api/projects';
import type { Project } from '../api/projects';
import { createAnnotation, getAnnotations, reviewAnnotation, updateAnnotation, previewProjectLabelRemoval, removeProjectLabel } from '../api/annotations';
import type { Annotation, LabelRemovalPreview } from '../api/annotations';
import { rememberAnnotationProgress } from '../components/annotationProgress';
import ModelPrediction from '../components/ModelPrediction';
import WorkActivityTracker from '../components/WorkActivityTracker';
import { useCurrentUser } from '../components/currentUser';

type Event = { id: string; action: string; user_id: number; created_at: string; detail: { version?: number;source?:string } };

export default function DetectionWorkspace() {
  const [notice, noticeContext] = message.useMessage();
  const { assetId = '' } = useParams();
  const navigate = useNavigate();
  const [params,setParams] = useSearchParams();
  const annotationLabel=params.get('label')||undefined;
  const reviewValue=params.get('review_status');
  const reviewStatus=['pending','approved','rejected'].includes(reviewValue||'')?reviewValue as Annotation['review_status']:undefined;
  const fromReview = params.get('from') === 'review';
  const isAdmin = useCurrentUser()?.role === 'admin';
  const returnParams = new URLSearchParams(params);
  returnParams.delete('from');

  const [baseline, setBaseline] = useState<Draft>({ boxes: [], negative: false });
  const [asset, setAsset] = useState<Asset>();
  const [project, setProject] = useState<Project>();
  const [image, setImage] = useState('');
  const displayedImage=useRef('');
  const [history, setHistory] = useState<Annotation[]>([]);
  const [latest, setLatest] = useState<Annotation>();
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
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const [error, setError] = useState('');
  const [removalOpen,setRemovalOpen]=useState(false),[removalLabel,setRemovalLabel]=useState('');
  const [removalPreview,setRemovalPreview]=useState<LabelRemovalPreview>(),[removalError,setRemovalError]=useState('');
  const [removalPhase,setRemovalPhase]=useState<'preview'|'delete'>();
  const [neighbors, setNeighbors] = useState<AssetNeighbors>();
  const [navigationError, setNavigationError] = useState('');
  const [navigationReload, setNavigationReload] = useState(0);
  const [jumpPosition, setJumpPosition] = useState<number | null>(null);
  const [jumping, setJumping] = useState(false);
  const jumpRequest = useRef(0);
  const loading=asset?.id!==assetId;
  const historical=!!viewed&&!viewed.is_latest;
  const readonly = loading||historical;
  const negativeReviewRestricted = !!latest && latest.annotation_data.boxes.length === 0 && !isAdmin;
  const width = asset?.width || 1, height = asset?.height || 1;
  const declared = project?.label_schema?.labels;
  const labels: string[] = Array.isArray(declared) ? declared.filter((label): label is string => typeof label === 'string' && !!label.trim()) : [];
  const duplicates = duplicatePairs(boxes);
  const invalidLabels = boxes.some(box => !labels.includes(box.label));

  const accept = (annotation?: Annotation) => {
    setViewed(annotation);
    const initial = { boxes: annotation?.annotation_data.boxes.map((box: Box) => ({ ...box })) || [],
      negative: !!annotation && annotation.annotation_data.boxes.length === 0 };
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
    let active = true, objectUrl = '';
    setError(''); setDrawing(false);setRemovalOpen(false);setRemovalPreview(undefined);
    (async () => {
      const item = await getAsset(assetId);
      const [p, data, records, audit] = await Promise.all([
        getProject(item.project_id), apiClient.get(`/api/assets/${assetId}/content`, { responseType: 'blob' }),
        getAnnotations({ asset_id: assetId, latest_only: false, page_size: 100 }), apiClient.get(`/api/annotations/assets/${assetId}/events`),
      ]);
      if (!active) return;
      if (p.task_type !== 'detection') { setError('此工作台仅支持检测项目'); return; }
      if(p.label_schema?.annotation_scope==='crops'&&item.source_type!=='model_crop'){setError('此项目仅标注裁剪图，请从项目裁剪图继续');return;}
      objectUrl = URL.createObjectURL(data.data);
      const decoded=new Image();decoded.src=objectUrl;await decoded.decode();
      if(!active)return;
      displayedImage.current=objectUrl;
      setAsset(item); setProject(p); setImage(objectUrl);
      const configured = Array.isArray(p.label_schema?.labels) ? p.label_schema.labels as string[] : [];
      setLabel(previous => configured.includes(previous) ? previous : configured[0] || '');
      const current = records.items.find(a => a.is_latest);
      setHistory(records.items); setLatest(current); accept(current); setEvents(audit.data);
    })().catch(() => { if(objectUrl&&objectUrl!==displayedImage.current)URL.revokeObjectURL(objectUrl);if (active) setError('工作台读取失败，请刷新或检查图片'); });
    return () => { active = false; if (objectUrl&&objectUrl!==displayedImage.current) URL.revokeObjectURL(objectUrl); };
  }, [assetId]);
  useEffect(()=>()=>{if(image)URL.revokeObjectURL(image);},[image]);
  useEffect(() => {
    let active = true;
    setNeighbors(undefined); setNavigationError(''); setJumpPosition(null);
    getAssetNeighbors(assetId,annotationLabel,reviewStatus).then(result => { if (active) { setNeighbors(result); setJumpPosition(result.matched===false?null:result.position); } })
      .catch(() => { if (active) setNavigationError(reviewStatus?'审核筛选队列读取失败，请重试并确认后台支持审核状态筛选':'图片顺序读取失败，请重试'); });
    return () => { active = false; };
  }, [assetId, annotationLabel, reviewStatus, navigationReload]);
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
    change({ ...draft.present, boxes: boxes.map((b, i) => i === selected ? { ...b, [key]: value } : b) });
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
      const data = { boxes: boxes.map(b => ({ ...b })) };
      if (latest) await updateAnnotation(latest.id, { annotation_data: data, base_version: latest.version });
      else await createAnnotation({ asset_id: assetId, annotation_data: data, base_version: 0 });
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
    if (!latest || dirty || readonly || negativeReviewRestricted || busy || drawing || jumping || saving.current || ((annotationLabel||reviewStatus)&&!neighbors)) return;
    saving.current=true;setBusy(true);setError('');
    try { await reviewAnnotation(latest.id, { approved, review_comment: approved ? '人工核对通过' : '需要修改' }); await refresh(); }
    catch { setError('审核失败，请重试或重新加载最新版本'); }
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
  const filterQueue=async(nextLabel?:string,nextReview?:Annotation['review_status'])=>{
    if(!asset||loading||dirty||busy||drawing||jumping)return;
    const query=new URLSearchParams(params);
    if(nextLabel!==annotationLabel||nextReview!==reviewStatus)query.delete('page');
    if(nextLabel)query.set('label',nextLabel);else query.delete('label');
    if(nextReview)query.set('review_status',nextReview);else query.delete('review_status');
    if(!nextLabel&&!nextReview){setParams(query);return;}
    const request=++jumpRequest.current;setJumping(true);
    try{
      const result=await getAssets({project_id:asset.project_id,scope:'annotation',label:nextLabel,review_status:nextReview,page:1,page_size:1});
      if(request!==jumpRequest.current)return;
      if(!result.items[0]){notice.info(nextReview?'当前审核状态与标签筛选没有匹配图片':'当前标签没有已标注图片');return;}
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
      const result = await getAssets({ project_id: asset.project_id, scope: 'annotation', label:annotationLabel,review_status:reviewStatus,page: jumpPosition, page_size: 1 });
      if (request !== jumpRequest.current) return;
      if (!result.items[0]) { notice.warning('此序号已不存在，请重试切图读取'); return; }
      go(result.items[0].id);
    } catch { if (request === jumpRequest.current) notice.error('跳转失败，当前图片与草稿已保留，请重试'); }
    finally { setJumping(false); }
  };
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      const target = event.target as Element | null;
      if (event.repeat || event.isComposing || loading || drawing || busy || jumping || showShortcuts || removalOpen || target?.closest('input, textarea, select, [contenteditable="true"], [role="combobox"], [role="dialog"], .ant-select, .ant-modal-wrap, .ant-drawer')) return;
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
  return <Card className="detection-workspace" title={project ? `${project.project_name} · ${asset?.original_name || ''}` : '检测标注工作台'}
    extra={asset&&<Link to={fromReview ? `/annotations?${returnParams.toString()}` : isAdmin ? `/projects/${asset.project_id}/images` : `/annotations?project_id=${asset.project_id}`}>返回 {project?.project_name || '当前项目'} {fromReview || !isAdmin ? '标注与审核' : '项目图片'}</Link>}>
    {noticeContext}
    {asset && <WorkActivityTracker key={asset.id} projectId={asset.project_id} assetId={asset.id}
      enabled={!!image && !error && !readonly && !busy && !jumping && !removalOpen && !!labels.length}
      initialActivity={fromReview ? 'review' : 'annotation'} />}
    <div className="workspace-alerts">
    {!labels.length && project && <Alert type="warning" showIcon title={isAdmin?'此项目尚未配置标注类别，先到项目管理中编辑“标注类别”。':'此项目尚未配置标注类别，请联系管理员设置。'} action={isAdmin&&<Link to="/projects">设置类别</Link>} style={{ marginBottom: 12 }} />}
    {invalidLabels && <Alert type="warning" title="部分框的类别已不在项目配置中，请修改或删除这些框后保存；历史版本和冻结包不会自动改名。" style={{ marginBottom: 12 }} />}
    {duplicates.length > 0 && <Alert type="warning" showIcon title={`可能重复的同类框：${duplicates.map(([a, b]) => `${a + 1} / ${b + 1}`).join('、')}。真实遮挡允许重叠，请自行核对，不会自动删框。`} style={{ marginBottom: 12 }} />}
    {error && <Alert type="error" title={error} style={{ marginBottom: 12 }} />}
    {negativeReviewRestricted && <Alert type="info" title="负样本须由管理员最终审核，当前账号无法通过或驳回。" style={{ marginBottom: 12 }} />}
    {navigationError && <Alert type="error" title={navigationError} style={{ marginBottom: 12 }}
      action={<Button onClick={() => setNavigationReload(value => value + 1)}>重试切图读取</Button>} />}
    {neighbors?.matched===false&&<Alert type="info" style={{marginBottom:12}} title={neighbors.total?'当前图片已不符合筛选，上一张 / 下一张仍在筛选结果中切换':'当前筛选已无匹配图片，可清除筛选查看全部'}
      action={<Button disabled={dirty||busy||drawing||jumping} onClick={()=>void filterQueue(undefined,undefined)}>清除筛选</Button>}/>}
    </div>
    <Space className="workspace-toolbar" wrap style={{ marginBottom: 12 }}>
      <Select aria-label="筛选标注标签" prefix="筛选：" placeholder="全部标签" allowClear showSearch optionFilterProp="label" value={annotationLabel}
        onChange={value=>void filterQueue(value,reviewStatus)} options={labels.map(label=>({value:label,label}))} style={{minWidth:160}}
        disabled={!asset||loading||dirty||busy||drawing||jumping} title={dirty?'请先保存修改再筛选':'按最新已保存标注中的框类别筛选图片'}/>
      <Select<Annotation['review_status']|''> aria-label="筛选审核状态" prefix="审核：" value={reviewStatus||''} style={{minWidth:170}} title="按最新已保存标注的审核状态筛选"
        options={[{value:'',label:'全部状态'},{value:'pending',label:'待审核'},{value:'approved',label:'已通过'},{value:'rejected',label:'已驳回'}]}
        onChange={value=>void filterQueue(annotationLabel,value||undefined)} disabled={!asset||loading||dirty||busy||drawing||jumping}/>
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
      <Button type="primary" loading={busy} disabled={!asset || readonly || !dirty || drawing || !labels.length || invalidLabels} onClick={save}>保存标注</Button>
      <Button disabled={busy || drawing || !asset || loading} onClick={() => { if (!dirty || window.confirm('放弃未保存修改并重新加载吗？')) refresh(); }}>重新加载</Button>
      {isAdmin&&<Button danger disabled={!project||readonly||dirty||busy||drawing||jumping||!labels.length} title={dirty?'请先保存当前修改':'批量删除当前项目全部标注图片中的指定类别框'} onClick={()=>{
        setRemovalLabel(annotationLabel||(labels.includes('person')?'person':label||labels[0]||''));setRemovalPreview(undefined);setRemovalError('');setRemovalOpen(true);
      }}>批量删除类别框</Button>}
      <Button aria-label="审核通过" aria-keyshortcuts="R" disabled={!latest || dirty || readonly || busy || drawing || jumping || negativeReviewRestricted || !!(annotationLabel||reviewStatus)&&!neighbors} onClick={() => review(true)}>审核通过（R）</Button>
      <Button danger disabled={!latest || dirty || readonly || busy || drawing || negativeReviewRestricted} onClick={() => review(false)}>驳回</Button>
      <Button aria-label="撤销标注操作" disabled={!draft.past.length || readonly || busy || drawing} onClick={undo}>撤销</Button>
      <Button aria-label="重做标注操作" disabled={!draft.future.length || readonly || busy || drawing} onClick={redo}>重做</Button>
      <Button onClick={() => setShowShortcuts(true)}>快捷键</Button>
      <Tag>{dirty ? '未保存' : latest ? `v${latest.version} · ${{pending:'待审核',approved:'已通过',rejected:'已驳回'}[latest.review_status]}` : '待标注'}</Tag>
    </Space>
    <div className="workspace-grid">
      <div className="workspace-image-column">
        <p className="workspace-help">滚轮缩放；Ctrl/空格拖动平移。Shift拖动强制画框；重叠处默认选小框，Alt点击循环选择，也可用右侧框列表。坐标始终按原图像素保存。{historical && '当前为历史版本，只读。'}</p>
        <div style={{position:'relative',display:'flex',flexDirection:'column',flex:1,minHeight:0}} aria-busy={loading}>
        {loading&&<div role="status" style={{position:'absolute',top:12,right:12,zIndex:1,padding:'6px 12px',background:'var(--ui-surface)',borderRadius:6}}>正在加载图片…</div>}
        {image && <DetectionCanvas key={asset?.id} image={image} width={width} height={height} boxes={boxes}
          selected={selected} label={label} tool={tool} disabled={readonly || busy || !labels.length}
          onSelect={setSelected} onDragging={setDrawing}
          onChange={next => {if(!readonly)setDraft(previous => changeDraft(previous, { boxes: next, negative: false }));}} />}
        </div>
        <Space wrap><Button onClick={markNegative} disabled={!asset || readonly || busy || drawing || jumping || !labels.length || negative}
          title="清空当前框并标记负样本，可用Ctrl/Cmd+Z撤销">标记负样本（N）</Button>
        <Checkbox disabled={readonly || !!boxes.length || busy || drawing} checked={negative}
          onChange={e => change({ boxes, negative: e.target.checked })}>此图没有项目中的目标（负样本）</Checkbox>
        </Space>
      </div>
      <div className="workspace-inspector">
        {asset && <ModelPrediction key={asset.id} assetId={asset.id} labels={labels} disabled={readonly || busy || drawing || jumping || !labels.length}
          onApply={next => { change({ boxes: next, negative: false }); setSelected(-1); }} />}
        <h3>框列表（{boxes.length}）</h3>
        {boxes.map((b, i) => <Button key={i} block style={{ marginBottom: 6 }} type={i === selected ? 'primary' : 'default'} onClick={() => setSelected(i)}>{i + 1} · {b.label}</Button>)}
        {selected >= 0 && boxes[selected] && <Space orientation="vertical">
          <Select aria-label="选中框类别" disabled={readonly} value={boxes[selected].label} onChange={v => edit('label', v)} options={[...new Set([...labels, boxes[selected].label])].map(l => ({ value: l, label: l, disabled: !labels.includes(l) }))} />
          {(['x', 'y', 'width', 'height'] as const).map(k => <label key={k}>{k}: <InputNumber aria-label={`框${k}`} disabled={readonly}
            value={boxes[selected][k]} min={k === 'x' || k === 'y' ? 0 : 1} onChange={v => edit(k, v)} /></label>)}
          <Button danger disabled={readonly} onClick={removeBox}>删除选中框</Button>
        </Space>}
        <h3>标注历史</h3>
        <Select disabled={busy || drawing} aria-label="标注历史版本" style={{ width: '100%' }} value={viewed?.id}
          onChange={id => { if (!dirty || window.confirm('放弃未保存修改并查看历史吗？')) accept(history.find(a => a.id === id)); }}
          options={history.map(a => ({ value: a.id, label: `v${a.version} · ${{pending:'待审核',approved:'已通过',rejected:'已驳回'}[a.review_status]}${a.is_latest ? ' · 最新' : ''}` }))} />
        {historical && <Button disabled={loading} onClick={() => accept(latest)}>返回最新版本</Button>}
        <h3>操作记录</h3>
        <div style={{ maxHeight: 180, overflow: 'auto', fontSize: 12 }}>{events.map(e => <p key={e.id}>{e.detail.source==='bulk_label_delete'?'批量删除类别框':{create:'创建标注',update:'修改标注',approve:'审核通过',reject:'驳回',delete:'删除标注'}[e.action] || e.action} · 用户{e.user_id} · v{e.detail.version || '-'}<br />{new Date(e.created_at).toLocaleString()}</p>)}</div>
      </div>
    </div>
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
      <p>R 审核通过当前图片，不自动切图；须为最新已保存版本，负样本仍需管理员审核。标签筛选按最新已保存标注生效，前后切图和序号跳转保持该范围。</p>
      <p>N 清空当前图片的全部框并标记负样本，可撤销；手动保存或切图时保存。在上一张与下一张之间输入图片序号，回车跳转，有修改时先自动保存。</p>
      <p>A/D 或左右方向键切换图片（先自动保存，失败不切换）；Tab/Shift+Tab 在聚焦的画布中选择下一个/上一个框，到边缘后继续按可离开画布；Esc 取消拖动或选择。</p>
      <p>滚轮缩放；Ctrl/空格拖动平移；Shift 拖动在已有框上强制画框；Alt 点击循环选择重叠框。</p>
      <p>快捷键不打断输入框、类别选择和弹窗。本地撤销最多保留100步；保存/重新加载/查看历史/切换图片后清空。撤销不直接删除服务器历史。</p>
    </Modal>
  </Card>;
}

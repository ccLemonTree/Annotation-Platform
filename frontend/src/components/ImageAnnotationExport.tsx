import { useRef, useState } from 'react';
import { Alert,Button,Checkbox,Input,InputNumber,Modal,Select,Space } from 'antd';
import type { TaskType } from '../api/projects';
import type { AttributeFilters, AttributeSchema } from '../api/objectAttributes';
import apiClient from '../api/client';
import { isAxiosError } from 'axios';
import { DefaultPromptPreview } from './PromptEditor';
import type { ExportScope } from './ProjectExport';
import EmbeddingNegatives from './EmbeddingNegatives';
import EmbeddingNegativeRecommendations from './EmbeddingNegativeRecommendations';

export default function ImageAnnotationExport({projectId,taskType,attributeSchema,scope,onFrozen}:{projectId:string;taskType:TaskType;attributeSchema?:AttributeSchema;scope:ExportScope;onFrozen:()=>void}) {
  const [open,setOpen]=useState(false),[pending,setPending]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const downloadRequest=useRef<AbortController|null>(null);
  const defaultInstruction=taskType==='classification'?'请识别图片中的类别，以JSON数组回答。':'请描述这张图片。';
  const instructionFor=(sample:string)=>sample==='attributes'?'请分析目标属性，以 JSON 对象回答。无法判断的属性填 null。':
    sample==='attribute_descriptions'?'请用自然语言描述图中目标的可见属性。':
    sample==='labels'?'请识别图片中的类别，以JSON数组回答。':'请描述这张图片。';
  const [format,setFormat]=useState('portable'),[source,setSource]=useState(taskType==='classification'?'labels':'descriptions'),[instruction,setInstruction]=useState(defaultInstruction);
  const [attributes,setAttributes]=useState<AttributeFilters>({});
  const exportingAttributes=format==='attributes';
  const trainable=format!=='portable'&&!exportingAttributes;
  const [negativeMode,setNegativeMode]=useState<'batch'|'manual'|'automatic'>('batch');
  const [picker,setPicker]=useState(false),[recommendations,setRecommendations]=useState(false);
  const [generated,setGenerated]=useState<{id:string;key:string}>();
  const [semantics,setSemantics]=useState<'forward'|'reverse'|'both'>('forward');
  const [sampleLimit,setSampleLimit]=useState<number|null>(null),[negativeCount,setNegativeCount]=useState(3);
  const [positiveOnly,setPositiveOnly]=useState(false);
  const [versionLabel,setVersionLabel]=useState('');
  const single=source==='single_attribute'&&trainable&&format!=='swift_sft';
  const single_attribute=single?{semantics,sample_limit:sampleLimit,negative_count:positiveOnly?0:negativeCount}:undefined;
  const invalidLimit=single&&!positiveOnly&&format!=='infonce'&&sampleLimit===1;
  const manualAvailable=taskType==='caption'&&format==='infonce'&&!single;
  const explicit_negatives=manualAvailable&&negativeMode==='manual';
  const automatic=manualAvailable&&negativeMode==='automatic';
  const generationKey=JSON.stringify([source,!pending,scope]);
  const automatic_negative_job_id=automatic&&generated?.key===generationKey?generated.id:undefined;
  const [preview,setPreview]=useState<{preview_token:string;asset_count:number;sample_count?:number;negative_image_count?:number;key:string}>();
  const changeSource=(sample:string)=>{
    if(instruction===instructionFor(source))setInstruction(instructionFor(sample));
    setSource(sample);setError('');
  };
  const snapshotRequest={...scope,dataset_format:format,sample_source:single||source!=='single_attribute'?source:'descriptions',approved_only:!pending,explicit_negatives,automatic_negative_job_id,single_attribute,instruction:format==='swift_sft'?instruction:defaultInstruction};
  const requestKey=JSON.stringify(snapshotRequest);
  const confirmed=preview?.key===requestKey?preview:undefined;
  const negativesChanged=()=>{setNegativeMode('manual');setPreview(undefined);};
  const generatedNegativesChanged=(id?:string)=>{setGenerated(id?{id,key:generationKey}:undefined);setPreview(undefined);};
  const prepare=async(freeze:boolean)=>{
    setBusy(true);setError('');
    try {
      const url=`/api/export/projects/${projectId}/annotation-snapshots`;
      if(freeze&&confirmed?.asset_count){
        await apiClient.post(url+'/jobs',{...snapshotRequest,preview_token:confirmed.preview_token,version_label:versionLabel},{skipErrorToast:true});
        setPreview(undefined);setOpen(false);
        onFrozen();
      }else if(!freeze){
        const {data}=await apiClient.post(url+'/preview',snapshotRequest,{timeout:120000});
        setPreview({...data,key:requestKey});
      }
    } catch(cause) {
      if(isAxiosError(cause)&&cause.response?.status===409)setPreview(undefined);
      setError(isAxiosError(cause)&&typeof cause.response?.data?.detail==='string'?cause.response.data.detail:'读取或冻结失败，请检查冻结版本后重试。');
    } finally {setBusy(false);}
  };
  const download=async()=>{
    const controller=new AbortController();
    downloadRequest.current=controller;
    setBusy(true);setError('');
    try {
      const response=await apiClient.get(exportingAttributes?`/api/projects/${projectId}/attributes/export`:`/api/export/projects/${projectId}/export`,{
        params:exportingAttributes?{...scope,...attributes,approved_only:!pending&&attributes.review_status!=='pending'}:
          {...scope,format:'annotation',approved_only:!pending,dataset_format:format,sample_source:snapshotRequest.sample_source,explicit_negatives,automatic_negative_job_id,single_attribute:single_attribute?JSON.stringify(single_attribute):undefined,instruction:format==='swift_sft'?instruction:undefined},
        responseType:'blob',timeout:0,signal:controller.signal,skipErrorToast:true});
      const url=URL.createObjectURL(response.data);const link=document.createElement('a');link.href=url;link.download=`${exportingAttributes?'attributes':'annotations'}_${projectId}.zip`;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);setOpen(false);
    } catch(error) {
      if(controller.signal.aborted)return;
      let detail='ZIP 下载中断，请重试。';
      if(isAxiosError(error)&&error.response?.data instanceof Blob){try{const body=JSON.parse(await error.response.data.text());if(typeof body.detail==='string')detail=body.detail;}catch{/* Keep the fallback for non-JSON responses. */}}
      setError(detail);
    }
    finally {downloadRequest.current=null;setBusy(false);}
  };
  const close=()=>{
    if(busy&&!downloadRequest.current)return;
    downloadRequest.current?.abort();
    setOpen(false);
  };
  return <><Button onClick={()=>{setPreview(undefined);setError('');setOpen(true);}}>导出 / 冻结描述与属性</Button>
    <Modal title="描述与属性数据" open={open} onCancel={close} footer={<Space wrap>
      <Button disabled={busy&&!downloadRequest.current} onClick={close}>{downloadRequest.current?'取消下载':'关闭'}</Button><Button loading={!!downloadRequest.current} disabled={busy||invalidLimit||automatic&&!automatic_negative_job_id} onClick={download}>下载 ZIP</Button>
      {trainable&&<><Button disabled={busy||invalidLimit||automatic&&!automatic_negative_job_id||format==='swift_sft'&&!instruction.trim()} onClick={()=>void prepare(false)}>预览冻结范围</Button>
        <Button type="primary" disabled={busy||!confirmed?.asset_count} onClick={()=>void prepare(true)}>冻结数据</Button></>}
    </Space>}>
    <p>{exportingAttributes?'属性 ZIP 包含图片、属性 CSV、结构化 JSONL 和来源清单。':'包含图片、描述/类别 JSONL、来源清单；单选分类另按类别编号分目录。向量匹配默认仅导出审核通过的正负配对，AI分歧需先人工复核，无法判断不作为负样本。'}</p>
    <p>同一原图的所有裁剪图应归入同一训练/验证划分，本包不自动划分。</p>
    <Space orientation="vertical" style={{width:'100%',marginBottom:16}}>
      <label htmlFor="training-dataset-format">数据格式</label><Select id="training-dataset-format" aria-label="训练数据格式" value={format} disabled={busy} style={{width:'100%'}} onChange={value=>{setFormat(value);setError('');if(value==='swift_sft'&&['matches','single_attribute'].includes(source))changeSource('descriptions');}}
        options={[{value:'portable',label:'通用标注包（保留原始数据）'},...(attributeSchema?[{value:'attributes',label:'目标属性 ZIP · CSV 与图片'}]:[]),{value:'swift_sft',label:'大模型 SFT · messages + images'},...(taskType==='caption'||taskType==='vector'?[{value:'infonce',label:'向量 InfoNCE'},{value:'contrastive',label:'向量 Contrastive'},{value:'online_contrastive',label:'向量 Online Contrastive'},{value:'cosine_similarity',label:'向量 Cosine Similarity'}]:[])]}/>
      {exportingAttributes&&<Space wrap>
        <Select aria-label="导出属性字段" placeholder="全部属性" allowClear style={{width:160}} value={attributes.attribute_key}
          options={attributeSchema?.fields.map(field=>({value:field.key,label:field.name}))}
          onChange={attribute_key=>setAttributes(previous=>({review_status:previous.review_status,attribute_key,attribute_state:attribute_key?'analyzed':'all'}))}/>
        <Input aria-label="导出属性值" placeholder="精确匹配属性值" maxLength={500} allowClear disabled={busy||!attributes.attribute_key||attributes.attribute_state==='unknown'||attributes.attribute_state==='missing'}
          value={attributes.attribute_value||''} onChange={event=>setAttributes(previous=>({...previous,attribute_value:event.target.value||undefined}))}/>
        <Select aria-label="导出属性判断状态" value={attributes.attribute_state||'all'} disabled={!attributes.attribute_key} style={{width:160}}
          options={[{value:'all',label:'全部判断状态'},{value:'analyzed',label:'已分析'},{value:'known',label:'已确认值'},{value:'unknown',label:'无法判断'},{value:'missing',label:'尚未分析此属性'}]}
          onChange={attribute_state=>setAttributes(previous=>({...previous,attribute_state,
            attribute_value:attribute_state==='unknown'||attribute_state==='missing'?undefined:previous.attribute_value}))}/>
        <Select aria-label="导出属性审核状态" placeholder="按审核范围导出" allowClear value={attributes.review_status} style={{width:160}}
          options={[{value:'approved',label:'仅已通过'},{value:'pending',label:'仅待审核'}]} onChange={review_status=>setAttributes(previous=>({...previous,review_status}))}/>
      </Space>}
      {format!=='portable'&&!exportingAttributes&&<><label htmlFor="training-sample-source">数据来源</label><Select id="training-sample-source" aria-label="训练数据来源" value={source} disabled={busy} style={{width:'100%'}} onChange={changeSource}
        options={taskType==='classification'?[{value:'labels',label:'分类标签配对'}]:[{value:'descriptions',label:'图片描述配对'},{value:'attributes',label:format==='swift_sft'?'目标属性（结构化 JSON）':'目标属性配对'},{value:'attribute_descriptions',label:'目标属性转自然语言'},...(format!=='swift_sft'?[{value:'single_attribute',label:'单属性文图配对'}]:[]),...(taskType==='vector'&&format!=='swift_sft'?[{value:'matches',label:'检索匹配判定（正确 / 错误）'}]:[])]}/>
        {single&&<>
          <label htmlFor="single-semantics">语义方向</label>
          <Select id="single-semantics" value={semantics} onChange={setSemantics} disabled={busy}
            options={[{value:'forward',label:'仅正向'},{value:'reverse',label:'仅反向'},{value:'both',label:'正向和反向'}]}/>
          <Checkbox checked={positiveOnly} onChange={e=>setPositiveOnly(e.target.checked)} disabled={busy}>只导出正样本</Checkbox>
          <Space wrap>
            <label htmlFor="single-limit">训练记录上限</label><InputNumber id="single-limit" min={positiveOnly||format==='infonce'?1:2} max={1000000} precision={0} value={sampleLimit} onChange={setSampleLimit} placeholder="全部" disabled={busy}/>
            {!positiveOnly&&<><label htmlFor="single-negatives">{format==='infonce'?'每条负样本张数':'负样本张数'}</label><InputNumber id="single-negatives" min={1} max={20} precision={0} value={negativeCount} onChange={v=>setNegativeCount(v??1)} disabled={busy}/></>}
          </Space>
          <p>支持目标属性和固定 JSON 格式的图片描述。{positiveOnly?'自由文本可生成正向配对，明确的否定描述可生成反向配对。':'自动负样本使用可明确区分的属性。'}训练记录上限留空时导出全部。</p>
          {invalidLimit&&<Alert type="warning" title="当前格式至少需要两条记录，才能包含正负样本"/>}
        </>}
        {format==='swift_sft'?<><label htmlFor="training-sft-instruction">训练指令</label><Input.TextArea id="training-sft-instruction" aria-label="SFT训练指令" value={instruction} maxLength={2000} disabled={busy} autoSize={{minRows:2,maxRows:5}} onChange={e=>setInstruction(e.target.value)}/><DefaultPromptPreview value={instructionFor(source)} label="默认训练指令"/><p>所选标注作为 assistant 回答。可在指令中放一个 &lt;image&gt;；未填写占位符时自动加在开头。</p></>:single?null:source==='matches'?<p>正确 / 错误导出为 1 / 0，检索相似度不作训练标签。InfoNCE 每行一个正样本，无法判断不参与。</p>:<p>每张图片与所选描述或已填写的属性组成正样本。{format==='infonce'?(explicit_negatives?'仅导出已确认负样本的配对。':automatic?'仅导出有明确不匹配负图的配对。':'平台训练会使用批内负样本。'):'此来源只提供正样本。'}</p>}
        {manualAvailable&&<Space wrap>
          <label htmlFor="infonce-negative-mode">负样本来源</label>
          <Select id="infonce-negative-mode" aria-label="负样本来源" value={negativeMode} disabled={busy} style={{width:160}}
            options={[{value:'batch',label:'批内负样本'},{value:'manual',label:'人工确认'},{value:'automatic',label:'自动生成'}]}
            onChange={setNegativeMode}/>
          {explicit_negatives&&<><Button disabled={busy} onClick={()=>setPicker(true)}>配置负样本</Button>
            <Button disabled={busy} onClick={()=>setRecommendations(true)}>自动推荐负样本</Button></>}
          {automatic&&<Button disabled={busy} onClick={()=>setRecommendations(true)}>{automatic_negative_job_id?'查看生成结果':'自动生成负样本'}</Button>}
        </Space>}
        <p>ZIP包含 train.jsonl 和图片。解压后进入该目录使用，图片路径相对此目录。</p></>}
    </Space>
    {trainable&&<Space orientation="vertical" style={{width:'100%',marginBottom:12}}>
      <label htmlFor="annotation-version-label">冻结版本标签（可选）</label>
      <Input id="annotation-version-label" value={versionLabel} maxLength={100} disabled={busy} onChange={e=>setVersionLabel(e.target.value)}/>
    </Space>}
    <Checkbox checked={exportingAttributes&&attributes.review_status?attributes.review_status==='pending':pending} disabled={busy||exportingAttributes&&!!attributes.review_status}
      onChange={e=>setPending(e.target.checked)}>{exportingAttributes?'包含待审核属性标注':'包含待审核标注（含匹配判定，不含AI分歧待复核项）'}</Checkbox>
    {trainable&&confirmed&&<Alert type={confirmed.asset_count?'info':'warning'}
      title={single||explicit_negatives||automatic?`本次冻结 ${confirmed.sample_count||0} 条配对，包含 ${confirmed.negative_image_count||0} 张负图`:`本次冻结范围 ${confirmed.asset_count} 张图片`} style={{marginTop:12}}/>}
    {error&&<Alert type="error" title={error} style={{marginTop:12}}/>}
  </Modal>
    {picker&&<EmbeddingNegatives projectId={projectId} scope={scope} source={source} onClose={()=>setPicker(false)}
      onSaved={negativesChanged}/>}
    {recommendations&&<EmbeddingNegativeRecommendations projectId={projectId} scope={scope} source={source} approvedOnly={!pending}
      onIncludePending={()=>setPending(true)} onClose={()=>setRecommendations(false)} onConfirmed={negativesChanged}
      onGenerated={automatic?generatedNegativesChanged:undefined}/>}
  </>;
}

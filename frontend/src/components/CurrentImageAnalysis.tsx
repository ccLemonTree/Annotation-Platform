import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Descriptions,Modal,Select,Space,Typography} from 'antd';
import {isAxiosError} from 'axios';
import {analyzeCurrentImage,getAnalysisModels,type AnalysisModel,type AnalysisMode,type ImageAnalysis} from '../api/annotationAnalysis';
import PromptEditor from './PromptEditor';

export default function CurrentImageAnalysis({assetId,name,attributesAvailable=false,disabled,onApply}:{assetId:string;name:string;attributesAvailable?:boolean;disabled:boolean;onApply:(result:ImageAnalysis)=>void}){
  const [open,setOpen]=useState(false),[models,setModels]=useState<AnalysisModel[]>([]),[modelId,setModelId]=useState<string>();
  const [loading,setLoading]=useState(false),[analyzing,setAnalyzing]=useState(false),[reload,setReload]=useState(0);
  const [result,setResult]=useState<ImageAnalysis>(),[error,setError]=useState('');
  const [mode,setMode]=useState<AnalysisMode>(attributesAvailable?'attributes':'description');
  const [prompt,setPrompt]=useState(''),[attributePrompt,setAttributePrompt]=useState('');
  const request=useRef(0),controller=useRef<AbortController|undefined>(undefined);
  const model=models.find(row=>row.id===modelId);
  useEffect(()=>{setPrompt(mode==='description'&&model?.supports_prompt?model.prompt??'':'');setAttributePrompt(mode==='attributes'&&model?.supports_prompt?model.prompt??'':'');},[model,mode]);
  const message=(error:unknown)=>isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'分析失败，请稍后重试';
  const close=()=>{request.current++;controller.current?.abort();controller.current=undefined;setAnalyzing(false);setOpen(false);};
  useEffect(()=>{setOpen(false);setResult(undefined);setError('');setMode(attributesAvailable?'attributes':'description');return()=>{request.current++;controller.current?.abort();};},[assetId,attributesAvailable]);
  useEffect(()=>{if(!open)return;const abort=new AbortController();let active=true;
    setLoading(true);setModels([]);setResult(undefined);setError('');
    getAnalysisModels(assetId,mode,abort.signal).then(rows=>{if(active){setModels(rows);const available=rows.filter(row=>!row.unavailable_reason);setModelId(previous=>available.some(row=>row.id===previous)?previous:available.length===1?available[0].id:undefined);}})
      .catch(error=>{if(active)setError(message(error));}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;abort.abort();};
  },[open,assetId,mode,reload]);
  const analyze=async()=>{
    if(disabled||!model||model.unavailable_reason||controller.current)return;
    const id=++request.current,abort=new AbortController();controller.current=abort;
    setAnalyzing(true);setError('');setResult(undefined);
    try{
      const response=await analyzeCurrentImage(assetId,model,mode,prompt,attributePrompt,abort.signal);
      if(id===request.current){if(response.asset_id!==assetId)throw Error('Image changed');setResult(response);}
    }catch(error){if(id===request.current)setError(message(error));}
    finally{if(id===request.current){controller.current=undefined;setAnalyzing(false);}}
  };
  return <>
    <Button type="primary" disabled={disabled} onClick={()=>{setResult(undefined);setError('');setOpen(true);}}>大模型分析当前图片</Button>
    <Modal title="大模型分析当前图片" open={open} width={760} onCancel={close} footer={[
      <Button key="close" onClick={close}>关闭</Button>,
      <Button key="analyze" loading={analyzing} disabled={disabled||loading||!model||!!model.unavailable_reason} onClick={()=>void analyze()}>开始分析</Button>,
      <Button key="apply" type="primary" disabled={disabled||analyzing||!result||result.asset_id!==assetId} onClick={()=>{if(result?.asset_id===assetId){onApply(result);close();}}}>填入标注区</Button>
    ]}>
      <Space orientation="vertical" size="middle" style={{width:'100%'}}>
        <Typography.Text style={{overflowWrap:'anywhere'}}>当前图片：{name}</Typography.Text>
        <Typography.Text type="secondary">{mode==='attributes'?'按项目属性配置分析当前图片。':'生成当前图片的描述，保留已有检测框、分类和属性。'}结果先预览，填入后请核对并保存。</Typography.Text>
        {attributesAvailable&&<Select aria-label="分析内容" value={mode} disabled={analyzing||loading} style={{width:240,maxWidth:'100%'}} onChange={value=>{setMode(value);setModelId(undefined);setResult(undefined);setError('');}} options={[{value:'attributes',label:'分析项目属性'},{value:'description',label:'描述当前图片'}]}/>}
        <Space wrap><Select aria-label="单图分析模型" placeholder="选择分析模型" value={modelId} loading={loading} disabled={analyzing||loading} style={{width:340,maxWidth:'100%'}} onChange={id=>{setModelId(id);setResult(undefined);setError('');}} options={models.map(row=>({value:row.id,disabled:!!row.unavailable_reason,label:`${row.name} · r${row.revision}${row.unavailable_reason?' · '+row.unavailable_reason:''}`}))}/>
          <Button disabled={analyzing||loading} onClick={()=>setReload(value=>value+1)}>刷新模型</Button></Space>
        {(!model||model.supports_prompt)?<Space wrap>
          {mode==='attributes'?<><PromptEditor key={`attributes:${modelId}`} title="本次属性分析核心提示词" buttonLabel="编辑属性分析提示词" ariaLabel="属性分析核心提示词" value={attributePrompt} defaultValue={model?.prompt} disabled={analyzing||loading||!model}
            onChange={value=>{setAttributePrompt(value);setResult(undefined);}} help="编辑本次属性分析的完整核心指令；留空使用默认核心，不修改全局模型配置。" readOnlyContext="系统追加当前项目的目标类别、属性字段与选项、JSON 返回结构；属性结果仍按当前项目配置校验。"/>
            <PromptEditor key={`supplement:${modelId}`} title="本次属性分析补充要求" buttonLabel="编辑属性补充要求" ariaLabel="补充分析要求" value={prompt} defaultValue="" referencePrompt={attributePrompt||model?.prompt} referenceLabel="本次属性核心提示词" disabled={analyzing||loading||!model}
              onChange={value=>{setPrompt(value);setResult(undefined);}} help="追加到本次属性分析核心之后。仅补充本次分析要求，项目属性、选项和返回结构保持不变。"/></>
            :<PromptEditor key={`description:${modelId}`} title="本次图片描述提示词" buttonLabel="编辑图片描述提示词" ariaLabel="本次提示词" value={prompt} defaultValue={model?.prompt} disabled={analyzing||loading||!model}
              onChange={value=>{setPrompt(value);setResult(undefined);}} help="仅用于本次分析，不修改模型的默认提示词。"/>}
        </Space>:<Typography.Text type="secondary">当前模型使用固定接口，不支持自由提示词。</Typography.Text>}
        {!loading&&!error&&!models.some(row=>!row.unavailable_reason)&&<Alert type="info" showIcon title="暂无可用分析模型，请联系管理员配置在线且支持当前属性的视觉模型。"/>}
        {error&&<Alert type="error" showIcon title={error}/>}
        {analyzing&&<Typography.Text role="status">正在分析当前图片，请稍候；关闭窗口可取消等待。</Typography.Text>}
        {result&&<>
          <Typography.Text strong>分析结果（尚未保存）</Typography.Text>
          <Alert type="warning" showIcon title={result.attributes?'填入后将替换当前属性草稿；图片分类与描述保留。请核对后保存。':'填入后将替换当前图片描述草稿；其他标注保留。请核对后保存。'}/>
          {result.attribute_schema&&result.attributes?<Descriptions column={1} bordered size="small" items={result.attribute_schema.fields.map(field=>{
            const item=result.attributes![field.key];
            return {key:field.key,label:field.name,children:<Space orientation="vertical" size={4} style={{overflowWrap:'anywhere'}}>
              <Typography.Text strong type={item?.value==null?'warning':undefined}>{item?.value??'无法判断'}</Typography.Text>
              {item?.reason&&<Typography.Text type="secondary">{item.reason}</Typography.Text>}
            </Space>};
          })}/>:<Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{result.description}</Typography.Paragraph>}
        </>}
      </Space>
    </Modal>
  </>;
}

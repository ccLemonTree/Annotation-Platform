import { useState } from 'react';
import { Alert,Button,Checkbox,Input,Modal,Select,Space } from 'antd';
import type { TaskType } from '../api/projects';
import apiClient from '../api/client';
import { isAxiosError } from 'axios';

export default function ImageAnnotationExport({projectId,taskType}:{projectId:string;taskType:TaskType}) {
  const [open,setOpen]=useState(false),[pending,setPending]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [format,setFormat]=useState('portable'),[source,setSource]=useState(taskType==='classification'?'labels':'descriptions'),[instruction,setInstruction]=useState(taskType==='classification'?'请识别图片中的类别，以JSON数组回答。':'请描述这张图片。');
  const download=async()=>{
    setBusy(true);setError('');
    try {
      const response=await apiClient.get(`/api/export/projects/${projectId}/export`,{params:{format:'annotation',approved_only:!pending,dataset_format:format,sample_source:source,instruction:format==='swift_sft'?instruction:undefined},responseType:'blob',timeout:120000});
      const url=URL.createObjectURL(response.data);const link=document.createElement('a');link.href=url;link.download=`annotations_${projectId}.zip`;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);setOpen(false);
    } catch(error) {
      let detail='导出失败，请检查所选来源是否有符合审核范围的标注；InfoNCE检索配对需要至少一个正确结果。';
      if(isAxiosError(error)&&error.response?.data instanceof Blob){try{const body=JSON.parse(await error.response.data.text());if(typeof body.detail==='string')detail=body.detail;}catch{/* Keep the fallback for non-JSON responses. */}}
      setError(detail);
    }
    finally {setBusy(false);}
  };
  return <><Button onClick={()=>setOpen(true)}>导出标注数据</Button><Modal title="导出训练标注" open={open} onCancel={()=>!busy&&setOpen(false)} onOk={download} confirmLoading={busy} okButtonProps={{'aria-label':'下载 ZIP',disabled:busy}} okText="下载 ZIP" cancelText="取消">
    <p>包含图片、描述/类别 JSONL、来源清单；单选分类另按类别编号分目录。向量匹配默认仅导出审核通过的正负配对，AI分歧需先人工复核，无法判断不作为负样本。</p>
    <p>同一原图的所有裁剪图应归入同一训练/验证划分，本包不自动划分。</p>
    <Space orientation="vertical" style={{width:'100%',marginBottom:16}}>
      <label htmlFor="training-dataset-format">数据格式</label><Select id="training-dataset-format" aria-label="训练数据格式" value={format} disabled={busy} style={{width:'100%'}} onChange={value=>{setFormat(value);setError('');if(value==='swift_sft'&&source==='matches')setSource('descriptions');}}
        options={[{value:'portable',label:'通用标注包（保留原始数据）'},{value:'swift_sft',label:'大模型 SFT · messages + images'},...(taskType==='vector'?[{value:'infonce',label:'向量 InfoNCE'},{value:'contrastive',label:'向量 Contrastive'},{value:'online_contrastive',label:'向量 Online Contrastive'},{value:'cosine_similarity',label:'向量 Cosine Similarity'}]:[])]}/>
      {format!=='portable'&&<><label htmlFor="training-sample-source">数据来源</label><Select id="training-sample-source" aria-label="训练数据来源" value={source} disabled={busy} style={{width:'100%'}} onChange={value=>{setSource(value);setError('');}}
        options={[{value:'descriptions',label:'图片描述配对'},{value:'labels',label:'分类标签配对'},...(taskType==='vector'&&format!=='swift_sft'?[{value:'matches',label:'检索匹配判定（正确 / 错误）'}]:[])]}/>
        {format==='swift_sft'?<><label htmlFor="training-sft-instruction">训练指令</label><Input.TextArea id="training-sft-instruction" aria-label="SFT训练指令" value={instruction} maxLength={2000} disabled={busy} autoSize={{minRows:2,maxRows:5}} onChange={e=>setInstruction(e.target.value)}/><p>保存的描述或类别作为 assistant 回答。可在指令中放一个 &lt;image&gt;；未填写占位符时自动加在开头。</p></>:<p>正确 / 错误导出为 1 / 0，检索相似度不作训练标签。InfoNCE每行一个正样本，无法判断不参与；图片描述和分类标签配对不自动生成负样本。</p>}
        <p>ZIP包含 train.jsonl 和图片。解压后进入该目录使用，图片路径相对此目录。</p></>}
    </Space>
    <Checkbox checked={pending} disabled={busy} onChange={e=>setPending(e.target.checked)}>包含待审核标注（含匹配判定，不含AI分歧待复核项）</Checkbox>
    {error&&<Alert type="error" title={error} style={{marginTop:12}}/>}
  </Modal></>;
}

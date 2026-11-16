import {useState,type ReactNode} from 'react';
import {Button,Input,Modal,Space,Typography} from 'antd';

export function DefaultPromptPreview({value,label='默认提示词',expanded=true}:{value?:string;label?:string;expanded?:boolean}){
  return value?.trim()?<details open={expanded}>
    <summary>{label}</summary>
    <Typography.Paragraph style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',margin:'8px 0 0'}}>{value}</Typography.Paragraph>
  </details>:null;
}

export default function PromptEditor({value='',onChange,title,buttonLabel='编辑提示词',disabled=false,defaultValue,prefillDefault=false,maxLength=8000,required=false,help,readOnlyContext,ariaLabel,readOnly=false,referencePrompt,referenceLabel}:{
  value?:string;onChange?:(value:string)=>void;title:string;buttonLabel?:string;disabled?:boolean;defaultValue?:string;
  prefillDefault?:boolean;maxLength?:number;required?:boolean;help?:ReactNode;readOnlyContext?:string;ariaLabel?:string;readOnly?:boolean;
  referencePrompt?:string;referenceLabel?:string;
}){
  const [open,setOpen]=useState(false),[draft,setDraft]=useState('');
  const invalid=draft.length>maxLength||required&&!draft.trim();
  const displayedDefault=referencePrompt||defaultValue;
  return <>
    <Space wrap>
      <Button disabled={disabled} onClick={()=>{setDraft(value||(prefillDefault?defaultValue||'':''));setOpen(true);}}>{buttonLabel}</Button>
      <Typography.Text type="secondary" ellipsis style={{maxWidth:320}}>{value.trim()?value.split('\n')[0]:required?'尚未设置':referencePrompt&&!defaultValue?'未填写自定义内容':defaultValue?'使用默认提示词':'未填写提示词'}</Typography.Text>
    </Space>
    <Modal title={title} open={open} width={780} onCancel={()=>setOpen(false)} styles={{body:{maxHeight:'70dvh',overflowY:'auto'}}} footer={
      <Space wrap>
        {!readOnly&&defaultValue!==undefined&&<Button disabled={disabled} onClick={()=>setDraft(defaultValue)}>恢复默认提示词</Button>}
        <Button aria-label={readOnly?'关闭提示词':'取消'} onClick={()=>setOpen(false)}>{readOnly?'关闭':'取消'}</Button>
        {!readOnly&&<Button type="primary" disabled={disabled||invalid} onClick={()=>{onChange?.(prefillDefault&&draft===defaultValue?'':draft);setOpen(false);}}>应用提示词</Button>}
      </Space>
    }>
      <Space orientation="vertical" size="middle" style={{width:'100%'}}>
        {help&&<Typography.Paragraph style={{marginBottom:0}}>{help}</Typography.Paragraph>}
        {displayedDefault?.trim()?<DefaultPromptPreview value={displayedDefault} label={referenceLabel}/>:<Typography.Text type="secondary">{required?'请填写本次内容。没有可显示的默认提示词。':'没有可显示的默认提示词。'}</Typography.Text>}
        <Input.TextArea aria-label={ariaLabel??title} value={draft} readOnly={readOnly} disabled={disabled} maxLength={maxLength} showCount
          autoSize={{minRows:8,maxRows:18}} onChange={event=>setDraft(event.target.value)}/>
        {!readOnly&&invalid&&<Typography.Text type="danger">{draft.length>maxLength?`提示词不能超过 ${maxLength} 字`:'请填写提示词'}</Typography.Text>}
        {readOnlyContext&&<details><summary>系统追加的固定约束</summary><Typography.Paragraph style={{whiteSpace:'pre-wrap',marginTop:8}}>{readOnlyContext}</Typography.Paragraph></details>}
      </Space>
    </Modal>
  </>;
}

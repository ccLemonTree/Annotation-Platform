import { useEffect,useRef,useState } from 'react';
import { Button,Checkbox,Space,Typography } from 'antd';

export default function TrainingLogs({text,busy,onRefresh}:{text:string;busy:boolean;onRefresh:()=>void}){
  const [wrap,setWrap]=useState(false),[follow,setFollow]=useState(true);
  const panel=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(follow&&panel.current)panel.current.scrollTop=panel.current.scrollHeight;},[text,follow]);
  const lines=text.split('\n');
  return <Space orientation="vertical" style={{width:'100%',minWidth:0}}>
    <Space wrap><Button disabled={busy} onClick={onRefresh}>刷新日志</Button>
      <Checkbox checked={wrap} onChange={event=>setWrap(event.target.checked)}>自动换行</Checkbox>
      <Checkbox checked={follow} onChange={event=>setFollow(event.target.checked)}>跟随最新</Checkbox>
      <Typography.Text type="secondary">最近200行 · {text?lines.length:0} 行</Typography.Text>
    </Space>
    <div ref={panel} role="log" aria-label="训练日志内容" tabIndex={0} onScroll={()=>{
      const node=panel.current;if(node&&node.scrollTop+node.clientHeight<node.scrollHeight-40&&follow)setFollow(false);
    }} style={{height:'min(55dvh,560px)',minHeight:240,overflow:'auto',border:'1px solid var(--ui-border)',borderRadius:8,background:'var(--ui-background)',fontFamily:'Consolas, "SFMono-Regular", monospace',fontSize:12,lineHeight:1.7,padding:'12px 0'}}>
      {text?lines.map((line,index)=><div key={index} style={{display:'flex',minWidth:wrap?undefined:'max-content'}}>
        <span aria-hidden style={{flex:'0 0 48px',textAlign:'right',paddingRight:16,color:'var(--ui-muted)',userSelect:'none'}}>{index+1}</span>
        <code style={{flex:1,minWidth:0,paddingRight:16,whiteSpace:wrap?'pre-wrap':'pre',overflowWrap:wrap?'anywhere':undefined,color:/\b(error|exception|traceback|failed)\b/i.test(line)?'var(--ui-error)':/\bwarning\b/i.test(line)?'var(--ui-warning)':undefined}}>{line||' '}</code>
      </div>):<Typography.Text type="secondary" style={{padding:16}}>暂无日志。点击刷新读取。</Typography.Text>}
    </div>
    <Typography.Text type="secondary">已合并输出顺序、清理颜色控制符和进度条回写；错误与告警保留。刷新读取当前日志，不影响训练。</Typography.Text>
  </Space>;
}

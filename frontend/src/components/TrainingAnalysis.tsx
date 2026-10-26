import {useEffect,useState} from 'react';
import {Alert,Button,Card,Collapse,Input,Select,Space,Table,Tag,Typography} from 'antd';
import {Link} from 'react-router-dom';
import {analyzeTraining,collectTrainingEvidence,getTrainingComparisons,getTrainingReports,getTrainingReport} from '../api/training';
import type {TrainingEvidence,TrainingEvidenceRun,TrainingMetric,TrainingReport,TrainingReportSummary,TrainingRun} from '../api/training';
import {getModelServices,getPromptDefaults} from '../api/modelServices';
import type {ModelService} from '../api/modelServices';
import JsonView from './JsonView';

const value=(n:number|null|undefined)=>n==null?'—':Number(n.toPrecision(6)).toString();
const errorText=(error:unknown)=>{
  const cause=error as {response?:{data?:{detail?:string;message?:string}};message?:string};
  return cause.response?.data?.detail||cause.response?.data?.message||cause.message||'操作失败，请重试';
};

function MetricCurve({runs,metric}:{runs:TrainingEvidenceRun[];metric:TrainingMetric}){
  const colors=['#6b9dff','#51cfbd','#e9b970','#bc9cff'];
  const lines=runs.flatMap((run,index)=>{
    const found=run.metrics.find(m=>m.name===metric.name&&m.file===metric.file);
    return found?.series.length?[{name:run.name,color:colors[index],points:found.series}]:[];
  });
  const points=lines.flatMap(line=>line.points);
  if(!points.length)return <Typography.Text type="secondary">没有可绘制的曲线记录</Typography.Text>;
  const axis=points.every(p=>p.epoch!=null)?'epoch':points.every(p=>p.step!=null)?'step':'index';
  const xs=points.map(p=>p[axis]!),ys=points.map(p=>p.value);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const x=(p:number)=>65+(p-minX)/(maxX-minX||1)*650;
  const y=(p:number)=>215-(p-minY)/(maxY-minY||1)*180;
  return <>
    <svg role="img" aria-label={`${metric.name} 指标趋势，横轴${axis==='index'?'记录序号':axis}`} viewBox="0 0 760 260" style={{width:'100%',maxHeight:300,display:'block'}}>
      {[0,.5,1].map(t=><g key={t}><line x1="65" x2="715" y1={35+t*180} y2={35+t*180} stroke="currentColor" opacity=".15"/><text x="58" y={40+t*180} textAnchor="end" fill="currentColor" fontSize="12">{value(maxY-t*(maxY-minY))}</text></g>)}
      <text x="65" y="239" fill="currentColor" fontSize="12">{value(minX)}</text><text x="715" y="239" textAnchor="end" fill="currentColor" fontSize="12">{value(maxX)}</text>
      <text x="390" y="255" textAnchor="middle" fill="currentColor" fontSize="12">{axis==='index'?'记录序号':axis}</text>
      {lines.map(line=><g key={line.name}><polyline points={line.points.map(p=>`${x(p[axis]!)},${y(p.value)}`).join(' ')} fill="none" stroke={line.color} strokeWidth="2"/>{line.points.length===1&&<circle cx={x(line.points[0][axis]!)} cy={y(line.points[0].value)} r="4" fill={line.color}/>}</g>)}
    </svg>
    <Space wrap>{lines.map(line=><Typography.Text key={line.name} style={{color:line.color}}>● {line.name}</Typography.Text>)}</Space>
  </>;
}

export default function TrainingAnalysis({run,admin}:{run:TrainingRun;admin:boolean}){
  const [models,setModels]=useState<ModelService[]>([]),[modelId,setModelId]=useState<string>();
  const [candidates,setCandidates]=useState<TrainingRun[]>([]),[total,setTotal]=useState(0),[page,setPage]=useState(1);
  const [compareIds,setCompareIds]=useState<string[]>([]),[prompt,setPrompt]=useState(''),[defaultPrompt,setDefaultPrompt]=useState('');
  const [history,setHistory]=useState<TrainingReportSummary[]>([]),[report,setReport]=useState<TrainingReport>();
  const [evidence,setEvidence]=useState<TrainingEvidence>(),[metricKey,setMetricKey]=useState<string>();
  const [busy,setBusy]=useState(''),[error,setError]=useState('');
  useEffect(()=>{
    let current=true;
    void Promise.all([getModelServices({enabled:true}),getTrainingComparisons(run.id),getTrainingReports(run.id),getPromptDefaults()]).then(async([models,rows,reports,defaults])=>{
      if(!current)return;
      const choices=models.filter(m=>m.protocol==='openai_chat');setModels(choices);setModelId(choices[0]?.id);
      setDefaultPrompt(defaults.training_analysis||'');setPrompt(choices[0]?.chat_prompts?.training_analysis||defaults.training_analysis||'');setCandidates(rows.items);setTotal(rows.total);setHistory(reports);
      if(reports[0]){const latest=await getTrainingReport(run.id,reports[0].id);if(current){setReport(latest);setEvidence(latest.evidence);}}
    }).catch(cause=>{if(current)setError(errorText(cause));});
    return ()=>{current=false;};
  },[run.id]);
  const metrics=evidence?.current.metrics||[];
  const chosen=metrics.find(m=>`${m.file}:${m.name}`===metricKey)||metrics.find(m=>m.name.includes('ema_mAP_50_95'))||metrics.find(m=>m.name.toLowerCase().includes('map'))||metrics[0];
  const execute=async(analyze:boolean)=>{
    if(busy||analyze&&!modelId)return;
    setBusy(analyze?'analyze':'collect');setError('');
    try{
      if(analyze){const result=await analyzeTraining(run.id,compareIds,modelId!,prompt);setReport(result);setEvidence(result.evidence);setHistory(items=>[result,...items].slice(0,10));}
      else {setEvidence(await collectTrainingEvidence(run.id,compareIds));setReport(undefined);}
    }catch(cause){setError(errorText(cause));}finally{setBusy('');}
  };
  const more=async()=>{setBusy('more');try{const rows=await getTrainingComparisons(run.id,page+1);setCandidates(old=>[...old,...rows.items]);setPage(page+1);setTotal(rows.total);}catch(cause){setError(errorText(cause));}finally{setBusy('');}};
  const openReport=async(id:string)=>{setBusy('history');setError('');try{const row=await getTrainingReport(run.id,id);setReport(row);setEvidence(row.evidence);}catch(cause){setError(errorText(cause));}finally{setBusy('');}};
  return <Space orientation="vertical" size="middle" style={{width:'100%'}}>
    <Alert showIcon type="info" title="先查看指标，再让大模型分析" description="分析使用服务器已保存的指标；最多选择3个更早完成的模型。指标只读采集，分析调用已配置的大模型服务，不启动训练。报告保存后可重新打开。"/>
    <Typography.Text>历史模型（可跨训练器选择；条件不一致会显示对比限制）</Typography.Text>
    <Select aria-label="历史对比模型" mode="multiple" maxCount={3} allowClear showSearch optionFilterProp="label" style={{width:'100%'}} value={compareIds} disabled={!!busy} placeholder="不选则只分析当前训练；最多3个历史模型"
      options={candidates.map(row=>({value:row.id,label:`${row.name} · ${row.kind} · ${new Date(row.created_at).toLocaleString()}`}))}
      onChange={ids=>{setCompareIds(ids);setEvidence(undefined);setReport(undefined);}}/>
    {candidates.length<total&&<Button disabled={!!busy} onClick={()=>{void more();}}>加载更多历史模型（{candidates.length}/{total}）</Button>}
    {total===0&&<Typography.Text type="secondary">暂无更早完成的训练任务；失败、取消和运行中的任务不作为历史成品模型候选。</Typography.Text>}
    <Space wrap><Button aria-label="读取指标并对比" disabled={!!busy||run.status==='draft'} loading={busy==='collect'} onClick={()=>{void execute(false);}}>读取指标并对比</Button>
      <Link to="/models">配置分析大模型 / 默认提示词 →</Link></Space>
    {admin&&<>
      <Typography.Text>分析大模型</Typography.Text>
      <Select aria-label="训练分析大模型" style={{width:'100%'}} value={modelId} disabled={!!busy} placeholder="选择模型管理中已启用的对话模型" options={models.map(m=>({value:m.id,label:m.name}))} onChange={id=>{setModelId(id);setPrompt(models.find(m=>m.id===id)?.chat_prompts?.training_analysis||defaultPrompt);}}/>
      {!models.length&&<Alert type="warning" title="请先在模型管理中配置并启用OpenAI对话模型；原始指标仍可查看。"/>}
      <Collapse items={[{key:'prompt',label:'编辑本次训练分析提示词',children:<Input.TextArea aria-label="本次训练分析提示词" value={prompt} maxLength={8000} disabled={!!busy} autoSize={{minRows:4,maxRows:12}} onChange={e=>setPrompt(e.target.value)}/>} ]}/>
      <Button aria-label="大模型分析并保存报告" type="primary" loading={busy==='analyze'} disabled={!!busy||!modelId||!prompt.trim()||!metrics.length} onClick={()=>{void execute(true);}}>大模型分析并保存报告</Button>
    </>}
    {busy&&<Typography.Text type="secondary">{busy==='analyze'?'正在重新读取指标并调用大模型，请稍候…':busy==='collect'?'正在从各训练服务器读取指标…':'正在加载历史数据…'}</Typography.Text>}
    {error&&<Alert type="error" showIcon title={error}/>}
    {!!history.length&&<Select aria-label="已保存的训练分析报告" style={{width:'100%'}} value={report?.id} disabled={!!busy} placeholder="打开最近10次分析报告" options={history.map(r=>({value:r.id,label:`${new Date(r.created_at).toLocaleString()} · ${r.model.name}`}))} onChange={id=>{void openReport(id);}}/>}
    {report&&<Card title="大模型分析报告" size="small" styles={{body:{fontSize:14,lineHeight:1.75}}}>
      <Space orientation="vertical" style={{width:'100%'}}>
        <Typography.Text type="secondary">{report.model.name} · {report.model.model_name} · 配置版本 {report.model.revision} · {new Date(report.created_at).toLocaleString()}</Typography.Text>
        <Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{report.analysis.summary}</Typography.Paragraph>
        {([['findings','指标解读'],['recommendations','下一步建议'],['limitations','分析限制']] as const).map(([key,title])=><div key={key}><Typography.Title level={5}>{title}</Typography.Title><ul>{report.analysis[key].map((text,i)=><li key={i} style={{whiteSpace:'pre-wrap'}}>{text}</li>)}</ul></div>)}
        <Collapse items={[{key:'prompt',label:'查看本报告使用的提示词',children:<Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{report.prompt}</Typography.Paragraph>} ]}/>
      </Space>
    </Card>}
    {evidence&&<>
      <Typography.Text type="secondary">证据采集：{new Date(evidence.collected_at).toLocaleString()} · 当前 {evidence.current.name}{evidence.previous.map(r=>` / 对比 ${r.name}`).join('')}{report?' · 已保存快照':' · 本次读取'}</Typography.Text>
      {[...evidence.limitations,...[evidence.current,...evidence.previous].flatMap(r=>r.warnings.map(w=>r.name+'：'+w))].map((text,i)=><Alert key={i} type="warning" title={text}/>)}
      {!!metrics.length&&<>
        <Select aria-label="指标趋势选择" style={{width:'100%'}} value={chosen?`${chosen.file}:${chosen.name}`:undefined} options={metrics.map(m=>({value:`${m.file}:${m.name}`,label:`${m.name} · ${m.file}`}))} onChange={setMetricKey}/>
        {chosen&&<MetricCurve runs={[evidence.current,...evidence.previous]} metric={chosen}/>}
        <Table size="small" rowKey={m=>`${m.file}:${m.name}`} dataSource={metrics} pagination={{pageSize:10}} scroll={{x:850}} columns={[
          {title:'指标 / 来源',render:(_,m)=><><Typography.Text>{m.name}</Typography.Text><br/><Typography.Text type="secondary">{m.file}</Typography.Text></>},
          {title:'首次',dataIndex:'first',render:value},{title:'最后有效值',dataIndex:'last',render:value},{title:'各自最佳值',dataIndex:'best',render:value},
          {title:'最佳位置',render:(_,m)=>m.best_epoch!=null?`epoch ${m.best_epoch}`:m.best_step!=null?`step ${m.best_step}`:'—'},
          {title:'方向',render:(_,m)=>m.direction==='higher'?'越高越好':m.direction==='lower'?'越低越好':'参考项'},
        ]}/>
      </>}
      {evidence.comparisons.map(comparison=><Card key={comparison.run_id} title={`与 ${comparison.name} 对比`} size="small" extra={<Tag color={comparison.comparable?'blue':'orange'}>{comparison.comparable?'可作条件性对照':'需核对条件'}</Tag>}>
        <Space orientation="vertical" style={{width:'100%'}}>{comparison.warnings.map(text=><Alert key={text} type="warning" title={text}/>)}
          <Table size="small" rowKey={m=>`${m.file}:${m.name}`} scroll={{x:700}} pagination={{pageSize:8}} dataSource={comparison.metrics} columns={[
            {title:'同名指标',dataIndex:'name'},{title:'当前最后值',dataIndex:'current',render:value},{title:'历史最后值',dataIndex:'previous',render:value},
            {title:'最后值差 Δ',dataIndex:'last_delta',render:value},{title:'各自最佳值差 Δ',dataIndex:'best_delta',render:value},
          ]}/>
        </Space>
      </Card>)}
      <Collapse items={[{key:'evidence',label:'指标来源、SHA256与完整证据',children:<JsonView label="训练指标证据快照" value={evidence}/>} ]}/>
    </>}
  </Space>;
}

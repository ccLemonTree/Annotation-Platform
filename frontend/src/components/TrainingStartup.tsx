import { Alert, Space, Steps, Typography } from 'antd';
import type { TrainingRun } from '../api/training';

const labels:Record<string,string>={precheck:'重新预检查',stage:'准备任务目录',archive:'校验冻结包',upload:'传输冻结包',unpack:'远端解包与校验',container:'创建并启动容器'};

export default function TrainingStartup({run}:{run:TrainingRun}){
  const progress=run.inspection.startup;
  if(!progress)return <Alert type="info" title="此任务未记录后台启动阶段；请查看现有预检查、状态与训练日志，勿重复提交启动。"/>;
  const current=progress.state==='complete'?progress.steps.length:Math.max(0,progress.steps.indexOf(progress.phase));
  return <Space orientation="vertical" style={{width:'100%'}}>
    <Alert showIcon type={progress.state==='complete'?'success':['failed','interrupted'].includes(progress.state)?'error':'info'}
      title={progress.state==='complete'?'容器已启动':progress.state==='queued'?'后台已接收启动任务，正在排队':progress.state==='interrupted'?'启动准备中断，需要核对':progress.state==='failed'?'启动准备失败':progress.state==='canceled'?'启动准备已取消':'后台正在准备启动'}
      description="可关闭详情或刷新页面，之后重新打开任务查看进度。各阶段耗时不同，传输和逐图校验可能较长。"/>
    <Steps orientation="vertical" size="small" current={current} status={['failed','interrupted'].includes(progress.state)?'error':['queued','canceled'].includes(progress.state)?'wait':'process'}
      items={progress.steps.map(phase=>({title:labels[phase]||phase}))}/>
    <Typography.Title level={5}>启动记录</Typography.Title>
    {progress.events.map((event,index)=><Typography.Paragraph key={index} style={{marginBottom:4}}>
      {new Date(event.time).toLocaleString()} · {event.message}
    </Typography.Paragraph>)}
  </Space>;
}

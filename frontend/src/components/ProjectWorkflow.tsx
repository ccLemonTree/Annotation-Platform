import { Link, useSearchParams } from 'react-router-dom';
import type { Project } from '../api/projects';
import { useCurrentUser } from './currentUser';

export default function ProjectWorkflow({project,active}:{project:Project;active:'images'|'prepare'|'annotate'|'review'|'deliver'|'test'|'settings'}) {
  const base=`/projects/${project.id}`;
  const admin=useCurrentUser()?.role==='admin';
  const [params]=useSearchParams();
  const scope=new URLSearchParams();
  for(const key of ['work_job_id','image_kind','kind'])if(params.get(key))scope.set(key,params.get(key)!);
  const annotation=`/annotations?project_id=${project.id}${scope.size?'&'+scope:''}`;
  const stages=[
    {key:'images',label:'接入数据',to:`${base}/images`},
    {key:'prepare',label:'批量处理',to:`${base}/prepare`},
    {key:'review',label:'标注与审核',to:annotation},
    {key:'deliver',label:'导出与训练',to:`${base}/deliver${scope.size?'?'+scope:''}`},
    {key:'test',label:'测试与评估',to:`${base}/test`},
  ].filter(item=>admin||item.key==='review');
  return <section className="project-workflow" aria-label="项目工作流程">
    <div className="project-workflow-heading"><strong>{project.project_name}</strong>
      <div className="project-workflow-links">{admin&&<Link to={`${base}/settings`} aria-current={active==='settings'?'page':undefined}>项目设置</Link>}
        <Link to={admin?'/projects':'/annotations'}>{admin?'全部项目':'标注项目'}</Link></div>
    </div>
    <nav aria-label="项目导航">
      {stages.map((item,index)=><Link key={item.key} to={item.to} aria-current={(active==='annotate'?'review':active)===item.key?'page':undefined}>
        <span className="project-workflow-step" aria-hidden="true">{index+1}</span>{item.label}
      </Link>)}
    </nav>
  </section>;
}

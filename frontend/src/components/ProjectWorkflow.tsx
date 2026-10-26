import { Link } from 'react-router-dom';
import type { Project } from '../api/projects';
import ContinueAnnotation from './ContinueAnnotation';
import { useCurrentUser } from './currentUser';

export default function ProjectWorkflow({project,active,reviewSearch=''}:{project:Project;active:'images'|'prepare'|'annotate'|'review'|'deliver';reviewSearch?:string}) {
  const base=`/projects/${project.id}`;
  const admin=useCurrentUser()?.role==='admin';
  return <section className="project-workflow" aria-label="项目工作流程">
    <div className="project-workflow-heading"><strong>{project.project_name}</strong><Link to={admin?'/projects':'/annotations'}>{admin?'全部项目':'标注项目'}</Link></div>
    <nav aria-label="项目导航">
      {[
        {key:'images',label:'1 图片',to:`${base}/images`},
        {key:'prepare',label:'2 标注准备',to:`${base}/prepare`},
        {key:'annotate',label:admin?'3 开始标注':'开始标注'},
        {key:'review',label:admin?'4 审核':'审核',to:`/annotations?project_id=${project.id}`},
        {key:'deliver',label:'5 导出与训练',to:`${base}/deliver`},
      ].filter(item=>admin||['annotate','review'].includes(item.key)).map(item=>active===item.key?<span key={item.key} aria-current="page">{item.label}</span>:item.to?
        <Link key={item.key} to={item.to}>{item.label}</Link>:
        <ContinueAnnotation key={item.key} projectId={project.id} taskType={project.task_type} reviewSearch={reviewSearch}/>) }
    </nav>
  </section>;
}

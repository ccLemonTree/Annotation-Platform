import type {Project} from '../src/api/projects';

export function withCreatedProjects(items:Project[],created:Project[]){
  const ids=new Set(items.map(project=>project.id));
  return [...created.filter(project=>!ids.has(project.id)),...items];
}

export function openProjectAutomation(projectId:string){
  if(window.parent!==window)window.parent.postMessage({type:'smarteye:open-automation',projectId},location.origin);
  else window.location.assign(`/detection-query?${new URLSearchParams({project_id:projectId,automation:'1'})}`);
}

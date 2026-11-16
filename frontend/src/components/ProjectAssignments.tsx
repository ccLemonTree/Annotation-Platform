import { useEffect,useRef,useState } from 'react';
import { Alert,Button,Modal,Select,Typography } from 'antd';
import { getProjectAssignments,updateProjectAssignments,type Project } from '../api/projects';
import { getUsers } from '../api/users';
import type { User } from '../api/auth';

export default function ProjectAssignments({project,onClose}:{project:Project;onClose:()=>void}){
  const [users,setUsers]=useState<User[]>([]),[ids,setIds]=useState<number[]>([]),[loading,setLoading]=useState(true),[saving,setSaving]=useState(false),[error,setError]=useState(''),[reload,setReload]=useState(0);
  const writing=useRef(false);
  useEffect(()=>{let active=true;
    (async()=>{
      const [assignment,users]=await Promise.all([getProjectAssignments(project.id),(async()=>{
        const users:User[]=[];
        for(let page=1;;page++){
          const result=await getUsers(page);users.push(...result.items);
          if(users.length>=result.total||!result.items.length)break;
        }
        return users.filter(user=>user.is_active&&user.role!=='admin');
      })()]);
      if(active){setUsers(users);setIds(assignment.user_ids);}
    })().catch(()=>{if(active)setError('项目成员读取失败，请重试');}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[project.id,reload]);
  const save=async()=>{if(loading||writing.current)return;writing.current=true;setSaving(true);setError('');
    try{await updateProjectAssignments(project.id,ids);onClose();}catch{setError('项目分配保存失败，请重试');}finally{writing.current=false;setSaving(false);}
  };
  return <Modal open title={`分配项目 · ${project.project_name}`} onCancel={()=>{if(!writing.current)onClose();}} onOk={()=>void save()} okText="保存分配" cancelText="取消" confirmLoading={saving} okButtonProps={{'aria-label':'保存分配',disabled:loading||error==='项目成员读取失败，请重试'}}>
    <Typography.Paragraph>选中的普通用户可标注和审核此项目；清空成员后，普通用户将无法访问此项目。管理员始终可访问。</Typography.Paragraph>
    {error&&<Alert type="error" title={error} style={{marginBottom:16}} action={error==='项目成员读取失败，请重试'?<Button onClick={()=>{setLoading(true);setError('');setReload(value=>value+1);}}>重试读取</Button>:undefined}/>}
    <Select aria-label="项目成员" mode="multiple" showSearch optionFilterProp="label" allowClear style={{width:'100%'}} placeholder="选择可标注和审核的用户" loading={loading} disabled={loading||saving} value={ids} onChange={setIds} notFoundContent={loading?'正在读取用户…':'暂无可分配的启用普通用户'} options={users.map(user=>({value:user.id,label:user.full_name?`${user.full_name}（${user.username}）`:user.username}))}/>
  </Modal>;
}

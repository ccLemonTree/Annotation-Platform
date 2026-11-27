import {useEffect,useState} from 'react';
import {Alert,Button,Form,Input,Modal,Popconfirm,Progress,Select,Space,Table,Tag} from 'antd';
import {appMessage} from '../../src/ui/appFeedback';
import {managementApi,type ManagedPlatform,type PlatformCheckTask} from '../managementApi';

export default function PlatformManagementPage(){
  const [rows,setRows]=useState<ManagedPlatform[]>([]),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const [open,setOpen]=useState(false),[editing,setEditing]=useState<ManagedPlatform>(),[busy,setBusy]=useState('');
  const [query,setQuery]=useState(''),[page,setPage]=useState(1);
  const [connectionFilter,setConnectionFilter]=useState<'all'|'online'|'offline'>('all');
  const [batch,setBatch]=useState<PlatformCheckTask|null>(null);
  const checking=!!batch&&['pending','running','processing'].includes(batch.status);
  const [form]=Form.useForm();
  const read=()=>Promise.all([managementApi<ManagedPlatform[]>('/managed-platforms'),managementApi<PlatformCheckTask|null>('/managed-platforms/check-all')]);
  const load=async()=>{setLoading(true);try{const [rows,batch]=await read();setRows(rows);setBatch(batch);setError('');}catch(error){setError(String(error instanceof Error?error.message:error));}finally{setLoading(false);}};
  useEffect(()=>{void load();},[]);
  useEffect(()=>{
    if(!checking)return;
    let active=true,pending=false;
    const timer=setInterval(()=>{
      if(pending)return;pending=true;
      void read().then(([rows,batch])=>{if(active){setRows(rows);setBatch(batch);setError('');}})
        .catch(error=>{if(active)setError(error instanceof Error?error.message:String(error));}).finally(()=>{pending=false;});
    },2000);
    return()=>{active=false;clearInterval(timer);};
  },[checking]);
  const checkAll=async(stop=false)=>{
    setBusy('check-all');setError('');
    try{await managementApi(stop&&batch?`/tasks/${encodeURIComponent(batch.task_id)}/cancel`:'/managed-platforms/check-all','POST');await load();}
    catch(error){setError(error instanceof Error?error.message:String(error));}finally{setBusy('');}
  };
  const edit=(row?:ManagedPlatform)=>{setEditing(row);form.resetFields();form.setFieldsValue(row?{...row,password:''}:{state:'1'});setOpen(true);};
  const save=async()=>{
    const values=await form.validateFields();
    if(editing&&!values.password)delete values.password;
    if(!values.login_url)delete values.login_url;
    setBusy('save');
    try{await managementApi(`/managed-platforms${editing?`/${encodeURIComponent(editing.plat_id)}`:''}`,editing?'PUT':'POST',values);setOpen(false);await load();appMessage.success('平台已保存');}
    catch(error){setError(error instanceof Error?error.message:String(error));}finally{setBusy('');}
  };
  const action=async(row:ManagedPlatform,operation:'check'|'delete')=>{
    setBusy(row.plat_id);setError('');
    try{const result=await managementApi<ManagedPlatform>(`/managed-platforms/${encodeURIComponent(row.plat_id)}${operation==='check'?'/check':''}`,operation==='check'?'POST':'DELETE');await load();if(operation==='check'&&result?.connection_error)setError(`${row.platform_name}：${result.connection_error}`);}
    catch(error){setError(`${row.platform_name}：${error instanceof Error?error.message:String(error)}`);}finally{setBusy('');}
  };
  const term=query.trim().toLowerCase();
  const filtered=rows.filter(row=>(connectionFilter==='all'||(connectionFilter==='online'?row.connection_status==='online':row.connection_status!=='online'))&&[row.platform_name,row.base_url,row.plat_id].some(value=>String(value||'').toLowerCase().includes(term)));
  return <section className="smarteye-page">
    <header className="smarteye-page-header">
      <div className="smarteye-page-header-row">
        <div>
          <h1>平台管理</h1>
          <p>一键检测会检查全部平台的连接和登录，包括停用平台。</p>
        </div>
        <Space wrap>
          <Button onClick={load} loading={loading}>刷新平台</Button>
          <Button onClick={()=>checkAll()} loading={checking||busy==='check-all'} disabled={!!busy||checking}>一键检测所有平台</Button>
          <Button type="primary" disabled={!!busy||checking} onClick={()=>edit()}>新增平台</Button>
        </Space>
      </div>
    </header>
    <div className="smarteye-page-body">
    <div className="page-toolbar">
      <div className="page-toolbar-fields">
        <Input.Search aria-label="搜索平台" placeholder="平台名称、地址或标识" allowClear style={{width:320,maxWidth:'100%'}} value={query} onChange={event=>{setQuery(event.target.value);setPage(1);}}/>
        <Select aria-label="平台在线状态" style={{width:160,maxWidth:'100%'}} value={connectionFilter} onChange={value=>{setConnectionFilter(value);setPage(1);}} options={[{value:'all',label:'全部平台'},{value:'online',label:'在线平台'},{value:'offline',label:'不在线平台'}]}/>
      </div>
    </div>
    {batch&&<Alert style={{marginBottom:16}} showIcon type={batch.status==='failed'?'error':checking?'info':batch.status==='completed'&&!batch.error_count?'success':'warning'}
      title={checking?'正在检测所有平台':({completed:'检测完成',cancelled:'检测已停止',interrupted:'检测已中断',failed:'检测任务失败'} as Record<string,string>)[batch.status]||batch.status}
      description={<div><div>已检测 {batch.processed_count}/{batch.total_count} 个平台；在线 {batch.online_count} 个，连接失败 {batch.error_count} 个，未配置 {batch.unconfigured_count} 个</div>
        <Progress aria-label="平台检测进度" percent={batch.progress||0} size="small" status={checking?'active':batch.status==='failed'?'exception':'normal'}/>
        {(checking||batch.status==='cancelled')&&<small>最多同时检测2个平台；停止后不再检测后续平台，当前平台的登录流程需等待结束或超时。</small>}
        {batch.updated_at&&<div><small>最近更新：{new Date(batch.updated_at).toLocaleString('zh-CN')}</small></div>}
        {batch.status==='failed'&&<small>{batch.message}</small>}
      </div>} action={checking?<Button disabled={!!busy} onClick={()=>checkAll(true)}>停止检测</Button>:undefined}/>}
    {error&&<Alert type="error" showIcon title={error} style={{marginBottom:16}}/>}
    <Table rowKey="plat_id" dataSource={filtered} loading={loading} scroll={{x:1000}} pagination={{current:page,onChange:setPage,showTotal:total=>`共 ${total} 个平台`}} columns={[
      {title:'平台名称',dataIndex:'platform_name'}, {title:'服务地址',dataIndex:'base_url'},
      {title:'启用状态',dataIndex:'state',render:value=>value==='1'?'启用':'停用'},
      {title:'连接状态',render:(_,row)=><Space direction="vertical" size={0}><Tag color={row.connection_status==='online'?'success':row.connection_status==='error'?'error':'default'}>{({online:'在线',error:'连接失败',unconfigured:'未配置',unknown:'未检测',unchecked:'未检测'} as Record<string,string>)[row.connection_status]||row.connection_status}</Tag>{row.connection_error&&<span>{row.connection_error}</span>}<small>{row.checked_at?new Date(row.checked_at).toLocaleString('zh-CN'):'尚未检测'}</small></Space>},
      {title:'设备数',dataIndex:'device_count'},
      {title:'操作',render:(_,row)=><Space><Button size="small" onClick={()=>action(row,'check')} loading={busy===row.plat_id} disabled={!!busy||checking}>连接测试</Button><Button size="small" disabled={!!busy||checking} onClick={()=>edit(row)}>编辑平台</Button><Popconfirm title="删除此平台？" description="历史图片保留；关联定时任务须先停用或删除。" onConfirm={()=>action(row,'delete')}><Button size="small" danger disabled={!!busy||checking}>删除平台</Button></Popconfirm></Space>},
    ]}/>
    <Modal open={open} title={editing?'编辑平台':'新增平台'} okText="保存平台" cancelText="取消" confirmLoading={busy==='save'} onOk={save} onCancel={()=>setOpen(false)}>
      <Form form={form} layout="vertical">
        {!editing&&<Form.Item name="plat_id" label="平台标识" rules={[{required:true,whitespace:true,max:160}]}><Input maxLength={160}/></Form.Item>}
        <Form.Item name="platform_name" label="平台名称" rules={[{required:true,whitespace:true}]}><Input maxLength={200}/></Form.Item>
        <Form.Item name="base_url" label="服务地址" rules={[{required:true},{type:'url'}]}><Input placeholder="http://platform.example"/></Form.Item>
        <Form.Item name="login_url" label="登录路径" rules={[{pattern:/^\/(?!\/)/,message:'请输入以单个 / 开头的相对路径'}]}><Input placeholder="/api/oauth/anyTenant/login（留空使用默认值）"/></Form.Item>
        <Form.Item name="username" label="登录用户名" rules={[{required:true,whitespace:true}]}><Input autoComplete="off"/></Form.Item>
        <Form.Item name="password" label="平台密码" extra={editing?'留空保留已有密码':undefined} rules={editing?[]:[{required:true}]}><Input.Password autoComplete="new-password"/></Form.Item>
        <Form.Item name="state" label="启用状态"><Select options={[{value:'1',label:'启用'},{value:'0',label:'停用'}]}/></Form.Item>
      </Form>
    </Modal>
    </div>
  </section>;
}

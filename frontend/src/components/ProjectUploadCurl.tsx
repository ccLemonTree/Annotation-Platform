import {useState} from 'react';
import {Button,Modal,Radio,Space,Typography} from 'antd';
import apiClient from '../api/client';

export default function ProjectUploadCurl({projectId,detection}:{projectId:string;detection:boolean}){
  const [open,setOpen]=useState(false);
  const [issuing,setIssuing]=useState(false);
  const [token,setToken]=useState('');
  const [kind,setKind]=useState<'originals'|'crops'>('originals');
  const projectPath=`/api/projects/${projectId}`;
  const url=(path:string)=>new URL(apiClient.getUri({url:projectPath+path}),window.location.origin).href;
  const issue=async()=>{
    setIssuing(true);
    try{
      const response=await apiClient.post<{token:string}>(`${projectPath}/upload-keys`,{name:'外部服务上传'});
      setToken(response.data.token);
    }catch{/* The API client displays the error. */}
    finally{setIssuing(false);}
  };
  const close=()=>{if(!issuing){setOpen(false);setToken('');}};
  const curl=`curl --fail-with-body -X POST -H 'Authorization: Bearer ${token}'`;
  const examples=[
    ['上传图片',`${curl} -F 'image_kind=${kind}' -F 'files=@image.jpg' '${url('/ingest/images')}'`],
    ...['zip','tar'].map(format=>{
      const filename=`images.${format}`;
      const query=new URLSearchParams({filename,image_kind:kind});
      return [`上传 ${format.toUpperCase()}`,`${curl} -H 'Content-Type: application/octet-stream' --data-binary '@${filename}' '${url(`/ingest/archives?${query}`)}'`];
    }),
    ...(detection?[
      ['上传 YOLO 标签文件',`${curl} -F 'files=@labels/image.txt' '${url('/ingest/yolo-labels')}'`],
      ['上传 YOLO 标签 ZIP',`${curl} -F 'archive=@labels.zip' '${url('/ingest/yolo-labels')}'`],
    ]:[]),
  ];
  return <>
    <Button onClick={()=>setOpen(true)}>外部上传 API</Button>
    <Modal title="外部上传 API" open={open} onCancel={close} footer={<Button disabled={issuing} onClick={close}>关闭</Button>} width={720}>
      <Typography.Paragraph>命令会填入当前项目地址和项目上传密钥。新密钥有效期为 90 天，只在本次生成后显示，请妥善保存。</Typography.Paragraph>
      {!token?<Button type="primary" loading={issuing} onClick={()=>void issue()}>生成密钥与 curl 示例</Button>:
        <Space orientation="vertical" size="middle" style={{width:'100%'}}>
          <Radio.Group aria-label="上传图片类型" value={kind} onChange={event=>setKind(event.target.value)}
            options={[{value:'originals',label:'原图'},{value:'crops',label:'裁剪图'}]}/>
          {examples.map(([name,command])=><div key={name}>
            <Typography.Text strong>{name}</Typography.Text>
            <Typography.Paragraph copyable={{text:command,tooltips:[`复制${name}命令`,'已复制']}} style={{marginBottom:0}}>
              <code style={{overflowWrap:'anywhere'}}>{command}</code>
            </Typography.Paragraph>
          </div>)}
          <Typography.Text type="secondary">执行前替换本地文件名。单次可用多个 -F files 上传最多 16 张图片。压缩包返回 HTTP 202 后由后台解包入库。</Typography.Text>
          {detection&&<Typography.Text type="secondary">YOLO 标签匹配已入库的同名图片。文件夹内的 TXT 每次提交最多 16 个。类别表可放在 ZIP 内或另加 -F 'classes=@data.yaml'，也可留空后在标注页设置。</Typography.Text>}
        </Space>}
    </Modal>
  </>;
}

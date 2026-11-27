import {useState} from 'react';
import {Button,Checkbox,Modal,Radio,Space,Typography} from 'antd';
import apiClient from '../api/client';

export default function ProjectUploadCurl({projectId,detection}:{projectId:string;detection:boolean}){
  const [open,setOpen]=useState(false);
  const [issuing,setIssuing]=useState(false);
  const [token,setToken]=useState('');
  const [kind,setKind]=useState<'originals'|'crops'>('originals');
  const [annotationFormat,setAnnotationFormat]=useState<'none'|'json'|'yolo'>('none');
  const [cropFromBoxes,setCropFromBoxes]=useState(false);
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
  const cropBoxes=annotationFormat==='json'&&cropFromBoxes;
  const annotationFields=annotationFormat==='json'?" -F 'annotations=@annotations.json'"+(cropBoxes?" -F 'crop_from_boxes=true'":''):
    annotationFormat==='yolo'?" -F 'labels=@image.txt' -F 'classes=@classes.txt'":'';
  const archiveFilename='bundle.zip';
  const archiveQuery=new URLSearchParams({filename:archiveFilename,image_kind:kind});
  if(annotationFormat!=='none')archiveQuery.set('annotation_format',annotationFormat);
  if(cropBoxes)archiveQuery.set('crop_from_boxes','true');
  const examples=[
    ['上传文件',`${curl} -F 'image_kind=${kind}' -F 'files=@image.jpg'${annotationFields} '${url('/ingest/images')}'`],
    ['上传压缩包',`${curl} -H 'Content-Type: application/octet-stream' --data-binary '@${archiveFilename}' '${url(`/ingest/archives?${archiveQuery}`)}'`],
  ];
  return <>
    <Button onClick={()=>setOpen(true)}>外部上传 API</Button>
    <Modal title="外部上传 API" open={open} onCancel={close} footer={<Button disabled={issuing} onClick={close}>关闭</Button>} width={720}>
      <Typography.Paragraph>命令会填入当前项目地址和项目上传密钥。新密钥有效期为 90 天，只在本次生成后显示，请妥善保存。</Typography.Paragraph>
      {!token?<Button type="primary" loading={issuing} onClick={()=>void issue()}>生成密钥与 curl 示例</Button>:
        <Space orientation="vertical" size="middle" style={{width:'100%'}}>
          <Radio.Group aria-label={annotationFormat==='json'?'未匹配图片类型':'上传图片类型'} value={kind} onChange={event=>setKind(event.target.value)}
            options={[{value:'originals',label:'原图'},{value:'crops',label:'裁剪图'}]}/>
          <Radio.Group aria-label="上传标注格式" value={annotationFormat} onChange={event=>setAnnotationFormat(event.target.value)}
            options={[{value:'none',label:'仅图片'},{value:'json',label:'JSON 标注'},...(detection?[{value:'yolo',label:'YOLO 标注'}]:[])]}/>
          {annotationFormat==='json'&&<Checkbox checked={cropFromBoxes} onChange={event=>setCropFromBoxes(event.target.checked)}>
            根据原图检测框生成裁剪图
          </Checkbox>}
          {examples.map(([name,command])=><div key={name}>
            <Typography.Text strong>{name}</Typography.Text>
            <Typography.Paragraph copyable={{text:command,tooltips:[`复制${name}命令`,'已复制']}} style={{marginBottom:0}}>
              <code style={{overflowWrap:'anywhere'}}>{command}</code>
            </Typography.Paragraph>
          </div>)}
          <Typography.Text type="secondary">支持 ZIP、TAR、TAR.GZ 和 TGZ。请将命令里的两处 {archiveFilename} 换成实际压缩包文件名。</Typography.Text>
          <Typography.Text type="secondary">每次最多上传 16 张图片。补传标注时可省略 files，压缩包也可只包含标注。HTTP 202 表示已受理，使用响应中的 Location 查询结果。</Typography.Text>
          {annotationFormat==='json'&&<Typography.Text type="secondary">按文件名匹配 JSON，列表对应原图，对象对应裁剪图。上方图片类型用于未匹配的图片。压缩包内可放一个 annotations.json。</Typography.Text>}
          {annotationFormat==='yolo'&&<Typography.Text type="secondary">YOLO 按不含扩展名的文件名匹配，每次最多 16 个 TXT。类别表可放在压缩包内，或者省略后在标注页设置。</Typography.Text>}
        </Space>}
    </Modal>
  </>;
}

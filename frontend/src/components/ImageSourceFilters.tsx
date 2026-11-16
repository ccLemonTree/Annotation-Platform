import {useEffect,useState} from 'react';
import {Input,Select,Space,Typography} from 'antd';
import {getImageSourcePlatforms,type ImageFilters} from '../api/images';

export default function ImageSourceFilters({value,onChange,disabled,projectId}:{value:ImageFilters;onChange:(filters:ImageFilters)=>void;disabled?:boolean;projectId?:string}){
  const [platforms,setPlatforms]=useState<string[]>([]),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const [device,setDevice]=useState(value.device_name||'');
  useEffect(()=>{setDevice(value.device_name||'');},[value.device_name]);
  useEffect(()=>{setPlatforms([]);setError('');},[value.collection_id,projectId]);
  const load=async()=>{
    setLoading(true);setError('');
    try{setPlatforms(await getImageSourcePlatforms(value.collection_id,projectId));}
    catch{setError('平台列表读取失败，请重新展开重试');}
    finally{setLoading(false);}
  };
  return <Space wrap>
    <Select aria-label="图片来源平台" placeholder="全部平台" allowClear showSearch optionFilterProp="label" style={{width:210}}
      value={value.platform_name} disabled={disabled} loading={loading} onOpenChange={open=>{if(open)void load();}}
      options={platforms.map(name=>({value:name,label:name==='@upload'?'本地上传 / 未记录平台':name}))}
      onChange={platform_name=>{setDevice('');onChange({...value,platform_name,device_name:undefined});}}/>
    <Input.Search aria-label="图片来源设备" placeholder="设备名称或 deviceId" allowClear value={device} disabled={disabled} maxLength={200} style={{width:230}}
      onChange={event=>{setDevice(event.target.value);if(!event.target.value)onChange({...value,device_name:undefined});}}
      onSearch={name=>onChange({...value,device_name:name.trim()||undefined})}/>
    {error&&<Typography.Text type="danger">{error}</Typography.Text>}
  </Space>;
}

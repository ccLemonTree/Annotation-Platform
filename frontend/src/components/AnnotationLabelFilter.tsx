import {useState} from 'react';
import {Select} from 'antd';
import type {Project} from '../api/projects';

export default function AnnotationLabelFilter({schema,value,onChange,disabled}:{
  schema:Project['label_schema']|undefined;value?:string;onChange:(value?:string)=>void;disabled?:boolean;
}){
  const [search,setSearch]=useState('');
  const labels=new Set([schema?.labels,schema?.imported_box_labels].flatMap(values=>
    Array.isArray(values)?values.filter((label):label is string=>typeof label==='string'&&!!label):[]));
  if(value)labels.add(value);
  const query=search.trim();
  if(query&&query.length<=100)labels.add(query);
  return <Select aria-label="筛选标注标签" placeholder="全部标注标签" value={value}
    allowClear showSearch={{searchValue:search,onSearch:setSearch,optionFilterProp:'label'}}
    onOpenChange={open=>{if(!open)setSearch('');}}
    onChange={label=>{setSearch('');onChange(label);}} disabled={disabled}
    options={[...labels].map(label=>({value:label,label}))} style={{width:180,maxWidth:'100%'}}/>;
}

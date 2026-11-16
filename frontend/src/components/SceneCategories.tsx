import {Button,Input,Select,Space,TreeSelect} from 'antd';
import {useEffect,useState} from 'react';
import {getImageIngestSettings} from '../api/images';
import type {ImageCollection,ImageIngestConfig,SceneGroup} from '../api/images';

export const configSceneGroups=(config:Pick<ImageIngestConfig,'scenes'|'scene_groups'>):SceneGroup[]=>config.scene_groups||[{name:'其他场景',scenes:config.scenes||[]}];
export function collectionSceneGroups(collections:ImageCollection[],collectionId?:string,fallback:SceneGroup[]=[]){
  const groups:SceneGroup[]=[];
  const configured=collections.filter(item=>!collectionId||item.id===collectionId).flatMap(collection=>collection.ingest_config?configSceneGroups(collection.ingest_config):[]);
  for(const group of [...configured,...fallback]){
    let target=groups.find(item=>item.name===group.name);
    if(!target){target={name:group.name,scenes:[]};groups.push(target);}
    target.scenes.push(...group.scenes.filter(scene=>!target.scenes.includes(scene)));
  }
  return groups;
}
export function useSceneGroups(collections:ImageCollection[],collectionId?:string){
  const [global,setGlobal]=useState<SceneGroup[]>([]);
  useEffect(()=>{let active=true;getImageIngestSettings().then(config=>{if(active)setGlobal(configSceneGroups(config));}).catch(()=>{});return()=>{active=false;};},[]);
  return collectionSceneGroups(collections,collectionId,global);
}
export const sceneLabel=(scene:string,groups:SceneGroup[])=>{
  const group=groups.find(group=>group.scenes.includes(scene));return group?`${group.name} / ${scene}`:scene;
};
export const sceneResultStatus=(status:string,labels?:string[])=>status==='completed'&&!labels?.length?'unmatched':status;
export function sceneTree(groups:SceneGroup[],scenes:string[],parentsSelectable=true){
  const seen=new Set<string>();
  const nodes=groups.map(group=>({value:'@group:'+group.name,title:group.name,label:group.name,selectable:parentsSelectable,
    children:group.scenes.filter(scene=>{if(seen.has(scene))return false;seen.add(scene);return true;}).map(scene=>({value:scene,title:scene,label:`${group.name} / ${scene}`}))}));
  const legacy=[...new Set(scenes)].filter(scene=>!seen.has(scene));
  if(legacy.length)nodes.push({value:'@history',title:'历史 / 未分组场景',label:'历史 / 未分组场景',selectable:false,children:legacy.map(scene=>({value:scene,title:scene,label:scene}))});
  return nodes;
}
export function SceneFilter({groups,scenes,value,onChange,label,disabled}:{groups:SceneGroup[];scenes:string[];value?:string;onChange:(value:string|undefined)=>void;label:string;disabled?:boolean}){
  return <TreeSelect aria-label={label} placeholder="全部场景" allowClear showSearch treeDefaultExpandAll treeNodeFilterProp="label" treeNodeLabelProp="label" disabled={disabled}
    style={{width:200}} value={value} onChange={onChange} treeData={sceneTree(groups,scenes)}/>;
}
export function validateSceneGroups(groups:SceneGroup[]){
  if(!groups?.length||groups.length>30)throw Error('请配置1至30个场景大类');
  const names=new Set<string>(),scenes=new Set<string>();
  for(const group of groups){
    const name=group.name.trim();
    if(!name||name.length>40||names.has(name))throw Error('大类名称须为1至40字符，且不能重复');
    names.add(name);
    if(!group.scenes.length)throw Error(`请为“${name}”添加具体场景`);
    for(const raw of group.scenes){
      const scene=raw.trim();
      if(!scene||scene.length>60||scene.startsWith('@group:')||scenes.has(scene))throw Error('具体场景须为1至60字符，同名场景只能归属一个大类');
      scenes.add(scene);
    }
  }
  if(scenes.size>100)throw Error('具体场景总数最多100个');
}
export const sceneGroupFields=(groups:SceneGroup[])=>({scene_groups:groups.map(group=>({name:group.name.trim(),scenes:group.scenes.map(scene=>scene.trim())})),scenes:groups.flatMap(group=>group.scenes.map(scene=>scene.trim()))});
export function SceneCategoryEditor({value=[],onChange,disabled}:{value?:SceneGroup[];onChange?:(value:SceneGroup[])=>void;disabled?:boolean}){
  const change=(index:number,group:SceneGroup)=>onChange?.(value.map((item,i)=>i===index?group:item));
  return <Space orientation="vertical" style={{width:'100%'}}>
    {value.map((group,index)=><div key={index} style={{border:'1px solid var(--ant-color-border)',borderRadius:8,padding:12}}>
      <Space.Compact style={{width:'100%',marginBottom:8}}><Input aria-label={`场景大类名称 ${index+1}`} placeholder="大类名称，例如交通场景" maxLength={40} disabled={disabled} value={group.name} onChange={event=>change(index,{...group,name:event.target.value})}/>
        <Button danger disabled={disabled} aria-label={`删除场景大类 ${index+1}`} onClick={()=>onChange?.(value.filter((_,i)=>i!==index))}>删除大类</Button></Space.Compact>
      <Select aria-label={`具体场景 ${index+1}`} placeholder="输入具体场景后回车，例如道路、路口" mode="tags" tokenSeparators={[',','，']} style={{width:'100%'}} value={group.scenes} disabled={disabled} onChange={scenes=>change(index,{...group,scenes})}/>
    </div>)}
    <Button disabled={disabled||value.length>=30} onClick={()=>onChange?.([...value,{name:'',scenes:[]}])}>添加场景大类</Button>
  </Space>;
}

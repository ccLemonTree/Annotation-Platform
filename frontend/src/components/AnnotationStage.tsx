import {createContext,useContext,useEffect,useState} from 'react';
import type {ReactNode} from 'react';
import {Button} from 'antd';
import {FullscreenExitOutlined,FullscreenOutlined} from '@ant-design/icons';
import './AnnotationStage.css';

const FullscreenContext=createContext({fullscreen:false,toggle:()=>{}});

export function AnnotationFullscreenButton(){
  const {fullscreen,toggle}=useContext(FullscreenContext);
  return <Button icon={fullscreen?<FullscreenExitOutlined/>:<FullscreenOutlined/>} aria-label={fullscreen?'退出全屏':'全屏标注'} aria-pressed={fullscreen} onClick={toggle} title={fullscreen?'Esc 退出全屏标注':'隐藏外围导航，扩大标注空间'}>{fullscreen?'退出全屏':'全屏标注'}</Button>;
}

export default function AnnotationStage({children}:{children:ReactNode}){
  const [fullscreen,setFullscreen]=useState(false);
  useEffect(()=>{
    if(!fullscreen)return;
    const previous=document.body.style.overflow;document.body.style.overflow='hidden';
    const escape=(event:KeyboardEvent)=>{
      if(event.key!=='Escape'||event.defaultPrevented||event.isComposing)return;
      const overlay=[...document.querySelectorAll('[role="dialog"],.ant-image-preview,.ant-select-dropdown,.ant-popover,.ant-drawer')]
        .some(element=>element.getClientRects().length>0&&getComputedStyle(element).visibility!=='hidden');
      if(overlay)return;
      event.preventDefault();event.stopPropagation();setFullscreen(false);
    };
    window.addEventListener('keydown',escape,true);
    return()=>{document.body.style.overflow=previous;window.removeEventListener('keydown',escape,true);};
  },[fullscreen]);
  return <FullscreenContext.Provider value={{fullscreen,toggle:()=>setFullscreen(value=>!value)}}><section className={`annotation-stage${fullscreen?' annotation-stage-fullscreen':''}`} aria-label="标注工作区">{children}</section></FullscreenContext.Provider>;
}

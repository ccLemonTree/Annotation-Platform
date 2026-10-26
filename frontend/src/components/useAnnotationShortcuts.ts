import { useLayoutEffect } from 'react';

export default function useAnnotationShortcuts(handle:(event:KeyboardEvent)=>void,enabled:boolean,saveInInput=false){
  useLayoutEffect(()=>{
    if(!enabled)return;
    const keydown=(event:KeyboardEvent)=>{
      if(event.defaultPrevented||event.repeat||event.isComposing||event.altKey)return;
      const overlay=[...document.querySelectorAll('[role="dialog"],.ant-image-preview,.ant-select-dropdown,.ant-popover,.ant-drawer')]
        .some(element=>element.getClientRects().length>0&&getComputedStyle(element).visibility!=='hidden');
      if(overlay)return;
      const target=event.target instanceof Element?event.target:null;
      const editing=target?.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="combobox"],[role="radio"],[role="checkbox"],.ant-select');
      const saving=(event.ctrlKey||event.metaKey)&&!event.shiftKey&&event.key.toLowerCase()==='s';
      if(editing&&!(saveInInput&&saving))return;
      handle(event);
    };
    window.addEventListener('keydown',keydown);return()=>window.removeEventListener('keydown',keydown);
  },[handle,enabled,saveInInput]);
}

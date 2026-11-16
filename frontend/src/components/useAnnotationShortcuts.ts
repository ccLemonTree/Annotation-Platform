import { useLayoutEffect } from 'react';

export default function useAnnotationShortcuts(handle:(event:KeyboardEvent,key:string)=>void,enabled:boolean,saveInInput=false){
  useLayoutEffect(()=>{
    if(!enabled)return;
    const keydown=(event:KeyboardEvent)=>{
      const target=event.target instanceof Element?event.target:null;
      // IMEs may report the physical R key as Process when a view control has focus.
      const focusedReview=!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey&&
        (event.key.toLowerCase()==='r'||event.code==='KeyR')&&target?.closest('[data-annotation-skip],[data-annotation-view]');
      if(event.defaultPrevented||event.repeat||(event.isComposing&&!focusedReview)||event.altKey)return;
      const overlay=[...document.querySelectorAll('[role="dialog"],.ant-image-preview,.ant-select-dropdown,.ant-popover,.ant-drawer')]
        .some(element=>element.getClientRects().length>0&&getComputedStyle(element).visibility!=='hidden');
      if(overlay)return;
      const editing=target?.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="combobox"],[role="radio"],[role="checkbox"],.ant-select');
      const saving=(event.ctrlKey||event.metaKey)&&!event.shiftKey&&event.key.toLowerCase()==='s';
      if(editing&&!(saveInInput&&saving)&&!focusedReview)return;
      handle(event,focusedReview?'r':event.key.toLowerCase());
    };
    window.addEventListener('keydown',keydown);return()=>window.removeEventListener('keydown',keydown);
  },[handle,enabled,saveInInput]);
}

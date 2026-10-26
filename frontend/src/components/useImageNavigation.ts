import { useCallback, useEffect, useState } from 'react';

export default function useImageNavigation<T extends {id:string}>({scope,items,page,pageSize,total,onPage,enabled,modal=false}:{
  scope:string;items?:T[];page:number;pageSize:number;total:number;onPage:(page:number)=>void;enabled:boolean;modal?:boolean;
}) {
  const [selection,setSelection]=useState<{scope:string;id?:string;pendingPage?:number;lastPage?:number}>({scope});
  const waiting=selection.scope===scope&&selection.pendingPage!==undefined&&selection.pendingPage!==page;
  const item=waiting?undefined:items?.find(row=>selection.scope===scope&&row.id===selection.id)
    ||(selection.scope===scope&&selection.lastPage===page?items?.at(-1):items?.[0]);
  const index=item?items!.indexOf(item):-1;
  const position=(page-1)*pageSize+index;
  const canPrevious=!!item&&position>0,canNext=!!item&&position+1<total;
  useEffect(()=>{if(item&&(selection.scope!==scope||selection.id!==item.id))setSelection({scope,id:item.id});},[item,scope,selection]);
  const select=(id:string)=>setSelection({scope,id});
  const move=useCallback((direction:number)=>{
    if(!item||(direction<0?!canPrevious:!canNext))return;
    const next=items?.[index+direction];
    if(next)setSelection({scope,id:next.id});
    else{
      const nextPage=page+direction;
      setSelection({scope,pendingPage:nextPage,lastPage:direction<0?nextPage:undefined});onPage(nextPage);
    }
  },[item,items,index,page,scope,canPrevious,canNext,onPage]);
  useEffect(()=>{
    if(!enabled||!item)return;
    const keydown=(event:KeyboardEvent)=>{
      if(event.defaultPrevented||event.isComposing||event.repeat||event.ctrlKey||event.altKey||event.metaKey||event.shiftKey)return;
      if(event.key!=='ArrowLeft'&&event.key!=='ArrowRight')return;
      const target=event.target instanceof Element?event.target:null;
      if(target?.closest('input,textarea,select,[role="combobox"],[contenteditable]:not([contenteditable="false"]),.ant-select'))return;
      if(!modal&&document.querySelector('.ant-modal-wrap:not([style*="display: none"]),.ant-popover:not(.ant-popover-hidden)'))return;
      event.preventDefault();move(event.key==='ArrowLeft'?-1:1);
    };
    window.addEventListener('keydown',keydown);return()=>window.removeEventListener('keydown',keydown);
  },[enabled,item,modal,move]);
  return {item,position:position+1,select,previous:()=>move(-1),next:()=>move(1),canPrevious,canNext};
}

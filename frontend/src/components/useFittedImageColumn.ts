import {useCallback} from 'react';

export default function useFittedImageColumn(){
  return useCallback((grid:HTMLDivElement|null)=>{
    if(!grid)return;
    const column=grid.firstElementChild as HTMLElement;
    let frame=0,observedImage:SVGSVGElement|null=null;
    const fit=()=>{
      const image=column.querySelector<SVGSVGElement>('.workspace-canvas');if(!image)return;
      if(image!==observedImage){if(observedImage)resize.unobserve(observedImage);resize.observe(image);observedImage=image;}
      if(window.innerWidth<992){grid.style.removeProperty('min-height');image.style.width='100%';return;}
      const before=image.getBoundingClientRect().top-column.getBoundingClientRect().top;
      let after=0;
      for(let node:Element|null=image;node&&node!==column;node=node.parentElement){
        for(let sibling=node.nextElementSibling;sibling;sibling=sibling.nextElementSibling){
          const style=getComputedStyle(sibling);
          after+=sibling.getBoundingClientRect().height+(parseFloat(style.marginTop)||0)+(parseFloat(style.marginBottom)||0);
        }
      }
      const minimumHeight=before+after+160;
      if(Math.abs(parseFloat(grid.style.minHeight)-minimumHeight)>.5||!grid.style.minHeight)grid.style.minHeight=`${minimumHeight}px`;
      const view=image.viewBox.baseVal;
      const fitted=Math.max(1,Math.min(column.clientWidth,(grid.clientHeight-before-after)*view.width/view.height));
      if(Math.abs(image.getBoundingClientRect().width-fitted)>.5)image.style.width=`${fitted}px`;
    };
    const schedule=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(fit);};
    const resize=new ResizeObserver(schedule);resize.observe(grid);resize.observe(column);
    const children=new MutationObserver(schedule);children.observe(column,{childList:true,subtree:true});
    schedule();
    return()=>{resize.disconnect();children.disconnect();cancelAnimationFrame(frame);};
  },[]);
}

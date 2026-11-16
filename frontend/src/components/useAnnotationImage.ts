import {useEffect,useState} from 'react';
import apiClient from '../api/client';

function useImageBlob(url?:string,attempt=0){
  const [result,setResult]=useState<{url:string;attempt:number;src?:string;error?:boolean}>();
  useEffect(()=>{
    setResult(undefined);
    if(!url)return;
    const controller=new AbortController();
    let objectUrl:string|undefined;
    async function load(requestUrl:string){
      try{
        const response=await apiClient.get(requestUrl,{responseType:'blob',signal:controller.signal,skipErrorToast:true});
        if(controller.signal.aborted)return;
        objectUrl=URL.createObjectURL(response.data);
        const image=new Image();image.src=objectUrl;await image.decode();
        if(!controller.signal.aborted)setResult({url:requestUrl,attempt,src:objectUrl});
      }catch{
        if(!controller.signal.aborted)setResult({url:requestUrl,attempt,error:true});
      }
    }
    void load(url);
    return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[url,attempt]);
  return result?.url===url&&result?.attempt===attempt?result:undefined;
}

export default function useAnnotationImage(url?:string){
  const [request,setRequest]=useState<{url:string;attempt:number}>();
  const requested=!!url&&request?.url===url;
  const preview=useImageBlob(url?`${url}${url.includes('?')?'&':'?'}preview=true`:undefined);
  const full=useImageBlob(requested?url:undefined,request?.attempt);
  const loadFull=()=>{
    if(url&&(!requested||full?.error))setRequest({url,attempt:(request?.attempt??0)+1});
  };
  return {src:full?.src||preview?.src,previewError:preview?.error,fullError:full?.error,
    fullLoading:requested&&!full,loadFull};
}

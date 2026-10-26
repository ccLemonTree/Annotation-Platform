import './JsonView.css';
import { formatJson } from './formatJson';

export default function JsonView({value,label='JSON 数据'}:{value:unknown;label?:string}){
  if(typeof value==='string'){
    try{JSON.parse(value);}catch{return <span style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{value}</span>;}
  }
  return <pre className="json-view" aria-label={label} tabIndex={0}><code>{formatJson(value)}</code></pre>;
}

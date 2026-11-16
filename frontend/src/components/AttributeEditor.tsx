import {AutoComplete,Button,Checkbox,Input,Select,Typography} from 'antd';
import type {AttributeSchema,AttributeValues} from '../api/objectAttributes';
import AttributeProbabilities from './AttributeProbabilities';
import './AttributeEditor.css';

const unknownOptions=new Set(['unknown','none','null','n/a','未知','无法判断','不确定','看不清','未识别','无','未提供']);

export default function AttributeEditor({schema,values,disabled,onChange,suggestions={},preannotations={}}:{schema:AttributeSchema;values:AttributeValues;disabled:boolean;onChange:(values:AttributeValues)=>void;suggestions?:Record<string,string[]>;preannotations?:AttributeValues}){
  return <div className="attribute-editor">{schema.fields.map(field=>{
    const item=values[field.key]||{value:null,reason:''};
    const change=(changes:Partial<typeof item>)=>onChange({...values,[field.key]:{...item,...changes}});
    const choose=(value:string)=>{
      if(unknownOptions.has(value.toLowerCase()))change({value:null,reason:item.value===null?item.reason:''});
      else change({value,...(item.value===null?{reason:''}:{})});
    };
    const probability=(option:string)=>item.probabilities?.[option];
    const caption=(option:string)=>probability(option)===undefined?option:`${option} ${(probability(option)!*100).toFixed(1)}%`;
    const candidates=item.probabilities?[...field.options].sort((a,b)=>(probability(b)??0)-(probability(a)??0)).slice(0,3):field.options.length<=3?field.options:[];
    const predicted=preannotations[field.key];
    const options=[...new Set([...(predicted?.value?[predicted.value]:[]),...(item.value?[item.value]:[]),...(suggestions[field.key]||[])])].filter(value=>!unknownOptions.has(value.toLowerCase()));
    const reason=<Input aria-label={`${field.name}判断原因`} placeholder={item.value===null?'无法判断原因（必填）':'可见证据 / 修正说明（可选）'} disabled={disabled} value={item.reason} maxLength={500} onChange={event=>change({reason:event.target.value})}/>;
    return <div key={field.key} className="attribute-editor-row">
      <div className="attribute-editor-main"><Typography.Text strong title={field.instruction}>{field.name}</Typography.Text>
        {field.options.length?<Select aria-label={`${field.name}属性值`} disabled={disabled} showSearch optionFilterProp="label" value={item.value||undefined} placeholder={item.value===null?'无法判断':'选择属性值'} options={field.options.map(value=>({value,label:caption(value)}))} onChange={choose}/>:<AutoComplete disabled={disabled} value={item.value??''} options={options.map(value=>({value}))} defaultActiveFirstOption={false} filterOption={(input,option)=>String(option?.value).toLowerCase().includes(input.toLowerCase())} onChange={choose}><Input aria-label={`${field.name}属性值`} maxLength={500} placeholder="选择已有值或输入新值"/></AutoComplete>}
        <Checkbox aria-label={`${field.name}无法判断`} disabled={disabled} checked={item.value===null} onChange={event=>change({value:event.target.checked?null:'',reason:''})}>无法判断</Checkbox>
      </div>
      {predicted?.value&&(!field.options.length||field.options.includes(predicted.value))&&<Button className="attribute-editor-preannotation" size="small" disabled={disabled} title="采用当前图片的模型预标注值" onClick={()=>change({value:predicted.value,reason:predicted.reason})}>预标注：{predicted.value}</Button>}
      {!!candidates.length&&<div className="attribute-editor-candidates">{candidates.map(option=><Button key={option} size="small" aria-label={`${field.name}选择${option}`} aria-pressed={item.value===option} type={item.value===option?'primary':'default'} disabled={disabled} onClick={()=>choose(option)}>{caption(option)}</Button>)}</div>}
      {item.value===null&&reason}
      <div className="attribute-editor-details"><details><summary>{item.value===null?'属性说明':'说明 / 修正原因'}</summary>{field.instruction&&<Typography.Paragraph type="secondary">{field.instruction}</Typography.Paragraph>}{item.value!==null&&reason}</details><AttributeProbabilities probabilities={item.probabilities}/></div>
    </div>;
  })}</div>;
}

import { Input } from 'antd';
import type { TextAreaProps } from 'antd/es/input/TextArea';
import { formatJson } from './formatJson';
import './JsonView.css';

export default function JsonInput({value,onChange,onBlur,...props}:Omit<TextAreaProps,'value'|'onChange'>&{value?:string;onChange?:(value:string)=>void}){
  return <Input.TextArea {...props} className={['json-input',props.className].filter(Boolean).join(' ')} value={value} onChange={event=>onChange?.(event.target.value)}
    onBlur={event=>{if(value&&!props.readOnly&&!props.disabled)onChange?.(formatJson(value));onBlur?.(event);}}/>;
}

import {Space,Tag,Typography} from 'antd';

export default function AttributeProbabilities({probabilities}:{probabilities?:Record<string,number>}){
  if(!probabilities)return null;
  return <details><summary>查看模型原始候选概率</summary><Space wrap style={{marginTop:8}}>
    {Object.entries(probabilities).sort((a,b)=>b[1]-a[1]).map(([option,probability])=><Tag key={option} title={String(probability)}>{option}：{(probability*100).toFixed(2)}%</Tag>)}
  </Space><Typography.Paragraph type="secondary">仅供人工核验，不代表已验证正确率；人工修正属性不会改变这些概率。</Typography.Paragraph></details>;
}

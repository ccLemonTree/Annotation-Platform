import apiClient from './client';
export interface AttributeField {key:string;name:string;instruction:string;options:string[]}
export interface AttributeSchema {target_label:string|string[];fields:AttributeField[]}
export const attributeTargets=(schema:AttributeSchema):string[]=>Array.isArray(schema.target_label)?schema.target_label:[schema.target_label];
export interface AttributeTemplate {id:string;name:string;config:AttributeSchema}
export const getAttributeTemplates=async():Promise<AttributeTemplate[]> =>(await apiClient.get('/api/attribute-templates')).data;
export const getCropLabels=async(projectId:string):Promise<string[]> =>(await apiClient.get(`/api/projects/${projectId}/crop-labels`)).data;
export const saveAttributeTemplate=async(name:string,config:AttributeSchema):Promise<AttributeTemplate> =>(await apiClient.post('/api/attribute-templates',{name,config})).data;
export const updateAttributeTemplate=async(id:string,name:string,config:AttributeSchema):Promise<AttributeTemplate> =>(await apiClient.put('/api/attribute-templates/'+id,{name,config})).data;
export const resolvedAttributeTemplates=(rows:AttributeTemplate[])=>[...builtinAttributeTemplates.map(template=>rows.find(row=>row.id===template.id)??template),...rows.filter(row=>!row.id.startsWith('builtin_'))];
export const deleteAttributeTemplate=async(id:string):Promise<void> =>{await apiClient.delete('/api/attribute-templates/'+id);};
export interface AttributeValue {value:string|null;reason:string;probabilities?:Record<string,number>}
export type AttributeValues=Record<string,AttributeValue>;
export const getAttributeOptions=async(projectId:string):Promise<Record<string,string[]>>=>(await apiClient.get(`/api/projects/${projectId}/attribute-options`)).data;
export interface PreannotationExample {asset_id:string;original_name:string;annotation_version:number;task_type?:string;attributes?:AttributeValues;attribute_schema?:AttributeSchema;compatible:boolean;boxes?:{label:string;x:number;y:number;width:number;height:number}[];width?:number;height?:number}
export interface AttributeFilters {attribute_key?:string;attribute_value?:string;attribute_state?:'all'|'analyzed'|'known'|'unknown'|'missing';review_status?:string}
export const vehicleAttributes:AttributeSchema={target_label:'car',fields:[
  {key:'plate',name:'车牌',instruction:'只填写完整清晰的车牌号码；不补猜模糊、遮挡或不可见字符',options:[]},
  {key:'plate_type',name:'车牌类型',instruction:'车牌制式或类型；看不清或无法确定时留空',options:[]},
  {key:'plate_color',name:'车牌颜色',instruction:'车牌底色，无法看清时留空',options:[]},
  {key:'vehicle_type',name:'车辆类型',instruction:'按可见外形判断车辆类型',options:[]},
  {key:'body_color',name:'车身颜色',instruction:'车辆主要车身颜色，注意光照影响',options:[]},
  {key:'brand',name:'车辆品牌',instruction:'仅依据清晰可辨的标志或文字判断品牌，不靠外形猜测',options:[]},
  {key:'orientation',name:'车身朝向',instruction:'车辆相对于摄像头的可见朝向',options:['车头','车尾','左侧','右侧','左前','右前','左后','右后']},
]};
export const builtinAttributeTemplates:AttributeTemplate[]=[
  {id:'builtin_vehicle',name:'车辆',config:vehicleAttributes},
  {id:'builtin_person',name:'人员',config:{target_label:'person',fields:[
    {key:'upper_clothing',name:'上身服饰',instruction:'描述清晰可见的上身衣物类型',options:[]},
    {key:'upper_color',name:'上衣颜色',instruction:'主要上衣颜色，注意光照影响',options:[]},
    {key:'lower_clothing',name:'下身服饰',instruction:'描述清晰可见的裤子或裙子类型',options:[]},
    {key:'lower_color',name:'下装颜色',instruction:'主要裤子或裙子颜色',options:[]},
    {key:'headwear',name:'头部穿戴',instruction:'仅填写可见的帽子、头盔等；无法看清时留空',options:[]},
    {key:'carried_items',name:'携带物品',instruction:'描述可见的背包、手提包或手持物品；遮挡时不猜测',options:[]},
    {key:'posture',name:'姿态',instruction:'按可见姿态判断站立、坐姿等，不推测意图',options:[]},
    {key:'orientation',name:'身体朝向',instruction:'相对于摄像头的可见朝向',options:['正面','背面','左侧','右侧','左前','右前','左后','右后']},
  ]}},
  {id:'builtin_object',name:'通用物品',config:{target_label:'object',fields:[
    {key:'object_type',name:'物品类型',instruction:'按清晰可见外形描述物品类型，不猜测用途',options:[]},
    {key:'color',name:'主要颜色',instruction:'物品主要可见颜色，注意光照影响',options:[]},
    {key:'shape',name:'外形',instruction:'描述可见的形状、轮廓',options:[]},
    {key:'visible_text',name:'可见文字',instruction:'仅记录完整清晰的文字；不补猜模糊或遮挡内容',options:[]},
    {key:'condition',name:'外观状态',instruction:'只描述可见状态，如包装完整、明显破损；不推断内部情况',options:[]},
  ]}},
];
export const getPreannotationExamples=async(projectId:string,page=1,q=''):Promise<{items:PreannotationExample[];total:number}>=>
  (await apiClient.get(`/api/projects/${projectId}/preannotation-examples`,{params:{page,page_size:10,q}})).data;

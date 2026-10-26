import apiClient from './client';
export type Operation='crop'|'caption'|'index';
export type Judgment='correct'|'incorrect'|'unknown';
export interface WorkJob { id:string;operation:Operation|'match_review';model_id:string;revision:number;total:number;counts:Record<string,number>; }
export interface WorkItem { id:string;asset_id:string|null;status:string;attempt:number;error:string|null;result:{asset_ids?:string[];detected_count?:number;description?:string;warning?:string;reason?:string}|null; }
export interface WorkDetail extends WorkJob { items:WorkItem[];page:number; }
export interface SmartSearchInfo {platform_id:string;platform_name:string;region:number[];original_width:number;original_height:number;record:{timestamp?:string;deviceName?:string;confidence?:number};}
export interface MatchHit {asset_id:string;image_id:string|null;original_name:string;source_type:string;score:number;judgment:Judgment;description?:string;labels?:string[];smart_search?:SmartSearchInfo;review_status?:'pending'|'approved'|'rejected';ai_review?:{status:'approved'|'needs_review'|'resolved';judgment:'correct'|'incorrect'|'uncertain';reason:string;model_name:string;revision:number;reviewed_at:string};}
export interface MatchQuery {id:string;query:{text?:string;image_id?:string;query_asset_id?:string;source?:string;platform_name?:string};version:number;space:{revision:number;source?:string;failures?:{index:number;error:string}[]};hits:MatchHit[];failures?:{index:number;error:string}[];}
export interface SmartSearchReference {reference_query_id:string;reference_asset_id:string;region:{x:number;y:number;width:number;height:number};}
export interface SmartSearchRequest {platform_id:string;text?:string;limit:number;start_time:string;end_time:string;reference_query_id?:string;reference_asset_id?:string;region?:SmartSearchReference['region'];}
const base=(id:string)=>`/api/projects/${id}`;
export const createWork=async(id:string,input:{operation:Operation;model_id:string;asset_ids:string[];prompt:string;labels:string[];confidence:number;expansion:number;crops_only?:boolean}):Promise<WorkJob> =>
  (await apiClient.post(base(id)+'/work/jobs',input)).data;
export const getWorkJobs=async(id:string,page=1):Promise<WorkJob[]> =>(await apiClient.get(base(id)+'/work/jobs',{params:{page}})).data;
export const getWork=async(id:string,job:string,page=1):Promise<WorkDetail> =>(await apiClient.get(base(id)+`/work/jobs/${job}`,{params:{page}})).data;
export const workAction=async(id:string,job:string,action:'cancel'|'retry')=>(await apiClient.post(base(id)+`/work/jobs/${job}/${action}`)).data;
export const searchMatches=async(id:string,input:{model_id:string;text?:string;image_id?:string;top_k:number}):Promise<MatchQuery> =>
  (await apiClient.post(base(id)+'/matches/search',input,{timeout:185000})).data;
export const getSmartSearchPlatforms=async(id:string):Promise<{id:string;name:string}[]> =>
  (await apiClient.get(base(id)+'/matches/smart-search/platforms')).data;
export const searchSmartMatches=async(id:string,input:SmartSearchRequest):Promise<MatchQuery> =>
  (await apiClient.post(base(id)+'/matches/smart-search',input,{timeout:600000})).data;
export const getMatchQueries=async(id:string,page=1):Promise<MatchQuery[]> =>(await apiClient.get(base(id)+'/matches',{params:{page}})).data;
export const judgeMatch=async(id:string,query:MatchQuery,asset_id:string,judgment:Judgment):Promise<MatchQuery> =>
  (await apiClient.put(base(id)+`/matches/${query.id}`,{asset_id,judgment,base_version:query.version})).data;
export const reviewMatch=async(id:string,query:MatchQuery,asset_id:string,approved:boolean):Promise<MatchQuery> =>
  (await apiClient.post(base(id)+`/matches/${query.id}/review`,{asset_id,approved,base_version:query.version})).data;
export const createMatchReviewBatch=async(id:string,input:{model_id:string;query_ids?:string[];prompt:string}):Promise<WorkJob[]> =>
  (await apiClient.post(base(id)+'/matches/review-jobs',input,{timeout:120000})).data;

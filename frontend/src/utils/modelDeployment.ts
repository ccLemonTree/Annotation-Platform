export const deploymentNames:{[key:string]:string}={draft:'待检查',checked:'检查通过',transferring:'准备中',staged:'待生效',activating:'生效中',active:'已生效',
  rolling_back:'恢复中',rolled_back:'已恢复旧版本',transfer_failed:'准备失败',activation_failed:'生效未完成',rollback_failed:'恢复未完成',unknown:'状态待核对'};
export const deploymentBusy=['transferring','activating','rolling_back'];
export function deploymentError(error:unknown){
  const value=error as {response?:{data?:{detail?:string|{msg:string}[]}};message?:string};const detail=value.response?.data?.detail;
  return typeof detail==='string'?detail:Array.isArray(detail)?detail.map(row=>row.msg).join('；'):value.message||'操作失败';
}

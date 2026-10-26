# 导出划分缺图修复

- 真实只读复现：GET /api/annotations按100条分页返回3945条，但仅3138个不同annotation/asset ID；资产接口为3945条、3945个唯一ID。对应用户截图“训练3138 / 来源缺失3945”及invalid_splits。
- 根因：标注列表仅按created_at DESC、version DESC排序。批量导入创建时间/版本相同，PostgreSQL不同分页缺少确定顺序，重复/遗漏标注。增加Annotation.id作为唯一排序末项，不更改业务数据或过滤规则。
- 新回归构造相同时间和版本、乱序UUID，验证多页按唯一键完整返回；旧实现顺序断言失败。修改后标注、scope、导出、划分和性能相关39项测试通过。
- 重启后端进程89356加载修复。真实认证请求全部分页：annotations与assets均3945条、3945个唯一ID，两个资产集合相等，所有原图均读到最新标注。
- 按全部train和关闭独立划分要求进行只读规则计算，不再出现invalid_splits；source_missing及training_budget仍真实返回，未绕过检查。
- 未修改图片、标注、来源或用户划分，未创建冻结包。窗口中此前缺失的列表需要关闭后重新打开加载；仅刷新预览不会重新读取旧列表。
- 证据：output/export-pagination-diagnosis.log、output/export-pagination-fixed.log、output/annotation-pagination-green.log、output/annotation-pagination-backend.stderr.log。

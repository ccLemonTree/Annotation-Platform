# P2-04 数据集划分收尾（2026-10-15）

本次实现与验收使用合成样本 / 独占测试库；没有提交代码，没有读写业务表，没有执行业务导出或真实训练。无需 schema 迁移，未占用 015。

## 实现范围

- `backend/backend/services/dataset_splits.py`：明确来源元数据归一化、传递分组、近重复合组、按图片统计类别预算、手动预览与确定性建议。
- `backend/backend/services/detection_export.py`：预览读取原图并校验 SHA256 / 尺寸，在工作线程计算 pHash / 颜色；冻结共用规则，保存 `manifest.split_evidence`。
- `backend/backend/api/export_routes.py`：预览、来源读取与局部编辑端点，现有冻结请求增加可选策略。
- `frontend/src/api/export.ts`、`frontend/src/components/ProjectExport.tsx`：复用原入口，添加来源编辑、预算/阈值/严格策略、预览/建议、各划分类别图片统计、冻结证据展示。
- 新专项测试：`backend/backend/tests/test_p2_dataset_splits.py`、`test_p2_dataset_splits_postgres.py`、`frontend/tests/p2-dataset-export.browser.cjs`。

未修改 models、conftest、pytest.ini、App、MainLayout、TODO。未增加依赖。构建及测试生成临时产物和本专项截图 / 结果。

## 接口

所有接口沿用认证及 tenant/project 范围校验。以下前缀为 `/api/export/projects/{project_id}`。

| 方法 / 路径 | 输入 / 输出 |
| --- | --- |
| `GET /split-sources` | 返回 `{asset_id: {parent_image_id?, group_id?, sequence_id?, device_id?}}`，只读取元数据，不读取原图或写审计 |
| `PATCH /assets/{asset_id}/split-source` | 部分更新上述四个字段；未传保持，`null` 清除。非空字符串最长 160；保留 device_info 其他键。锁资产，记录 `update_split_source` before/after 审计 |
| `POST /splits/preview` | `{labels?, approved_only:true, policy?, splits?, suggest:false}`；建议用 `suggest:true` 且不传 splits。返回实际 splits、来源、合并组、来源缺失 ID、图片统计、指纹、警告、结构化错误及 can_freeze。只读，不保存建议 |
| `POST /snapshots`（原有） | 原请求新增可选 `policy`，冻结时重新读取并校验，不信任浏览器预览。策略/来源/分组/实际划分/指纹/警告固定进入包及快照 JSONB |

策略示例：

```json
{
  "phash_distance": 4,
  "color_distance": 20,
  "training_budget_per_class": 100,
  "require_source": false,
  "require_independent": true
}
```

pHash 距离允许 0—64，颜色各通道最大差允许 0—255；两条件同时满足才匹配。阈值越大，匹配越多、跨划分拦截越严格。训练预算为正整数或 null（取消上限），按**含该类别的训练图片数**计，单图多框只计一次，多类图分别占用各类预算。val/test 不计入训练预算。

预览正常返回 200，即使规则不满足（查看 errors/can_freeze）。参数错误为 422；手动 splits 与 suggest 同传为 400。冻结划分 ID 不完整仍为 400；同内容哈希跨划分保留旧 400；新来源/近重复/预算/严格策略冲突为 409。

## 分组、建议与兼容

只读取 `Asset.device_info` 中明确的 parent_image_id、group_id、sequence_id、device_id，不解析文件名，不按时间猜连续帧。父图 ID 可关联本项目导出范围内的资产 ID 或图库 image_id；同一明确父图、来源组、连续帧组、设备、相同 SHA256 及近重复关系均传递合组。同设备整组保守保留。

自动建议按组大小及资产 ID 稳定排序；以约 60% 总图片为训练倾向，训练类别预算为上限，预留独立 val/test 组，余量平衡到 val/test。预算不是承诺足额采满 100；组不可拆，无法满足时明确报错或提示。大组超预算时保留能容纳的小组用于训练，不把所有小组先消耗给评估集。负样本和仅含未选中标签的图依旧保留。

新页面默认开启require_source，来源缺失时阻止冻结；操作员可显式关闭，缺失来源仍保留警告。直接API的策略缺省及旧请求保持兼容，不声称未知来源已验收。覆盖判定为有至少一种明确来源关系，或由明确父图/分组关系覆盖到原图；**不代表四类来源字段全部完整**。require_independent默认要求train/val/test均非空；每类在某划分缺图另外警告，不虚构样本或声称类别覆盖已验收。

旧冻结请求不传 policy 时：保留旧 4/20 阈值，不新增训练预算及三集齐全的强制要求；已知来源关系仍禁止跨组。旧快照只读原有文件，不重建、不回填。未划分当前包仍是原导出语义，不新增 split_evidence。标签过滤只过滤框、保留图及负样本。原 `['人']` fallback 保留；无标注时新增提示它只是界面候选，不代表项目类别配置。

来源编辑仅作用于当前 Asset 的 JSONB，不修改共享图库/其他项目，不改变任何已冻结包；其他来源键保留。

## 实际验证结果

1. **失败测试先行**：首次专项测试因缺模块/端点失败；来源只读端点新增前再次确认 404；不均衡大组用例先失败，再修复建议策略。
2. **隔离回归 32 passed**：专项 12 例 + 原 test_p2_exports / test_p2_annotations。覆盖来源传递、图库父图 ID、同内容、无文件名推断、非法元数据、预算图片计数、独立保留、不足/超大组、pHash/颜色阈值边界、预览无审计写、租户范围、冻结证据不可变、旧包、标签/负样本、异步不阻塞回归。
3. **专属 PostgreSQL 1 passed**：必须设置 `P2_DATASET_POSTGRES_INTEGRATION=1`，不是 `P2_POSTGRES_INTEGRATION`。连接管理库 postgres，仅创建自有 `codex_p2_dataset_<uuid>`；确认 JSONB 类型、规则执行、冻结证据持久化、来源编辑被导出资产行锁阻塞、解锁后成功、快照不变。最终复跑 5.74s，临时库 `codex_p2_dataset_f6fe1ad2d2bd439c9aa0ad2910ba09ef` 已删除并查询确认不存在。
4. **浏览器拦截验收 passed**：真实项目页面入口，Edge headless，30 次 API 全部 fulfill/abort，转发到后端为 0，未知请求和 pageerror 均为 0。覆盖手动错误、建议启用冻结（包含 ID 顺序不同）、预算/阈值修改使预览失效、来源编辑使预览失效、冻结载荷、旧包两次下载、无标注提示。只测试 UI 链路，合成 ZIP 不冒充真实训练包；后端包内容另由 Python 测试验证。
5. **npm run build 成功**：TypeScript + Vite；已有大 chunk 警告。定向 oxlint 无错误，仅组件原有 effect 内 setState 模式警告。

浏览器产物：`docs/testing/p2-04-browser/result.json`、`project-export.png`，已查看截图。父代理整体回归另计，不将定向结果表述为全库回归。

## 可复跑命令（pwsh）

后端工作目录 `F:/hzxf/数据管理平台/backend/backend`，始终显式独占 basetemp；不要用 pytest.ini 的共享默认目录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_p2_dataset_splits.py tests/test_p2_exports.py tests/test_p2_annotations.py -q --basetemp=.pytest-tmp-dataset
$env:P2_DATASET_POSTGRES_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/test_p2_dataset_splits_postgres.py -q -s --basetemp=.pytest-tmp-dataset-pg
```

浏览器脚本不新增项目依赖，使用现有 Playwright。两个 pwsh 会话，前端目录 `F:/hzxf/数据管理平台/frontend`：

```powershell
npm run dev -- --host 127.0.0.1 --port 3184 --strictPort
```

```powershell
# 本机已验证的现有运行时；其他机器替换为已有 playwright 模块目录。
$env:PLAYWRIGHT_MODULE='C:/Users/chen0/AppData/Local/OpenAI/Codex/runtimes/cua_node/a9ba6825a525118c/bin/node_modules/playwright'
$env:P2_FRONTEND_URL='http://127.0.0.1:3184'
node tests/p2-dataset-export.browser.cjs
```

默认使用系统 Edge。若需要其他已有 Chromium，可设置 P2_BROWSER_EXECUTABLE。P2_BROWSER_ARTIFACT_DIR 可指定独占产物目录。脚本拒绝非 localhost/127.0.0.1 地址，阻止所有未匹配 API 和外部请求，阻止 service worker；不会访问业务库。

## 未决边界

- 真实设备/来源完整性、训练和独立评估样本量及最终测试集验收仍待业务数据确认，未做真实集验收。
- 不保证任意裁剪 / 遮挡 / 视角变化 / 跨帧相似全部被 pHash+颜色识别。明确来源关系优先，缺失关系无法靠相似度补证。
- device_id / group_id 等目前以项目内字段值直接合组，跨平台同名 ID 可能过度合并；这是保守限制，可能降低可用分组数，不会因此允许已知同设备跨划分。应按真实元数据维护有来源命名空间的 ID，不能为凑预算编造新 ID。
- 自动建议是分组启发式，非最优分层求解；不保证每类每集足量，需人工预览并处理警告。近重复采用 O(n²) 比较，适用当前小规模精选集；批量规模扩大再建指纹索引。

# 烟火验证任务结果来源核查

2026-10-20 14:43—14:47（北京时间），只读核查任务 `a6eda2ce-94f9-452a-b51a-c1fcd42ee2bc`。未启动新推理、未修改任务、未重启后台。

## 远端执行证据

- 服务器：绍兴a100，GPU 3，UUID `GPU-53029a95-8a54-feee-194e-d1b0de2d9cae`。
- 容器：`platform-validation-5a5f4997-beff-474f-9f1c-5f07432c6dae`，ID `ab57c728c1c1a8526a2899824ff02ecb3637687d27beb51be7ef9de9bfb0efe5`。
- Docker StartedAt `2026-10-20T06:13:49.330656612Z`，FinishedAt `2026-10-20T06:18:34.884474549Z`，即北京时间 14:13:49—14:18:34。ExitCode=0，OOMKilled=false。
- DeviceRequests 绑定上述 GPU UUID。入口 `python3 -u /job/predict.py`，脚本选用 `cuda:0` 加载 RF-DETR；没有平台 CPU 回退分支。
- 镜像 `sha256:a5c8ea4b39d5b21e7ba1996edd33d19d66cb0c2fd88021b32aa89b305f1eda6d`。
- 只读绑定用户指定 `checkpoint_best_total.pth`。远端重新计算权重 SHA256 为 `0ee39e85bafc5cbf7719c8d192fe767b15f5ec88c758f6e355a1b9cd38f50f68`，与冻结配置、页面一致。
- 实际执行脚本 SHA256 为 `1d9868f1e6f017c38521f6fe026882b1eca9b92ff9f06cfd99346e47f20a63cc`，与平台 `services/checkpoint_predict.py` 一致。

## 结果来源

远端目录 `/ai/chen/validation/runs/5a5f4997-beff-474f-9f1c-5f07432c6dae/results/` 有 3945 份 JSON，manifest 3945 张，error=0，预测框合计 2245。文件时间为 14:14:03—14:18:33。

从数据库抽取最早 5 个非空结果和最新 5 个已完成结果，共 10 张（含 fire、smoke 和空预测），与对应远端 JSON 的 boxes 做对象相等比较，10/10 一致，包含坐标和完整浮点分数。抽样内容保存在 `output/validation-provenance-samples.json`。这是抽样一致性验证，不是全量数据库比对。

14:44:08 平台任务仍为 running，已处理 902/3945，失败 0。平台 `checkpoint_validation.infer` 对每图调用 `remote(action='result')`；`training_runs.bridge` 每次重新启动 Node / ssh-manager MCP 进程并建立连接，远端 executor 再读已有单图 JSON。随后 `validation.process_one` 校验原图、计算逐图指标、入库并更新覆盖率。GPU 批量预测完成后，平台仍可在较长时间内持续接收结果。

因此，页面“执行中”同时覆盖 GPU 推理和后续结果回传、校验、入库，不能解读为 GPU 此刻仍在运行。当前性能瓶颈包含逐图重新建立 SSH/MCP 通信；本次未修改该流程。未留存运行当时的 GPU 利用率曲线，设备结论依据容器设备配置、实际执行脚本和成功预测结果链路。

可用 `docker ps -a --filter name=platform-validation-5a5f4997-beff-474f-9f1c-5f07432c6dae` 查看已退出容器；普通 `docker ps` 只显示运行中的容器。

后续：用户授权取消任务后已确认 canceled / remote_active=false，保留 1049 张已入库预测，2896 张标为 canceled。批量结果回传已实现并更新后台，验证见 `2026-10-20-checkpoint-batch-results.md`；没有重新运行用户权重。

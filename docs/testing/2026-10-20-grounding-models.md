# 绍兴 A100 Grounding 模型部署验收

部署日期：2026-10-20。SSH 使用 ssh-manager 的 `绍兴a100`，公网地址 `36.138.253.82`。本次不操作嘉善 162。

| 模型 | Docker 容器 | GPU | 服务 | 平台 KEY / ID |
|---|---|---|---|---|
| VLX-Seek 1.5-10B | `platform-vlx-seek` | 1 / `GPU-1b815d84-e833-8bdf-9126-8e4ee42205bd` | `http://36.138.253.82:10031` | `vlx_seek` / `dba9aa4b-2f9e-4ba7-ac77-200ab121e619` |
| GroundingPI | `platform-groundingpi` | 2 / `GPU-80dbabae-d179-b14f-e919-f4137a3f2a43` | `http://36.138.253.82:10032` | `groundingpi` / `8ace756f-d870-42f2-ad40-ec524a9cc200` |

平台「模型管理」可连接测试、选择图片试运行；检测工作区可预测预览，再人工应用到草稿。默认类别 `electric bicycle` 映射为 `电瓶车`。单次输入可覆盖类别，多个类别用分号分隔。

## 运行环境和边界

- 主机：4 张 A100 40GB，驱动 615.71.09；GPU 0 的既有 `jev-person-test` 未修改，GPU 3 未分配。
- VLX 镜像：`registry.linker.cc/vlx/vlx-seek:py3.12-ubuntu24.04-nvidia-x86_64`，image ID `sha256:164cf74a231353d4da5badbf08ad648c64f7daa51ed9a6f58e8613df183033e6`；Torch 2.14/CUDA 13.4/Transformers 5.13。
- GroundingPI 镜像：`registry.cn-hangzhou.aliyuncs.com/lemontree_images/vllm-openai:v0.27.0-cu129-ubuntu2404`，image ID `sha256:e4e19093645c47b4617b35995e225186263759b447b32248b193aae342071df5`；Torch 2.13/CUDA 12.9/Transformers 5.15，原生 HF 推理，BF16/SDPA。不是 vLLM OpenAI 服务。
- 原始权重目录 `/ai/chen/models/VLX-Seek-1.5-10B`、`/ai/chen/models/GroundingPI` 均只读挂载；主机驱动、系统包、其他模型配置不变。
- 部署文件、缓存、辅助权重：`/ai/chen/deploy/grounding-models/`。适配源文件对应仓库 `scripts/grounding_service.py`。
- GroundingPI 上游代码 revision：`090932430bbbeea004cfa461767ede7f563c0f3a`，复用其空间 token 校验及提示/图像 token 展开逻辑。未将准备的 5.7 fork 安装进镜像。
- WeDetect 辅助权重 SHA256：`e595578aeeb5f2a36cca383c23279cf7ea72e0bfb7c546dc39420359bf2d2c82`，与官方 Hugging Face 文件页一致。
- 两容器均设置 `unless-stopped`，单 GPU、单请求串行，独立缓存，单图输入。原图限制 16 MiB / 1600 万像素，推理最长边 1536；结果框转换回原图像素。
- 生成上限 1024 token，拒绝截断结果。接受协议 `nms` 字段，但不对 VLM 结果执行按分数排序的二次 NMS。
- **分数 0.5 是未校准协议占位值，不表示 50% 准确率，不可据此自动通过。** 原始服务返回 `score_type=uncalibrated`；现已忽略兼容参数 `conf` / `nms`，不按占位分数过滤。平台调用不发送这两个无效参数，返回中去掉占位 score；预览显示“未提供”。

## 调用

```bash
curl --fail http://36.138.253.82:10031/health
curl --fail http://36.138.253.82:10032/health
curl --fail --max-time 180 \
  -F 'image=@/path/to/image.jpg' \
  -F 'prompt=electric bicycle; person' \
  http://36.138.253.82:10031/detect/grounding
```

GroundingPI 使用相同请求，端口换为 `10032`。响应包括 `detections`（class、原图 xyxy bbox、score）、`raw_output`、原图尺寸及未校准提示；`/openapi.json` 提供完整接口。

平台接口为 `POST /api/model-services/{上述ID}/predict`，使用平台登录鉴权，JSON 示例：

```json
{"image_id":"图片库中的实际UUID","prompt":"electric bicycle; person"}
```

也可用 `asset_id`，两者必须且只能提供一个。预览不写入标注。

## 已验证

- 两镜像实际 GPU 张量运算通过，模型完整加载；公网 health、OpenAPI、平台连接测试通过。
- 上游示例图片：VLX 返回 1 个 apple 框，GroundingPI 返回 2 个 apple 框；VLX 重复请求通过。
- 平台既有图片库图片 `0010a132-85ca-43b8-a193-ae267914bb97`：两条模型 predict 均 HTTP 200，VLX 返回空框并提示，GroundingPI 返回 3 框。这是调用验证，不是目标检测准确率评估。
- 本地适配测试 5 项、平台模型管理测试 9 项通过；前端构建通过，浏览器 mock 验证管理、检测提示透传、预览、映射、撤销、空框保护、只读及迟到响应丢弃。
- 平台验证记录：`output/grounding-deployment-20261020.json`。远端示例请求和结果在部署目录的 `verification/`。
- 未执行电瓶车数据集精度评测、压力测试或训练；现有前端 lint 警告保留。

## 维护

```bash
docker logs --tail 100 platform-vlx-seek
docker logs --tail 100 platform-groundingpi
docker restart platform-vlx-seek
docker restart platform-groundingpi
```

容器重启后先等模型加载，再检查 health 和真实图片请求。停止其中一项只使用相应容器名，不删除主模型目录。若修改适配源文件，需重启相应服务载入新代码。

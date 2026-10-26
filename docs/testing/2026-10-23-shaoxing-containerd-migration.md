# 绍兴 A100 Docker / containerd 存储迁移

2026-10-23，Docker 29.1.3 使用 containerd snapshotter。Docker Root Dir 已为 `/ai/docker`，但独立 containerd 2.2.2 仍使用 `/var/lib/containerd`，约166GiB，因此根分区仍达98%。

已将 containerd 数据迁至 `/ai/docker/containerd`。`/etc/containerd/config.toml` 使用默认版本3配置，只覆盖 root；state 仍在 `/run/containerd`。`/etc/systemd/system/containerd.service.d/storage.conf` 添加 `RequiresMountsFor=/ai/docker/containerd`。Docker daemon.json 保持原样。

执行前预复制并保存清单；停服务后再次 rsync 同步，并确认 dry-run 差异为空。迁移后镜像清单一致，运行中的容器 overlay 已落在 /ai 磁盘，containerd 实际打开的 meta.db 位于新路径。原始镜像、停止中的任务和宿主机训练数据保留。

第一次切换因 `focused_satoshi` 的退出自动删除导致容器数量检查失败，自动回退。停止前没有保存其完整配置，已明确告知用户；用户确认不重启该重复训练容器，保留既有 sim-train-v3 和 ckpt_v3 检查点。第二次切换前保存全部现存容器的完整配置并确认推理容器 AutoRemove=false，切换成功。推理服务恢复后，合成64×64灰色JPEG调用 `/api/analyze` 返回200、n_person=0；GET `/api/tasks` 和首页均200，GPU进程恢复。

成功验证后删除根分区旧副本：根分区从184GiB已用/4.6GiB可用/98%，降至19GiB已用/171GiB可用/10%；/ai为463GiB已用/493GiB可用/49%。未清理其他镜像或业务数据，未重启整机。

远端审计记录：`/ai/docker-migration-20261023-1123/`，成功切换与清理记录在 `attempt2/`。完整容器配置备份目录权限为0700，不随报告导出。

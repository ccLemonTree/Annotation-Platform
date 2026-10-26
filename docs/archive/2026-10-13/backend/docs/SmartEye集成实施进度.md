"""SmartEye integration implementation checklist and testing guide

## ✅ 已完成的工作

### 1. 数据库迁移脚本
- ✅ `alembic/versions/004_simplified_smarteye_integration.py`
- 简化了SmartEye集成架构
- 删除了不必要的 smarteye_tasks 和 smarteye_downloads 表
- 在 Platform 表中添加了 platform_type 和 storage_config 字段

### 2. 后端服务层
- ✅ `backend/services/smarteye_import_service.py`
- SmartEyeImportService 类
- 零拷贝导入逻辑
- 文件路径验证
- SHA256哈希计算（去重）

### 3. API路由
- ✅ `backend/api/smarteye_routes.py`
- POST /api/smarteye/create-annotation-task (创建标注任务)
- POST /api/smarteye/verify-paths (验证路径)
- 完整的请求/响应模型

### 4. 路由注册
- ✅ 更新 `backend/api/__init__.py`
- 注册 smarteye_routes

---

## 🔧 待完成的工作

### 1. 运行数据库迁移

```bash
cd F:\hzxf\数据管理平台\backend\backend
alembic upgrade head
```

### 2. 测试后端API

启动后端：
```bash
python main.py
```

访问API文档：http://localhost:8000/docs

测试创建标注任务接口：
```bash
curl -X POST "http://localhost:8000/api/smarteye/create-annotation-task" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "你的项目ID",
    "platform_id": "你的平台ID",
    "batch_name": "测试批次",
    "records": [
      {
        "id": "test_001",
        "image_path": "/data/smarteye/images/test.jpg",
        "alarm_type": "人员入侵",
        "device_id": "camera_001",
        "device_name": "测试摄像头",
        "timestamp": "2024-10-12 10:30:00",
        "confidence": 0.95
      }
    ]
  }'
```

### 3. 前端集成

#### 3.1 创建SmartEye集成页面

文件：`frontend/src/pages/DataAcquisition/SmartEyeIntegration.tsx`

核心功能：
- iframe嵌入SmartEye界面
- postMessage通信接收选中数据
- 调用后端API创建标注任务

#### 3.2 修改SmartEye前端

文件：`smarteye/download_img-main/src/...` (具体位置需要查看SmartEye项目)

添加功能：
- 添加"创建标注任务"按钮
- 通过postMessage发送选中数据到父窗口

```typescript
// 在SmartEye前端添加
window.parent.postMessage({
  type: 'SMARTEYE_DATA_SELECTED',
  records: selectedRecords
}, 'http://localhost:3000');  // 数据管理平台前端地址
```

### 4. SmartEye CORS配置

修改 `smarteye/app.py`：

```python
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=[
    "http://localhost:3000",  # 开发环境前端
    "http://localhost:8000",  # 开发环境后端
    # 生产环境根据实际情况添加
])
```

### 5. 部署配置

#### 开发环境启动脚本

创建 `scripts/start_dev.sh` (Linux/Mac) 或 `scripts/start_dev.bat` (Windows):

```bash
#!/bin/bash
# 启动SmartEye
cd /f/hzxf/数据管理平台/smarteye
python app.py &

# 启动数据管理平台后端
cd /f/hzxf/数据管理平台/backend/backend
python main.py &

# 启动前端
cd /f/hzxf/数据管理平台/frontend
npm run dev &

echo "所有服务已启动"
echo "SmartEye: http://localhost:5000"
echo "后端API: http://localhost:8000"
echo "前端: http://localhost:3000"
```

---

## 📋 测试清单

### 后端测试

- [ ] 数据库迁移成功
- [ ] Platform表有platform_type和storage_config字段
- [ ] 后端启动成功
- [ ] API文档可访问 (http://localhost:8000/docs)
- [ ] 健康检查接口正常 (GET /health)
- [ ] SmartEye路由已注册 (在API文档中可见)

### API功能测试

- [ ] 创建Platform配置（storage_config指向SmartEye图片目录）
- [ ] 调用 verify-paths 接口验证路径
- [ ] 调用 create-annotation-task 接口导入数据
- [ ] 检查Asset表中是否有新记录（零拷贝，blob_path指向SmartEye路径）
- [ ] 检查ImportBatch表中的统计数据

### 集成测试

- [ ] SmartEye独立运行正常
- [ ] 数据管理平台前端可以通过iframe加载SmartEye
- [ ] postMessage通信正常
- [ ] 选中数据后可以创建标注任务
- [ ] 数据正确导入到数据管理平台

### 去重测试

- [ ] 相同图片（相同hash）不会重复导入
- [ ] skipped_count正确统计

### 错误处理测试

- [ ] 图片路径不存在时的错误提示
- [ ] Platform不存在时的错误提示
- [ ] 必填字段缺失时的错误提示

---

## 🚨 注意事项

### 1. 路径配置

确保Platform表的storage_config正确配置：

```json
{
  "type": "local",
  "base_path": "/data/smarteye/images",
  "is_same_server": true
}
```

如果SmartEye和数据管理平台不在同一台服务器，需要：
- 设置 "is_same_server": false
- 配置NFS或其他共享存储
- 或者修改为复制模式

### 2. 文件权限

确保数据管理平台后端进程有权限读取SmartEye的图片目录

### 3. CORS配置

开发环境需要配置CORS允许跨域访问

### 4. 前端iframe安全策略

确保X-Frame-Options允许iframe嵌入

---

## 📝 下一步建议

1. **先测试后端**：运行迁移，启动后端，测试API
2. **再测试SmartEye独立运行**：确保SmartEye本身工作正常
3. **最后做前端集成**：iframe嵌入和postMessage通信

需要我继续完成哪个部分？
- [ ] 前端集成代码（React组件）
- [ ] SmartEye前端修改（添加"创建标注任务"按钮）
- [ ] 部署脚本和文档
- [ ] 先测试现有后端代码

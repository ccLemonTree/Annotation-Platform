# SmartEye集成方案

**更新日期**: 2026-10-12  
**SmartEye项目地址**: https://gitee.com/suLemonTree/auto-img-downloader

---

## 📊 SmartEye项目分析

### 技术栈

**后端**:
- Flask 3.1.2
- PostgreSQL (psycopg2-binary)
- Python-dotenv
- Requests
- Pillow + imagehash (图片处理和去重)

**前端**:
- React + TypeScript
- Vite (构建工具)

### 核心功能模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 告警数据采集 | `alarm_data_crawler.py` | 采集告警图片 |
| 告警类型管理 | `alarm_type_crawler.py` | 获取告警类型 |
| 台账图片下载 | `ledger_image_downloader.py` | 批量下载台账图片 |
| 台账增量监控 | `ledger_watch_downloader.py` | 实时监控新增记录 |
| 智能搜索 | `smart_search.py` | 多IP平台搜索 |
| AI图像生成 | `ai_image.py` | 火山引擎AI生图 |
| 数据库工具 | `db_utils.py` | 数据库操作 |
| 图片去重 | `image_dedup.py` | 基于哈希的去重 |

### 数据存储

- **图片存储**: 本地文件系统 (`DOWNLOAD_PATH`)
- **元数据**: PostgreSQL数据库
- **去重机制**: imagehash (感知哈希)

---

## 🎯 三种集成方案对比

### 方案1: 子模块集成（推荐）⭐⭐⭐⭐⭐

**架构**:
```
数据管理平台/
├── frontend/                    # React前端（新开发）
│   ├── src/
│   │   ├── pages/
│   │   │   ├── DataAcquisition/  # 数据拉取页面
│   │   │   │   └── SmartEyeIntegration.tsx  # 嵌入SmartEye
│   │   │   ├── DataManagement/   # 数据管理
│   │   │   └── Annotation/       # 标注工作台
│   │   └── ...
│
├── backend/
│   ├── api/
│   │   ├── smarteye_integration.py  # SmartEye集成API（新）
│   │   ├── project_routes.py
│   │   └── ...
│   ├── models/
│   ├── services/
│   │   └── smarteye/
│   │       ├── smarteye_client.py   # 调用SmartEye API（新）
│   │       └── import_service.py    # 零拷贝导入（新）
│   └── main.py                      # FastAPI入口
│
└── smarteye/                        # SmartEye子项目（作为子模块）
    ├── app.py                       # Flask应用（独立运行）
    ├── api/
    │   ├── alarm_data_crawler.py
    │   ├── ledger_image_downloader.py
    │   └── ...
    └── download_img-main/           # SmartEye前端
```

**工作流程**:
```
1. SmartEye独立运行在端口5000
2. 数据管理平台运行在端口8000
3. 用户访问: http://localhost:8000
4. 数据拉取页面通过iframe嵌入SmartEye: http://localhost:5000
5. 用户在SmartEye筛选数据后，点击"创建标注任务"
6. 调用数据管理平台API，零拷贝导入数据
```

**优点**:
- ✅ SmartEye代码独立，互不干扰
- ✅ SmartEye可以单独升级维护
- ✅ 开发简单，改动最小
- ✅ 前端通过iframe集成，实现快速

**缺点**:
- ⚠️ 需要运行两个后端服务
- ⚠️ iframe可能有跨域问题（可配置CORS解决）

**实施步骤**:
1. SmartEye作为子模块或子目录
2. SmartEye独立启动（Flask on 5000）
3. 数据管理平台启动（FastAPI on 8000）
4. 前端页面通过iframe嵌入SmartEye
5. 添加"创建标注任务"按钮，调用数据管理平台API

---

### 方案2: API集成（微服务架构）⭐⭐⭐⭐

**架构**:
```
数据管理平台（主项目）
├── 前端：React (新开发，包含数据拉取UI)
└── 后端：FastAPI
        └── 调用SmartEye API

SmartEye（独立服务）
├── 前端：React (保持独立，可选访问)
└── 后端：Flask API（提供RESTful接口）
```

**修改SmartEye，暴露API**:
```python
# smarteye/app.py 新增API端点

@app.route('/api/query-alarms', methods=['POST'])
def query_alarms_api():
    """
    供外部调用的告警查询API
    
    Request:
    {
        "start_time": "2024-10-01 00:00:00",
        "end_time": "2024-10-12 23:59:59",
        "alarm_types": ["人员入侵"],
        "device_ids": ["camera_001"]
    }
    
    Response:
    {
        "total": 1234,
        "records": [
            {
                "id": "record_id",
                "image_path": "/data/smarteye/images/xxx.jpg",
                "alarm_type": "人员入侵",
                "device_id": "camera_001",
                "timestamp": "2024-10-12 10:30:00",
                ...
            }
        ]
    }
    """
    data = request.json
    # 复用现有的 alarm_data_crawler 逻辑
    results = get_alarm_image_data(
        token=get_automated_token(),
        start_time=data['start_time'],
        end_time=data['end_time'],
        alarm_types=data.get('alarm_types', []),
        # ...
    )
    return jsonify(results)
```

**数据管理平台调用**:
```python
# backend/services/smarteye/smarteye_client.py

class SmartEyeClient:
    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url
    
    async def query_alarms(self, query_params: Dict) -> List[Dict]:
        """调用SmartEye API查询告警数据"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/query-alarms",
                json=query_params
            )
            return response.json()
```

**优点**:
- ✅ 前端完全自主开发，UX一致
- ✅ 松耦合，微服务架构
- ✅ SmartEye可以独立部署到其他服务器

**缺点**:
- ⚠️ 需要修改SmartEye，暴露API接口
- ⚠️ 前端需要重新开发数据拉取UI

---

### 方案3: 代码合并（不推荐）⭐⭐

**架构**:
```
数据管理平台/
├── frontend/  # 合并两个前端
└── backend/   # 合并Flask和FastAPI代码
```

**优点**:
- ✅ 单一代码库
- ✅ 单一部署

**缺点**:
- ❌ Flask和FastAPI混用，架构混乱
- ❌ 代码耦合度高
- ❌ SmartEye升级困难
- ❌ 工作量大

---

## 🏆 推荐方案详细设计

### 方案1: 子模块集成（最快实现）

#### 1. 目录结构

```
F:\hzxf\数据管理平台\
├── frontend/                    # 新开发的React前端
│   ├── src/
│   │   ├── pages/
│   │   │   ├── DataAcquisition/
│   │   │   │   └── SmartEyeIntegration.tsx  # 嵌入SmartEye
│   │   │   ├── DataManagement/
│   │   │   └── Annotation/
│   │   └── App.tsx
│   └── package.json
│
├── backend/                     # 当前开发的FastAPI后端
│   ├── backend/
│   │   ├── api/
│   │   │   ├── smarteye_integration.py  # 新增
│   │   │   └── ...
│   │   ├── services/
│   │   │   └── smarteye/
│   │   │       ├── smarteye_client.py   # 新增
│   │   │       └── import_service.py
│   │   └── main.py
│   └── docs/
│
└── smarteye/                    # SmartEye子项目
    ├── app.py                   # Flask应用
    ├── api/
    ├── download_img-main/       # SmartEye前端
    └── requirements.txt
```

#### 2. 启动方式

**开发环境**:
```bash
# 终端1: 启动SmartEye后端
cd F:\hzxf\数据管理平台\smarteye
python app.py  # 运行在 http://localhost:5000

# 终端2: 启动数据管理平台后端
cd F:\hzxf\数据管理平台\backend\backend
python main.py  # 运行在 http://localhost:8000

# 终端3: 启动前端
cd F:\hzxf\数据管理平台\frontend
npm run dev  # 运行在 http://localhost:3000
```

**生产环境**:
```bash
# Nginx配置
server {
    listen 80;
    
    # 主前端
    location / {
        root /path/to/frontend/dist;
    }
    
    # 数据管理平台API
    location /api/v1/ {
        proxy_pass http://localhost:8000;
    }
    
    # SmartEye (嵌入iframe用)
    location /smarteye/ {
        proxy_pass http://localhost:5000/;
    }
}
```

#### 3. 前端集成代码

```typescript
// frontend/src/pages/DataAcquisition/SmartEyeIntegration.tsx

import React, { useRef, useEffect } from 'react';
import { Button, Modal, Select, message } from 'antd';
import { createAnnotationTask } from '@/api/smarteye';

export const SmartEyeIntegration: React.FC = () => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [projectId, setProjectId] = useState<string>();
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [selectedData, setSelectedData] = useState<any[]>([]);

  // 监听iframe消息
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      // 确保消息来自SmartEye
      if (event.origin !== 'http://localhost:5000') return;
      
      if (event.data.type === 'SMARTEYE_DATA_SELECTED') {
        setSelectedData(event.data.records);
        setShowProjectModal(true);
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  const handleCreateAnnotationTask = async () => {
    if (!projectId) {
      message.error('请选择目标项目');
      return;
    }

    try {
      // 调用数据管理平台API
      const result = await createAnnotationTask({
        project_id: projectId,
        smarteye_data: selectedData
      });
      
      message.success(`成功导入 ${result.imported_count} 条数据`);
      setShowProjectModal(false);
    } catch (error) {
      message.error('导入失败');
    }
  };

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '16px', borderBottom: '1px solid #f0f0f0' }}>
        <h2>SmartEye数据拉取</h2>
        <p>在下方SmartEye界面中筛选数据后，点击"创建标注任务"导入数据</p>
      </div>
      
      <iframe
        ref={iframeRef}
        src="http://localhost:5000"
        style={{
          flex: 1,
          border: 'none',
          width: '100%'
        }}
      />

      <Modal
        title="创建标注任务"
        open={showProjectModal}
        onOk={handleCreateAnnotationTask}
        onCancel={() => setShowProjectModal(false)}
      >
        <p>已选择 {selectedData.length} 条数据</p>
        <Select
          placeholder="选择目标项目"
          style={{ width: '100%' }}
          onChange={setProjectId}
        >
          {/* 从API加载项目列表 */}
        </Select>
      </Modal>
    </div>
  );
};
```

#### 4. 修改SmartEye，添加消息通信

```javascript
// smarteye/download_img-main/App.tsx 或相关组件

// 添加"创建标注任务"按钮
<Button
  type="primary"
  onClick={() => {
    // 向父窗口发送消息
    window.parent.postMessage({
      type: 'SMARTEYE_DATA_SELECTED',
      records: selectedRecords  // 当前选中的数据
    }, 'http://localhost:3000');  // 父窗口的origin
  }}
>
  创建标注任务
</Button>
```

#### 5. 后端API实现

```python
# backend/api/smarteye_integration.py

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from services.smarteye.import_service import ImportService
from models import get_db

router = APIRouter(prefix="/smarteye-integration", tags=["SmartEye集成"])

@router.post("/create-annotation-task")
async def create_annotation_task(
    project_id: UUID,
    smarteye_data: List[Dict[str, Any]],
    db: AsyncSession = Depends(get_db)
):
    """
    从SmartEye选中的数据创建标注任务（零拷贝）
    
    smarteye_data格式:
    [
        {
            "id": "smarteye_record_id",
            "image_path": "/data/smarteye/images/xxx.jpg",
            "alarm_type": "人员入侵",
            "device_id": "camera_001",
            "timestamp": "2024-10-12 10:30:00",
            ...
        }
    ]
    """
    service = ImportService(db)
    
    # 创建导入批次
    batch = ImportBatch(
        project_id=project_id,
        source_type="smarteye",
        source_name="SmartEye告警数据",
        total_count=len(smarteye_data)
    )
    db.add(batch)
    
    success_count = 0
    for record in smarteye_data:
        try:
            # 零拷贝：直接引用SmartEye的图片路径
            image_path = record["image_path"]
            
            # 检查文件是否存在
            if not os.path.exists(image_path):
                continue
            
            # 计算hash去重
            content_hash = calculate_hash(image_path)
            
            # 检查是否已导入
            existing = await db.execute(
                select(Asset).where(
                    and_(
                        Asset.project_id == project_id,
                        Asset.content_hash == content_hash
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue
            
            # 创建Asset记录
            asset = Asset(
                project_id=project_id,
                batch_id=batch.id,
                source_type="smarteye",
                source_record_id=record["id"],
                blob_path=image_path,  # 零拷贝
                content_hash=content_hash,
                capture_time=record["timestamp"],
                device_info={
                    "platform": "SmartEye",
                    "device_id": record["device_id"],
                    "alarm_type": record["alarm_type"]
                }
            )
            db.add(asset)
            success_count += 1
            
        except Exception as e:
            logger.error(f"导入失败: {e}")
    
    batch.success_count = success_count
    batch.failed_count = len(smarteye_data) - success_count
    
    await db.commit()
    
    return {
        "batch_id": batch.id,
        "imported_count": success_count
    }
```

---

## 🚀 实施步骤

### 第一阶段：准备工作（1天）

1. ✅ 将SmartEye代码放到 `F:\hzxf\数据管理平台\smarteye\`
2. ✅ 验证SmartEye可以独立运行
3. ✅ 配置CORS，允许iframe嵌入

### 第二阶段：后端集成（2-3天）

1. ✅ 实现 `smarteye_integration.py` API
2. ✅ 实现零拷贝导入逻辑
3. ✅ 测试API功能

### 第三阶段：前端集成（2-3天）

1. ✅ 创建数据拉取页面
2. ✅ 集成iframe
3. ✅ 实现postMessage通信
4. ✅ 修改SmartEye前端，添加"创建标注任务"按钮

### 第四阶段：测试和优化（2天）

1. ✅ 端到端测试
2. ✅ 性能优化
3. ✅ 文档更新

---

## 📋 配置清单

### SmartEye CORS配置

```python
# smarteye/app.py

from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["http://localhost:3000"])  # 允许前端访问
```

### Nginx生产配置

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    # 前端
    location / {
        root /var/www/frontend/dist;
        try_files $uri $uri/ /index.html;
    }
    
    # 数据管理平台API
    location /api/v1/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    # SmartEye服务（iframe访问）
    location /smarteye/ {
        proxy_pass http://127.0.0.1:5000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        # 允许iframe嵌入
        add_header X-Frame-Options SAMEORIGIN;
    }
}
```

---

## ✅ 总结

### 推荐方案：子模块集成

**理由**：
1. ✅ 最小改动SmartEye代码
2. ✅ 开发速度最快
3. ✅ SmartEye可以独立维护升级
4. ✅ 零拷贝架构，节省存储

**工作量估算**：
- 后端集成：2-3天
- 前端集成：2-3天
- 测试优化：2天
- **总计：1周左右**

**下一步行动**：
1. 复制SmartEye代码到数据管理平台目录
2. 验证SmartEye独立运行
3. 开始后端API开发

需要我帮你开始实施吗？

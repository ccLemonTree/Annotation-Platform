# 📖 API使用指南

**面向**: 前端开发者  
**后端地址**: `http://localhost:8000`  
**API文档**: `http://localhost:8000/docs`

---

## 🚀 快速开始

### 1. 安装依赖

```bash
npm install axios
# 或
npm install @tanstack/react-query  # 推荐用于React项目
```

### 2. 配置API客户端

```javascript
// api/client.js
import axios from 'axios';

const apiClient = axios.create({
  baseURL: 'http://localhost:8000/api',
  timeout: 30000,
});

// 请求拦截器 - 添加token
apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器 - 统一错误处理
apiClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response?.status === 401) {
      // Token过期，跳转登录
      localStorage.removeItem('access_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export default apiClient;
```

---

## 🔐 认证流程

### 登录

```javascript
// api/auth.js
import apiClient from './client';

export const login = async (username, password) => {
  const formData = new FormData();
  formData.append('username', username);
  formData.append('password', password);

  const response = await axios.post(
    'http://localhost:8000/api/auth/login',
    formData
  );
  
  const { access_token, expires_in } = response.data;
  
  // 保存token
  localStorage.setItem('access_token', access_token);
  localStorage.setItem('token_expires_at', Date.now() + expires_in * 1000);
  
  return response.data;
};

export const logout = () => {
  localStorage.removeItem('access_token');
  localStorage.removeItem('token_expires_at');
};

export const getCurrentUser = async () => {
  return apiClient.get('/auth/me');
};
```

### React Hook示例

```javascript
// hooks/useAuth.js
import { useState, useEffect } from 'react';
import { login as apiLogin, getCurrentUser } from '../api/auth';

export const useAuth = () => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    if (token) {
      getCurrentUser()
        .then(setUser)
        .catch(() => localStorage.removeItem('access_token'))
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = async (username, password) => {
    const data = await apiLogin(username, password);
    const user = await getCurrentUser();
    setUser(user);
    return data;
  };

  const logout = () => {
    localStorage.removeItem('access_token');
    setUser(null);
  };

  return { user, loading, login, logout };
};
```

---

## 📊 项目管理

### 获取项目列表

```javascript
// api/projects.js
import apiClient from './client';

export const getProjects = async (page = 1, pageSize = 20) => {
  return apiClient.get('/projects', {
    params: { page, page_size: pageSize }
  });
};

// 返回数据结构
// {
//   items: [...],
//   total: 100,
//   page: 1,
//   page_size: 20
// }
```

### 创建项目

```javascript
export const createProject = async (projectData) => {
  return apiClient.post('/projects', {
    project_name: projectData.name,
    task_type: projectData.taskType, // "detection" | "classification" | "segmentation"
    description: projectData.description,
    label_schema: projectData.labelSchema // JSON对象
  });
};

// 使用示例
const newProject = await createProject({
  name: '人脸检测项目',
  taskType: 'detection',
  description: '用于人脸识别的标注项目',
  labelSchema: {
    labels: [
      { id: 1, name: 'face', color: '#ff0000' },
      { id: 2, name: 'mask', color: '#00ff00' }
    ]
  }
});
```

### 获取项目详情

```javascript
export const getProject = async (projectId) => {
  return apiClient.get(`/projects/${projectId}`);
};
```

### 更新项目

```javascript
export const updateProject = async (projectId, updates) => {
  return apiClient.put(`/projects/${projectId}`, updates);
};
```

### 删除项目

```javascript
export const deleteProject = async (projectId) => {
  return apiClient.delete(`/projects/${projectId}`);
};
```

---

## 🖼️ 资产管理

### 获取资产列表

```javascript
// api/assets.js
import apiClient from './client';

export const getAssets = async (projectId, page = 1, pageSize = 50, status = null) => {
  return apiClient.get('/assets', {
    params: {
      project_id: projectId,
      page,
      page_size: pageSize,
      annotation_status: status // "pending" | "in_progress" | "completed" | "reviewed"
    }
  });
};
```

### 上传单个文件

```javascript
export const uploadAsset = async (projectId, file) => {
  const formData = new FormData();
  formData.append('file', file);

  return apiClient.post('/assets/upload', formData, {
    params: { project_id: projectId },
    headers: { 'Content-Type': 'multipart/form-data' }
  });
};

// React组件示例
const AssetUploader = ({ projectId }) => {
  const handleFileChange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    try {
      const result = await uploadAsset(projectId, file);
      console.log('上传成功:', result);
    } catch (error) {
      console.error('上传失败:', error);
    }
  };

  return <input type="file" accept="image/*" onChange={handleFileChange} />;
};
```

### 批量上传

```javascript
export const batchUploadAssets = async (projectId, files) => {
  const formData = new FormData();
  files.forEach(file => formData.append('files', file));

  return apiClient.post('/assets/batch-upload', formData, {
    params: { project_id: projectId },
    headers: { 'Content-Type': 'multipart/form-data' }
  });
};

// 使用示例
const files = [file1, file2, file3];
const results = await batchUploadAssets(projectId, files);
console.log(`成功上传 ${results.length} 个文件`);
```

### 删除资产

```javascript
export const deleteAsset = async (assetId) => {
  return apiClient.delete(`/assets/${assetId}`);
};
```

---

## 🏷️ 标注管理

### 获取标注列表

```javascript
// api/annotations.js
import apiClient from './client';

export const getAnnotations = async (assetId = null, page = 1, pageSize = 50) => {
  return apiClient.get('/annotations', {
    params: {
      asset_id: assetId,
      page,
      page_size: pageSize
    }
  });
};
```

### 创建标注

```javascript
export const createAnnotation = async (assetId, annotationData) => {
  return apiClient.post('/annotations', {
    asset_id: assetId,
    annotation_data: annotationData // JSON对象，不需要字符串化
  });
};

// 使用示例 - 目标检测标注
const annotation = await createAnnotation(assetId, {
  boxes: [
    {
      label: 'person',
      bbox: [100, 100, 200, 300], // [x, y, width, height]
      confidence: 0.95
    },
    {
      label: 'car',
      bbox: [400, 200, 300, 200],
      confidence: 0.88
    }
  ],
  metadata: {
    annotator: '<由环境变量设置密码>',
    time_spent: 120 // 秒
  }
});
```

### 更新标注

```javascript
export const updateAnnotation = async (annotationId, annotationData) => {
  return apiClient.put(`/annotations/${annotationId}`, {
    annotation_data: annotationData
  });
};
```

### 审核标注

```javascript
export const reviewAnnotation = async (annotationId, approved, comment = '') => {
  return apiClient.post(`/annotations/${annotationId}/review`, {
    approved,
    review_comment: comment
  });
};

// 使用示例
await reviewAnnotation(annotationId, true, '标注质量良好');
await reviewAnnotation(annotationId, false, '边界框不准确，请重新标注');
```

---

## 📈 统计分析

### Dashboard统计

```javascript
// api/stats.js
import apiClient from './client';

export const getDashboardStats = async () => {
  return apiClient.get('/stats/dashboard');
};

// React组件示例
const Dashboard = () => {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    getDashboardStats().then(setStats);
  }, []);

  if (!stats) return <div>加载中...</div>;

  return (
    <div className="dashboard">
      <StatCard title="总项目数" value={stats.overview.total_projects} />
      <StatCard title="总资产数" value={stats.overview.total_assets} />
      <StatCard title="总标注数" value={stats.overview.total_annotations} />
      <StatCard title="活跃用户" value={stats.overview.active_users} />
      
      <RecentProjects projects={stats.recent_projects} />
      <TaskDistributionChart data={stats.task_distribution} />
      <AnnotationTrendChart data={stats.annotation_trend} />
    </div>
  );
};
```

### 项目统计

```javascript
export const getProjectStats = async (projectId) => {
  return apiClient.get(`/stats/projects/${projectId}/stats`);
};

// 使用示例
const ProjectProgress = ({ projectId }) => {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    getProjectStats(projectId).then(setStats);
  }, [projectId]);

  return (
    <div>
      <h3>项目进度</h3>
      <ProgressBar 
        value={stats?.annotation_progress || 0} 
        max={100} 
      />
      <p>{stats?.total_assets} 个资产</p>
      <StatusBreakdown data={stats?.assets_by_status} />
    </div>
  );
};
```

### 租户统计

```javascript
export const getTenantStats = async () => {
  return apiClient.get('/stats/tenant/stats');
};
```

---

## 📦 数据导出

### 导出标注数据

```javascript
// api/export.js
import apiClient from './client';

export const exportAnnotations = async (projectId, format = 'json', filters = {}) => {
  const response = await apiClient.post('/export/annotations', {
    project_id: projectId,
    format, // "json" | "csv" | "coco"
    filters
  });

  return response; // { download_url, file_size, expires_at }
};

// 下载文件
export const downloadExport = async (downloadUrl) => {
  const response = await axios.get(`http://localhost:8000${downloadUrl}`, {
    responseType: 'blob'
  });
  
  // 创建下载链接
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', downloadUrl.split('/').pop());
  document.body.appendChild(link);
  link.click();
  link.remove();
};

// React组件示例
const ExportButton = ({ projectId }) => {
  const [loading, setLoading] = useState(false);

  const handleExport = async (format) => {
    setLoading(true);
    try {
      const result = await exportAnnotations(projectId, format);
      await downloadExport(result.download_url);
      alert('导出成功！');
    } catch (error) {
      alert('导出失败：' + error.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button onClick={() => handleExport('json')} disabled={loading}>
        导出JSON
      </button>
      <button onClick={() => handleExport('csv')} disabled={loading}>
        导出CSV
      </button>
      <button onClick={() => handleExport('coco')} disabled={loading}>
        导出COCO
      </button>
    </div>
  );
};
```

---

## 🔄 批量操作

### 批量更新资产状态

```javascript
// api/batch.js
import apiClient from './client';

export const batchUpdateAssetStatus = async (assetIds, status) => {
  return apiClient.post('/batch/assets/update-status', {
    asset_ids: assetIds,
    annotation_status: status
  });
};

// 使用示例
const selectedAssets = ['uuid1', 'uuid2', 'uuid3'];
const result = await batchUpdateAssetStatus(selectedAssets, 'in_progress');
console.log(`成功更新 ${result.success_count} 个资产`);
```

### 批量审核标注

```javascript
export const batchReviewAnnotations = async (annotationIds, status, comment = '') => {
  return apiClient.post('/batch/annotations/review', {
    annotation_ids: annotationIds,
    review_status: status, // "approved" | "rejected" | "needs_revision"
    review_comment: comment
  });
};

// React组件示例
const BatchReviewPanel = ({ selectedAnnotations }) => {
  const handleBatchApprove = async () => {
    await batchReviewAnnotations(selectedAnnotations, 'approved', '批量通过');
    alert('批量审核完成！');
  };

  return (
    <button onClick={handleBatchApprove}>
      批量通过 ({selectedAnnotations.length})
    </button>
  );
};
```

---

## 🎨 UI组件示例

### 分页组件

```javascript
const Pagination = ({ total, page, pageSize, onChange }) => {
  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="pagination">
      <button 
        disabled={page === 1} 
        onClick={() => onChange(page - 1)}
      >
        上一页
      </button>
      <span>{page} / {totalPages}</span>
      <button 
        disabled={page === totalPages} 
        onClick={() => onChange(page + 1)}
      >
        下一页
      </button>
    </div>
  );
};
```

### 项目列表组件

```javascript
const ProjectList = () => {
  const [projects, setProjects] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    getProjects(page, 20)
      .then(setProjects)
      .finally(() => setLoading(false));
  }, [page]);

  if (loading) return <div>加载中...</div>;

  return (
    <div>
      <div className="project-grid">
        {projects.items.map(project => (
          <ProjectCard key={project.id} project={project} />
        ))}
      </div>
      <Pagination 
        total={projects.total}
        page={page}
        pageSize={20}
        onChange={setPage}
      />
    </div>
  );
};
```

### 文件上传组件

```javascript
const FileUploadZone = ({ projectId, onSuccess }) => {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  const handleDrop = async (e) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    
    setUploading(true);
    try {
      const results = await batchUploadAssets(projectId, files);
      onSuccess(results);
    } catch (error) {
      alert('上传失败：' + error.message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div 
      className="upload-zone"
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
    >
      {uploading ? (
        <div>上传中... {progress}%</div>
      ) : (
        <div>拖拽文件到这里上传</div>
      )}
    </div>
  );
};
```

---

## ⚡ 性能优化建议

### 1. 使用React Query缓存

```javascript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

// 获取项目列表 - 自动缓存
const useProjects = (page) => {
  return useQuery({
    queryKey: ['projects', page],
    queryFn: () => getProjects(page, 20),
    staleTime: 5 * 60 * 1000, // 5分钟内不重新请求
  });
};

// 创建项目 - 自动刷新缓存
const useCreateProject = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: createProject,
    onSuccess: () => {
      queryClient.invalidateQueries(['projects']);
    },
  });
};

// 使用示例
const ProjectsPage = () => {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useProjects(page);
  const createMutation = useCreateProject();

  const handleCreate = async (projectData) => {
    await createMutation.mutateAsync(projectData);
  };

  return <ProjectList projects={data?.items} />;
};
```

### 2. 虚拟滚动大列表

```javascript
import { FixedSizeList } from 'react-window';

const AssetList = ({ assets }) => {
  const Row = ({ index, style }) => (
    <div style={style}>
      <AssetCard asset={assets[index]} />
    </div>
  );

  return (
    <FixedSizeList
      height={600}
      itemCount={assets.length}
      itemSize={200}
      width="100%"
    >
      {Row}
    </FixedSizeList>
  );
};
```

### 3. 图片懒加载

```javascript
const AssetImage = ({ src, alt }) => {
  const [isLoaded, setIsLoaded] = useState(false);
  const imgRef = useRef();

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsLoaded(true);
          observer.disconnect();
        }
      },
      { threshold: 0.1 }
    );

    if (imgRef.current) {
      observer.observe(imgRef.current);
    }

    return () => observer.disconnect();
  }, []);

  return (
    <div ref={imgRef}>
      {isLoaded ? (
        <img src={src} alt={alt} />
      ) : (
        <div className="skeleton" />
      )}
    </div>
  );
};
```

---

## 🐛 错误处理

### 统一错误处理

```javascript
// utils/errorHandler.js
export const handleApiError = (error) => {
  if (error.response) {
    // 服务器返回错误
    const { status, data } = error.response;
    
    switch (status) {
      case 400:
        return '请求参数错误';
      case 401:
        return '未登录或登录已过期';
      case 403:
        return '没有权限执行此操作';
      case 404:
        return '请求的资源不存在';
      case 500:
        return '服务器错误，请稍后重试';
      default:
        return data.message || '请求失败';
    }
  } else if (error.request) {
    // 网络错误
    return '网络连接失败，请检查网络';
  } else {
    return error.message || '未知错误';
  }
};

// 使用示例
try {
  await createProject(projectData);
} catch (error) {
  const message = handleApiError(error);
  toast.error(message);
}
```

---

## 📱 响应式设计建议

### 移动端适配

```javascript
const useIsMobile = () => {
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return isMobile;
};

// 使用示例
const AssetGrid = ({ assets }) => {
  const isMobile = useIsMobile();
  const columns = isMobile ? 2 : 4;

  return (
    <div style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}>
      {assets.map(asset => <AssetCard key={asset.id} asset={asset} />)}
    </div>
  );
};
```

---

## 🔍 调试技巧

### 1. 打印API响应

```javascript
apiClient.interceptors.response.use(
  (response) => {
    console.log('API Response:', response.config.url, response.data);
    return response.data;
  }
);
```

### 2. 监控性能

```javascript
apiClient.interceptors.response.use(
  (response) => {
    const processTime = response.headers['x-process-time'];
    if (processTime > 1) {
      console.warn(`慢请求: ${response.config.url} (${processTime}s)`);
    }
    return response.data;
  }
);
```

---

## 📞 技术支持

遇到问题？查看以下资源：

- **API文档**: http://localhost:8000/docs
- **功能文档**: [NEW_FEATURES.md](NEW_FEATURES.md)
- **初始报告**: [SUCCESS_REPORT.md](SUCCESS_REPORT.md)

---

**更新日期**: 2026-10-13  
**版本**: v1.0.0

<template>
  <div class="project-detail-view">
    <div class="view-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/projects' }">标注项目</el-breadcrumb-item>
        <el-breadcrumb-item>{{ project?.project_name }}</el-breadcrumb-item>
      </el-breadcrumb>
      <div class="header-actions">
        <el-button @click="showUploadDialog = true">
          <el-icon><Upload /></el-icon>
          上传图片
        </el-button>
        <el-button type="primary" @click="showImportDialog = true">
          <el-icon><Download /></el-icon>
          从SmartEye导入
        </el-button>
      </div>
    </div>

    <!-- 项目统计 -->
    <el-row :gutter="24" class="stats-row">
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-icon">
              <el-icon><Picture /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ project?.total_assets || 0 }}</div>
              <div class="stat-label">总图片数</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-icon success">
              <el-icon><Check /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ project?.annotated_count || 0 }}</div>
              <div class="stat-label">已标注</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-icon warning">
              <el-icon><Clock /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">
                {{ (project?.total_assets || 0) - (project?.annotated_count || 0) }}
              </div>
              <div class="stat-label">待标注</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-icon info">
              <el-icon><TrendCharts /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">
                {{ project?.total_assets > 0 ? Math.round((project.annotated_count / project.total_assets) * 100) : 0 }}%
              </div>
              <div class="stat-label">标注进度</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 图片列表 -->
    <el-card class="assets-card">
      <template #header>
        <div class="card-header">
          <span>图片列表</span>
          <el-select v-model="filterStatus" placeholder="筛选状态" style="width: 150px" @change="loadAssets">
            <el-option label="全部" value="" />
            <el-option label="待标注" value="pending" />
            <el-option label="已标注" value="annotated" />
            <el-option label="已审核" value="reviewed" />
          </el-select>
        </div>
      </template>

      <div class="assets-grid" v-loading="loading">
        <div
          v-for="asset in assets"
          :key="asset.id"
          class="asset-item"
          @click="viewAsset(asset)"
        >
          <div class="asset-image">
            <img :src="asset.url" :alt="asset.original_name" />
            <div class="asset-overlay">
              <el-tag :type="getAnnotationStatusType(asset.annotation_status)" size="small">
                {{ getAnnotationStatusText(asset.annotation_status) }}
              </el-tag>
            </div>
          </div>
          <div class="asset-info">
            <div class="asset-name">{{ asset.original_name }}</div>
            <div class="asset-meta">
              {{ asset.width }} × {{ asset.height }}
              <span v-if="asset.source_type === 'smarteye'" class="source-tag">
                <el-icon><Link /></el-icon> SmartEye
              </span>
            </div>
          </div>
        </div>
      </div>

      <el-empty v-if="!loading && assets.length === 0" description="暂无图片" />
    </el-card>

    <!-- 上传对话框 -->
    <el-dialog v-model="showUploadDialog" title="上传图片" width="500px">
      <el-upload
        drag
        multiple
        :auto-upload="false"
        :on-change="handleFileChange"
        accept="image/*"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">
          拖拽文件到此处或 <em>点击上传</em>
        </div>
        <template #tip>
          <div class="el-upload__tip">
            支持 jpg/png 格式图片
          </div>
        </template>
      </el-upload>
      <template #footer>
        <el-button @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" @click="handleUpload" :loading="uploading">
          上传 ({{ uploadFiles.length }})
        </el-button>
      </template>
    </el-dialog>

    <!-- 从SmartEye导入对话框 -->
    <el-dialog v-model="showImportDialog" title="从SmartEye导入" width="500px">
      <p style="color: #A0AEC0; margin-bottom: 20px;">
        选择SmartEye任务，零拷贝导入已下载的图片
      </p>
      <el-alert
        title="提示"
        type="info"
        description="请先在SmartEye页面创建并完成下载任务"
        :closable="false"
        style="margin-bottom: 20px"
      />
      <el-button type="primary" @click="goToSmartEye">
        前往SmartEye
      </el-button>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getProjectDetail, getProjectAssets, uploadImages } from '@/api/project'
import type { Project, Asset } from '@/api/project'

const route = useRoute()
const router = useRouter()

const projectId = route.params.id as string

const loading = ref(false)
const uploading = ref(false)
const showUploadDialog = ref(false)
const showImportDialog = ref(false)

const project = ref<Project>()
const assets = ref<Asset[]>([])
const filterStatus = ref('')

const uploadFiles = ref<File[]>([])

onMounted(() => {
  loadProject()
  loadAssets()
})

async function loadProject() {
  try {
    project.value = await getProjectDetail(projectId) as any
  } catch (error) {
    console.error('加载项目失败:', error)
  }
}

async function loadAssets() {
  loading.value = true
  try {
    const res: any = await getProjectAssets(projectId, {
      annotation_status: filterStatus.value || undefined,
      limit: 100
    })
    assets.value = res.assets
  } catch (error) {
    console.error('加载图片失败:', error)
  } finally {
    loading.value = false
  }
}

function handleFileChange(file: any) {
  uploadFiles.value.push(file.raw)
}

async function handleUpload() {
  if (uploadFiles.value.length === 0) {
    ElMessage.warning('请选择文件')
    return
  }

  uploading.value = true
  try {
    const formData = new FormData()
    uploadFiles.value.forEach(file => {
      formData.append('files', file)
    })

    const res: any = await uploadImages(projectId, formData)
    ElMessage.success(res.message)

    showUploadDialog.value = false
    uploadFiles.value = []
    loadProject()
    loadAssets()
  } catch (error) {
    console.error('上传失败:', error)
  } finally {
    uploading.value = false
  }
}

function viewAsset(asset: Asset) {
  ElMessage.info('标注功能开发中')
}

function goToSmartEye() {
  router.push('/smarteye')
}

function getAnnotationStatusType(status: string) {
  const map: Record<string, any> = {
    pending: 'info',
    annotated: 'success',
    reviewed: 'warning'
  }
  return map[status] || 'info'
}

function getAnnotationStatusText(status: string) {
  const map: Record<string, string> = {
    pending: '待标注',
    annotated: '已标注',
    reviewed: '已审核'
  }
  return map[status] || status
}
</script>

<style scoped>
.project-detail-view {
  max-width: 1400px;
}

.view-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.header-actions {
  display: flex;
  gap: 12px;
}

.stats-row {
  margin-bottom: 24px;
}

.stat-card {
  background: #0F131C;
  border: 1px solid #1E2636;
  border-radius: 12px;
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(56, 189, 248, 0.1);
  border-radius: 12px;
  font-size: 24px;
  color: #38BDF8;
}

.stat-icon.success {
  background: rgba(52, 211, 153, 0.1);
  color: #34D399;
}

.stat-icon.warning {
  background: rgba(251, 191, 36, 0.1);
  color: #FBBF24;
}

.stat-icon.info {
  background: rgba(139, 92, 246, 0.1);
  color: #8B5CF6;
}

.stat-info {
  flex: 1;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #E2E8F0;
  line-height: 1;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 14px;
  color: #A0AEC0;
}

.assets-card {
  background: #0F131C;
  border: 1px solid #1E2636;
  border-radius: 12px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.assets-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
}

.asset-item {
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  background: #161D2B;
  transition: all 0.3s;
}

.asset-item:hover {
  transform: translateY(-4px);
  box-shadow: 0 4px 12px rgba(56, 189, 248, 0.2);
}

.asset-image {
  position: relative;
  width: 100%;
  padding-top: 75%;
  background: #1E2636;
  overflow: hidden;
}

.asset-image img {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.asset-overlay {
  position: absolute;
  top: 8px;
  right: 8px;
}

.asset-info {
  padding: 12px;
}

.asset-name {
  font-size: 14px;
  color: #E2E8F0;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.asset-meta {
  font-size: 12px;
  color: #718096;
  display: flex;
  align-items: center;
  gap: 8px;
}

.source-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #38BDF8;
}

:deep(.el-card__header) {
  background: #161D2B;
  border-bottom: 1px solid #1E2636;
  padding: 16px 20px;
}

:deep(.el-card__body) {
  padding: 20px;
}
</style>

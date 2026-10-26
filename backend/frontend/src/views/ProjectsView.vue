<template>
  <div class="projects-view">
    <div class="view-header">
      <h1>标注项目</h1>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon>
        新建项目
      </el-button>
    </div>

    <!-- 项目列表 -->
    <el-row :gutter="24">
      <el-col
        v-for="project in projects"
        :key="project.id"
        :xs="24"
        :sm="12"
        :md="8"
        :lg="6"
      >
        <el-card class="project-card" shadow="hover" @click="goToProject(project.id)">
          <div class="project-header">
            <h3>{{ project.project_name }}</h3>
            <el-tag :type="getStatusType(project.status)">
              {{ getStatusText(project.status) }}
            </el-tag>
          </div>
          <p class="project-desc">{{ project.description || '暂无描述' }}</p>
          <div class="project-stats">
            <div class="stat-item">
              <span class="stat-label">总数</span>
              <span class="stat-value">{{ project.total_assets }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">已标注</span>
              <span class="stat-value">{{ project.annotated_count }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">进度</span>
              <span class="stat-value">
                {{ project.total_assets > 0 ? Math.round((project.annotated_count / project.total_assets) * 100) : 0 }}%
              </span>
            </div>
          </div>
          <div class="project-footer">
            <span class="project-time">{{ formatTime(project.created_at) }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 创建项目对话框 -->
    <el-dialog v-model="showCreateDialog" title="新建标注项目" width="600px">
      <el-form :model="projectForm" label-width="100px">
        <el-form-item label="项目名称">
          <el-input v-model="projectForm.project_name" placeholder="例：红衣人检测" />
        </el-form-item>
        <el-form-item label="项目描述">
          <el-input
            v-model="projectForm.description"
            type="textarea"
            :rows="3"
            placeholder="项目简介（可选）"
          />
        </el-form-item>
        <el-form-item label="任务类型">
          <el-select v-model="projectForm.task_type" placeholder="请选择任务类型" style="width: 100%">
            <el-option label="目标检测" value="detection" />
            <el-option label="图像分类" value="classification" />
            <el-option label="图像描述" value="description" />
          </el-select>
        </el-form-item>
        <el-form-item label="标签配置" v-if="projectForm.task_type === 'detection'">
          <el-input
            v-model="labelInput"
            placeholder="输入标签名（回车添加）"
            @keyup.enter="addLabel"
          />
          <div class="labels-container">
            <el-tag
              v-for="(label, index) in labels"
              :key="index"
              closable
              @close="removeLabel(index)"
              style="margin: 4px"
            >
              {{ label }}
            </el-tag>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreateProject" :loading="creating">
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getProjects, createProject } from '@/api/project'
import type { Project, CreateProjectParams } from '@/api/project'

const router = useRouter()

const projects = ref<Project[]>([])
const showCreateDialog = ref(false)
const creating = ref(false)

const projectForm = ref<Partial<CreateProjectParams>>({
  project_name: '',
  description: '',
  task_type: 'detection'
})

const labelInput = ref('')
const labels = ref<string[]>(['person', 'car'])

onMounted(() => {
  loadProjects()
})

async function loadProjects() {
  try {
    const res: any = await getProjects({ limit: 100 })
    projects.value = res.projects
  } catch (error) {
    console.error('加载项目失败:', error)
  }
}

function addLabel() {
  if (labelInput.value && !labels.value.includes(labelInput.value)) {
    labels.value.push(labelInput.value)
    labelInput.value = ''
  }
}

function removeLabel(index: number) {
  labels.value.splice(index, 1)
}

async function handleCreateProject() {
  if (!projectForm.value.project_name || !projectForm.value.task_type) {
    ElMessage.warning('请填写必填项')
    return
  }

  creating.value = true
  try {
    const labelSchema = {
      task_type: projectForm.value.task_type,
      labels: labels.value
    }

    await createProject({
      project_name: projectForm.value.project_name!,
      description: projectForm.value.description,
      task_type: projectForm.value.task_type!,
      label_schema: labelSchema
    })

    ElMessage.success('项目创建成功')
    showCreateDialog.value = false
    loadProjects()

    // 重置表单
    projectForm.value = {
      project_name: '',
      description: '',
      task_type: 'detection'
    }
    labels.value = ['person', 'car']
  } catch (error) {
    console.error('创建项目失败:', error)
  } finally {
    creating.value = false
  }
}

function goToProject(projectId: string) {
  router.push(`/projects/${projectId}`)
}

function getStatusType(status: string) {
  const map: Record<string, any> = {
    draft: 'info',
    active: 'success',
    completed: 'warning'
  }
  return map[status] || 'info'
}

function getStatusText(status: string) {
  const map: Record<string, string> = {
    draft: '草稿',
    active: '进行中',
    completed: '已完成'
  }
  return map[status] || status
}

function formatTime(time: string) {
  return new Date(time).toLocaleDateString('zh-CN')
}
</script>

<style scoped>
.projects-view {
  max-width: 1400px;
}

.view-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.view-header h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: #E2E8F0;
}

.project-card {
  background: #0F131C;
  border: 1px solid #1E2636;
  border-radius: 12px;
  margin-bottom: 24px;
  cursor: pointer;
  transition: all 0.3s;
}

.project-card:hover {
  border-color: #38BDF8;
  transform: translateY(-2px);
}

.project-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.project-header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #E2E8F0;
  flex: 1;
}

.project-desc {
  margin: 0 0 16px 0;
  color: #A0AEC0;
  font-size: 14px;
  line-height: 1.5;
  min-height: 42px;
}

.project-stats {
  display: flex;
  justify-content: space-between;
  padding: 16px 0;
  border-top: 1px solid #1E2636;
  border-bottom: 1px solid #1E2636;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-label {
  font-size: 12px;
  color: #A0AEC0;
  margin-bottom: 4px;
}

.stat-value {
  font-size: 20px;
  font-weight: 600;
  color: #38BDF8;
}

.project-footer {
  margin-top: 12px;
}

.project-time {
  font-size: 12px;
  color: #718096;
}

.labels-container {
  margin-top: 8px;
}

:deep(.el-card__body) {
  padding: 20px;
}
</style>

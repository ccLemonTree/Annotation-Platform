<template>
  <div class="smarteye-view">
    <div class="view-header">
      <h1>SmartEye 数据拉取</h1>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon>
        新建任务
      </el-button>
    </div>

    <!-- 任务列表 -->
    <el-card class="task-list-card">
      <template #header>
        <div class="card-header">
          <span>任务列表</span>
          <el-button text @click="loadTasks">
            <el-icon><Refresh /></el-icon>
          </el-button>
        </div>
      </template>

      <el-table :data="tasks" style="width: 100%" v-loading="loading">
        <el-table-column prop="task_name" label="任务名称" width="200" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="200">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :status="row.status === 'completed' ? 'success' : undefined"
            />
          </template>
        </el-table-column>
        <el-table-column prop="downloaded_count" label="已下载" width="100" />
        <el-table-column prop="total_count" label="总数" width="100" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" @click="viewTaskDetail(row.id)">
              详情
            </el-button>
            <el-button
              text
              type="success"
              @click="showImportDialog(row)"
              :disabled="row.status !== 'completed'"
            >
              导入项目
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建任务对话框 -->
    <el-dialog v-model="showCreateDialog" title="新建下载任务" width="600px">
      <el-form :model="taskForm" label-width="100px">
        <el-form-item label="任务名称">
          <el-input v-model="taskForm.task_name" placeholder="例：红衣人检测数据集" />
        </el-form-item>
        <el-form-item label="平台">
          <el-select v-model="taskForm.platform_id" placeholder="请选择平台" style="width: 100%">
            <el-option
              v-for="platform in platforms"
              :key="platform.id"
              :label="platform.platform_name"
              :value="platform.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="dateRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="设备ID">
          <el-input v-model="taskForm.device_id" placeholder="可选" />
        </el-form-item>
        <el-form-item label="告警类型">
          <el-input v-model="taskForm.alarm_type" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreateTask" :loading="creating">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 导入项目对话框 -->
    <el-dialog v-model="showImportDialogVisible" title="导入到标注项目" width="500px">
      <el-form label-width="100px">
        <el-form-item label="选择项目">
          <el-select v-model="importForm.project_id" placeholder="请选择项目" style="width: 100%">
            <el-option
              v-for="project in projects"
              :key="project.id"
              :label="project.project_name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
        <el-alert
          title="零拷贝导入"
          type="info"
          description="引用已下载的图片，无需重复下载，瞬间完成"
          :closable="false"
          style="margin-bottom: 20px"
        />
      </el-form>
      <template #footer>
        <el-button @click="showImportDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleImportToProject" :loading="importing">
          导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getPlatforms, createTask, getTasks, importToProject } from '@/api/smarteye'
import { getProjects } from '@/api/project'
import type { Platform, SmartEyeTask, CreateTaskParams, ImportToProjectParams } from '@/api/smarteye'
import type { Project } from '@/api/project'

const loading = ref(false)
const creating = ref(false)
const importing = ref(false)
const showCreateDialog = ref(false)
const showImportDialogVisible = ref(false)

const platforms = ref<Platform[]>([])
const tasks = ref<SmartEyeTask[]>([])
const projects = ref<Project[]>([])

const dateRange = ref<[Date, Date]>()
const taskForm = ref<Partial<CreateTaskParams>>({
  task_name: '',
  platform_id: '',
  device_id: '',
  alarm_type: ''
})

const importForm = ref<Partial<ImportToProjectParams>>({
  task_id: '',
  project_id: ''
})

onMounted(() => {
  loadPlatforms()
  loadTasks()
  loadProjects()
})

async function loadPlatforms() {
  try {
    const res: any = await getPlatforms()
    platforms.value = res.platforms
  } catch (error) {
    console.error('加载平台失败:', error)
  }
}

async function loadTasks() {
  loading.value = true
  try {
    const res: any = await getTasks({ limit: 100 })
    tasks.value = res.tasks
  } catch (error) {
    console.error('加载任务失败:', error)
  } finally {
    loading.value = false
  }
}

async function loadProjects() {
  try {
    const res: any = await getProjects({ limit: 100 })
    projects.value = res.projects
  } catch (error) {
    console.error('加载项目失败:', error)
  }
}

async function handleCreateTask() {
  if (!taskForm.value.task_name || !taskForm.value.platform_id || !dateRange.value) {
    ElMessage.warning('请填写必填项')
    return
  }

  creating.value = true
  try {
    await createTask({
      task_name: taskForm.value.task_name!,
      platform_id: taskForm.value.platform_id!,
      start_time: dateRange.value[0].toISOString(),
      end_time: dateRange.value[1].toISOString(),
      device_id: taskForm.value.device_id,
      alarm_type: taskForm.value.alarm_type
    })

    ElMessage.success('任务创建成功')
    showCreateDialog.value = false
    loadTasks()

    // 重置表单
    taskForm.value = {
      task_name: '',
      platform_id: '',
      device_id: '',
      alarm_type: ''
    }
    dateRange.value = undefined
  } catch (error) {
    console.error('创建任务失败:', error)
  } finally {
    creating.value = false
  }
}

function showImportDialog(task: SmartEyeTask) {
  importForm.value.task_id = task.id
  showImportDialogVisible.value = true
}

async function handleImportToProject() {
  if (!importForm.value.project_id) {
    ElMessage.warning('请选择项目')
    return
  }

  importing.value = true
  try {
    const res: any = await importToProject({
      task_id: importForm.value.task_id!,
      project_id: importForm.value.project_id!
    })

    ElMessage.success(res.message)
    showImportDialogVisible.value = false
  } catch (error) {
    console.error('导入失败:', error)
  } finally {
    importing.value = false
  }
}

function viewTaskDetail(taskId: string) {
  ElMessage.info('任务详情功能开发中')
}

function getStatusType(status: string) {
  const map: Record<string, any> = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger'
  }
  return map[status] || 'info'
}

function getStatusText(status: string) {
  const map: Record<string, string> = {
    pending: '待处理',
    running: '进行中',
    completed: '已完成',
    failed: '失败'
  }
  return map[status] || status
}

function formatTime(time: string) {
  return new Date(time).toLocaleString('zh-CN')
}
</script>

<style scoped>
.smarteye-view {
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

.task-list-card {
  background: #0F131C;
  border: 1px solid #1E2636;
  border-radius: 12px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

:deep(.el-card__header) {
  background: #161D2B;
  border-bottom: 1px solid #1E2636;
  padding: 16px 20px;
}

:deep(.el-table) {
  background: transparent;
  color: #E2E8F0;
}

:deep(.el-table__header-wrapper) {
  background: #161D2B;
}

:deep(.el-table th) {
  background: #161D2B;
  color: #A0AEC0;
  border-bottom: 1px solid #1E2636;
}

:deep(.el-table td) {
  border-bottom: 1px solid #1E2636;
}

:deep(.el-table tr) {
  background: transparent;
}

:deep(.el-table__row:hover) {
  background: #161D2B !important;
}
</style>

<template>
  <div id="app">
    <el-container class="app-container">
      <!-- 侧边栏 -->
      <el-aside width="240px" class="app-aside">
        <div class="logo">
          <h2>数据管理平台</h2>
        </div>
        <el-menu
          :default-active="activeMenu"
          router
          background-color="#0F131C"
          text-color="#A0AEC0"
          active-text-color="#38BDF8"
        >
          <el-menu-item index="/smarteye">
            <el-icon><Download /></el-icon>
            <span>SmartEye</span>
          </el-menu-item>
          <el-menu-item index="/projects">
            <el-icon><Files /></el-icon>
            <span>标注项目</span>
          </el-menu-item>
          <el-menu-item index="/training">
            <el-icon><TrendCharts /></el-icon>
            <span>模型训练</span>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <!-- 主内容区 -->
      <el-container>
        <!-- 顶部栏 -->
        <el-header class="app-header">
          <div class="header-left">
            <el-breadcrumb separator="/">
              <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
              <el-breadcrumb-item v-if="breadcrumb">{{ breadcrumb }}</el-breadcrumb-item>
            </el-breadcrumb>
          </div>
          <div class="header-right">
            <el-icon class="header-icon"><Bell /></el-icon>
            <el-icon class="header-icon"><User /></el-icon>
          </div>
        </el-header>

        <!-- 内容 -->
        <el-main class="app-main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const activeMenu = computed(() => {
  return route.path
})

const breadcrumb = computed(() => {
  const map: Record<string, string> = {
    '/smarteye': 'SmartEye数据拉取',
    '/projects': '标注项目',
    '/training': '模型训练'
  }
  return map[route.path] || ''
})
</script>

<style scoped>
.app-container {
  height: 100vh;
  background: #0A0D12;
}

.app-aside {
  background: #0F131C;
  border-right: 1px solid #1E2636;
}

.logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid #1E2636;
}

.logo h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #E2E8F0;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #0F131C;
  border-bottom: 1px solid #1E2636;
  padding: 0 24px;
}

.header-left {
  flex: 1;
}

.header-right {
  display: flex;
  gap: 16px;
}

.header-icon {
  font-size: 20px;
  color: #A0AEC0;
  cursor: pointer;
  transition: color 0.3s;
}

.header-icon:hover {
  color: #38BDF8;
}

.app-main {
  background: #0A0D12;
  padding: 24px;
  overflow-y: auto;
}

/* Element Plus菜单样式覆盖 */
:deep(.el-menu) {
  border-right: none;
}

:deep(.el-menu-item) {
  margin: 4px 12px;
  border-radius: 8px;
  transition: all 0.3s;
}

:deep(.el-menu-item:hover) {
  background-color: #161D2B !important;
}

:deep(.el-menu-item.is-active) {
  background-color: rgba(56, 189, 248, 0.1) !important;
}

:deep(.el-breadcrumb__inner) {
  color: #A0AEC0;
}

:deep(.el-breadcrumb__inner:hover) {
  color: #38BDF8;
}
</style>

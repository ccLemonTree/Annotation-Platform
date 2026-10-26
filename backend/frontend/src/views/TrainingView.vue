<template>
  <div class="training-view">
    <div class="view-header">
      <h1>模型训练</h1>
      <el-button type="primary" disabled>
        <el-icon><Plus /></el-icon>
        新建训练任务
      </el-button>
    </div>

    <el-empty description="训练功能开发中" />
  </div>
</template>

<script setup lang="ts">
// 训练功能占位页面
</script>

<style scoped>
.training-view {
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
</style>

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/smarteye'
    },
    {
      path: '/smarteye',
      name: 'smarteye',
      component: () => import('@/views/SmartEyeView.vue')
    },
    {
      path: '/projects',
      name: 'projects',
      component: () => import('@/views/ProjectsView.vue')
    },
    {
      path: '/projects/:id',
      name: 'project-detail',
      component: () => import('@/views/ProjectDetailView.vue')
    },
    {
      path: '/training',
      name: 'training',
      component: () => import('@/views/TrainingView.vue')
    }
  ]
})

export default router

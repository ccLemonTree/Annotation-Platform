// 路由配置和应用入口
import { lazy, Suspense } from 'react';
import { Alert, Button, Space, Spin } from 'antd';
import { createBrowserRouter, createRoutesFromElements, RouterProvider, Route, Navigate, NavLink, Outlet, useSearchParams, useParams } from 'react-router-dom';
import PlatformProvider from './ui/PlatformProvider';
import LoginPage from './pages/LoginPage';
import MainLayout from './layouts/MainLayout';
import ProjectsPage from './pages/ProjectsPage';
import PrivateRoute from './components/PrivateRoute';
import { useCurrentUser } from './components/currentUser';
const WorkflowTemplatesPage = lazy(() => import('./pages/WorkflowTemplatesPage'));
const ProjectWorkflowPage = lazy(() => import('./pages/ProjectWorkflowPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const AssetsPage = lazy(() => import('./pages/AssetsPage'));
const AnnotationsPage = lazy(() => import('./pages/AnnotationsPage'));
const SmartEyePage = lazy(() => import('./pages/SmartEyePage'));
const AnnotationWorkspace = lazy(() => import('./pages/AnnotationWorkspace'));
const VectorAnnotationWorkspace = lazy(() => import('./pages/VectorAnnotationWorkspace'));
const ModelServicesPage = lazy(() => import('./pages/ModelServicesPage'));
const ModelPreviewPage = lazy(() => import('./pages/ModelPreviewPage'));
const StorageMaintenancePage = lazy(() => import('./pages/StorageMaintenancePage'));
const AutomationPage = lazy(() => import('./pages/AutomationPage'));
const ReviewQueuePage = lazy(() => import('./pages/ReviewQueuePage'));
const ImageSemanticsPage = lazy(() => import('./pages/ImageSemanticsPage'));
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage'));
const GpuResourcesPage = lazy(() => import('./pages/GpuResourcesPage'));
const ModelDeploymentsPage = lazy(() => import('./pages/ModelDeploymentsPage'));
const ModelRepositoryPage = lazy(() => import('./pages/ModelRepositoryPage'));
const GpuTerminalPage = lazy(() => import('./pages/GpuTerminalPage'));
const GpuServersPage = lazy(() => import('./pages/GpuServersPage'));

function LegacyValidationRoute(){
  const [params]=useSearchParams();const next=new URLSearchParams(params);next.set('view','validation');
  return <Navigate replace to={`/model-preview?${next}`}/>;
}

function ProjectPreparationRoute() {
  const {projectId}=useParams();
  const [params]=useSearchParams();
  const next=new URLSearchParams(params);next.set('project_id',projectId!);next.set('prepare','1');
  if(next.get('tool')==='attributes')next.delete('tool');
  return <Navigate replace to={`/annotations?${next}`}/>;
}

function LegacyTrainingRoute() {
  const [params]=useSearchParams();
  const projectId=params.get('project_id');
  return <Navigate replace to={projectId?`/projects/${encodeURIComponent(projectId)}/deliver?${params}`:'/projects'}/>;
}

function ImageLibraryRoute() {
  const [params] = useSearchParams();
  const projectId = params.get('project_id');
  return <Navigate replace to={projectId?`/projects/${encodeURIComponent(projectId)}/images`:'/projects/data-sources'} />;
}

function ProjectManagementRoute() {
  const admin=useCurrentUser()?.role==='admin';
  return <>
    <div className="project-workflow"><nav aria-label="项目管理">
      <NavLink to="/projects" end>项目列表</NavLink>
      <NavLink to="/projects/data-sources">共享数据源</NavLink>
      {admin&&<NavLink to="/projects/workflow-templates">工作流模板</NavLink>}
    </nav></div>
    <Outlet />
  </>;
}

function WorkflowTemplateManagementRoute() {
  return useCurrentUser()?.role==='admin'?<WorkflowTemplatesPage/>:<Navigate replace to="/annotations"/>;
}

const router = createBrowserRouter(createRoutesFromElements(
<>
          {/* 登录页 */}
          <Route path="/login" element={<LoginPage />} />

          {/* 主应用路由 */}
          <Route
            path="/"
            errorElement={<div style={{padding:24}}><Alert type="error" showIcon title="页面加载失败" description="请重新加载页面；仍无法打开时，可以返回项目列表。" action={<Space wrap><Button onClick={()=>window.location.reload()}>重新加载页面</Button><Button href="/projects">返回项目列表</Button></Space>}/></div>}
            element={
              <PrivateRoute>
                <MainLayout />
              </PrivateRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="detection-query" element={<SmartEyePage />} />
            <Route path="projects" element={<ProjectManagementRoute />}>
              <Route index element={<ProjectsPage />} />
              <Route path="data-sources" element={<AssetsPage />} />
              <Route path="workflow-templates" element={<WorkflowTemplateManagementRoute />} />
            </Route>
            <Route path="assets" element={<ImageLibraryRoute />} />
            <Route path="projects/:projectId/images" element={<AssetsPage />} />
            <Route path="projects/:projectId/capture" element={<ProjectWorkflowPage stage="capture" />} />
            <Route path="projects/:projectId/automation" element={<ProjectWorkflowPage stage="automation" />} />
            <Route path="projects/:projectId/review" element={<ProjectWorkflowPage stage="review" />} />
            <Route path="projects/:projectId/prepare" element={<ProjectPreparationRoute />} />
            <Route path="projects/:projectId/settings" element={<ProjectsPage />} />
            <Route path="projects/:projectId/attributes" element={<ProjectPreparationRoute />} />
            <Route path="projects/:projectId/deliver" element={<ProjectWorkflowPage stage="deliver" />} />
            <Route path="projects/:projectId/test" element={<ProjectWorkflowPage stage="test" />} />
            <Route path="annotations" element={<AnnotationsPage />} />
            <Route path="annotate/:assetId" element={<AnnotationWorkspace />} />
            <Route path="annotate/project/:projectId" element={<VectorAnnotationWorkspace />} />
            <Route path="models" element={<ModelServicesPage />} />
            <Route path="model-preview" element={<ModelPreviewPage />} />
            <Route path="vector-test" element={<Navigate replace to="/model-preview?view=vector-test" />} />
            <Route path="automation" element={<AutomationPage />} />
            <Route path="review-queue" element={<ReviewQueuePage />} />
            <Route path="image-semantics" element={<Navigate to="/annotations" replace />} />
            <Route path="image-semantics/history" element={<ImageSemanticsPage />} />
            <Route path="analytics" element={<AnalyticsPage />} />
            <Route path="validation" element={<LegacyValidationRoute />} />
            <Route path="storage-maintenance" element={<StorageMaintenancePage />} />
            <Route path="gpu-resources" element={<GpuResourcesPage />} />
            <Route path="gpu-servers" element={<Suspense fallback={<Spin />}><GpuServersPage /></Suspense>} />
            <Route path="training" element={<LegacyTrainingRoute />} />
            <Route path="model-deployments" element={<Suspense fallback={<Spin />}><ModelDeploymentsPage /></Suspense>} />
            <Route path="model-repository" element={<Suspense fallback={<Spin />}><ModelRepositoryPage /></Suspense>} />
            <Route path="gpu-terminal" element={<Suspense fallback={<Spin />}><GpuTerminalPage /></Suspense>} />
            <Route path="smarteye" element={<SmartEyePage />} />
          </Route>

          {/* 404 重定向 */}
          <Route path="*" element={<Navigate to="/" replace />} />
</>
));

function App() {
  return (
    <PlatformProvider>
      <RouterProvider router={router} />
    </PlatformProvider>
  );
}

export default App;

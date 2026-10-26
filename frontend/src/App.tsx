// 路由配置和应用入口
import { lazy, Suspense } from 'react';
import { Spin } from 'antd';
import { createBrowserRouter, createRoutesFromElements, RouterProvider, Route, Navigate, useSearchParams } from 'react-router-dom';
import PlatformProvider from './ui/PlatformProvider';
import LoginPage from './pages/LoginPage';
import MainLayout from './layouts/MainLayout';
import ProjectWorkflowPage from './pages/ProjectWorkflowPage';
import DashboardPage from './pages/DashboardPage';
import ProjectsPage from './pages/ProjectsPage';
import AssetsPage from './pages/AssetsPage';
import AnnotationsPage from './pages/AnnotationsPage';
import SmartEyePage from './pages/SmartEyePage';
import AnnotationWorkspace from './pages/AnnotationWorkspace';
import VectorAnnotationWorkspace from './pages/VectorAnnotationWorkspace';
import ModelServicesPage from './pages/ModelServicesPage';
import ModelPreviewPage from './pages/ModelPreviewPage';
import StorageMaintenancePage from './pages/StorageMaintenancePage';
import AutomationPage from './pages/AutomationPage';
import ImageSemanticsPage from './pages/ImageSemanticsPage';
import AnalyticsPage from './pages/AnalyticsPage';
import ValidationPage from './pages/ValidationPage';
import GpuResourcesPage from './pages/GpuResourcesPage';
import TrainingPage from './pages/TrainingPage';
import PrivateRoute from './components/PrivateRoute';
const ModelDeploymentsPage = lazy(() => import('./pages/ModelDeploymentsPage'));
const GpuTerminalPage = lazy(() => import('./pages/GpuTerminalPage'));

function ImageLibraryRoute() {
  const [params] = useSearchParams();
  const projectId = params.get('project_id');
  return projectId ? <Navigate replace to={`/projects/${encodeURIComponent(projectId)}/images`} /> : <AssetsPage />;
}

const router = createBrowserRouter(createRoutesFromElements(
<>
          {/* 登录页 */}
          <Route path="/login" element={<LoginPage />} />

          {/* 主应用路由 */}
          <Route
            path="/"
            element={
              <PrivateRoute>
                <MainLayout />
              </PrivateRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="detection-query" element={<SmartEyePage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="assets" element={<ImageLibraryRoute />} />
            <Route path="projects/:projectId/images" element={<AssetsPage />} />
            <Route path="projects/:projectId/prepare" element={<ProjectWorkflowPage stage="prepare" />} />
            <Route path="projects/:projectId/deliver" element={<ProjectWorkflowPage stage="deliver" />} />
            <Route path="annotations" element={<AnnotationsPage />} />
            <Route path="annotate/:assetId" element={<AnnotationWorkspace />} />
            <Route path="annotate/project/:projectId" element={<VectorAnnotationWorkspace />} />
            <Route path="models" element={<ModelServicesPage />} />
            <Route path="model-preview" element={<ModelPreviewPage />} />
            <Route path="automation" element={<AutomationPage />} />
            <Route path="image-semantics" element={<Navigate to="/annotations" replace />} />
            <Route path="image-semantics/history" element={<ImageSemanticsPage />} />
            <Route path="analytics" element={<AnalyticsPage />} />
            <Route path="validation" element={<ValidationPage />} />
            <Route path="storage-maintenance" element={<StorageMaintenancePage />} />
            <Route path="gpu-resources" element={<GpuResourcesPage />} />
            <Route path="training" element={<TrainingPage />} />
            <Route path="model-deployments" element={<Suspense fallback={<Spin />}><ModelDeploymentsPage /></Suspense>} />
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

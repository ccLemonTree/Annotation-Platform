import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Drawer, Select, Space, Spin, Tabs } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import { getProject, type Project } from '../api/projects';
import type { WorkflowHistoryConfig } from '../api/projectWork';
import ProjectProcessingWorkflow from '../components/ProjectProcessingWorkflow';
import ProjectAnnotationScope from '../components/ProjectAnnotationScope';
import { ProjectAttributeConfigDialog } from '../components/ObjectAttributes';
import AutomationPage from './AutomationPage';

export default function SmartEyePage({ embeddedProject }: { embeddedProject?: Project } = {}) {
  const [params, setParams] = useSearchParams();
  const projectId = embeddedProject?.id || params.get('project_id');
  const requestedAutomation = params.get('automation') === '1';
  const frame = useRef<HTMLIFrameElement>(null), request = useRef(0);
  const [project, setProject] = useState<Project>();
  const [opening, setOpening] = useState(false), [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<'workflow' | 'history'>('workflow');
  const [historySource, setHistorySource] = useState<'workflow' | 'automation'>('workflow');
  const [loadedWorkflow, setLoadedWorkflow] = useState<{ config: WorkflowHistoryConfig; seq: number }>();
  const [editingAttributes, setEditingAttributes] = useState(false);
  const [attributeCropLabels, setAttributeCropLabels] = useState<string[]>([]);
  const [attributeRevision, setAttributeRevision] = useState(0);

  const openAutomation = useCallback(async (id: string) => {
    const current = ++request.current; setOpening(true); setError('');
    try {
      const selected = await getProject(id);
      if (current !== request.current) return;
      if (selected.task_type === 'segmentation') { setError('图像分割项目暂不支持批量处理工作流'); return; }
      setActiveTab('workflow');
      setHistorySource('workflow');
      setProject(selected);
    } catch { if (current === request.current) setError('项目读取失败，请重试打开批量处理'); }
    finally { if (current === request.current) setOpening(false); }
  }, []);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === 'smarteye:open-automation' && typeof event.data.projectId === 'string' && event.data.projectId)
        void openAutomation(event.data.projectId);
    };
    window.addEventListener('message', receive);
    return () => { window.removeEventListener('message', receive); request.current++; };
  }, [openAutomation]);

  useEffect(() => { if (projectId && requestedAutomation) void openAutomation(projectId); }, [projectId, requestedAutomation, openAutomation]);

  return <>
    {error && <Alert type="error" showIcon title={error} closable afterClose={() => setError('')} />}
    {opening && <Spin description="读取项目…"><div style={{ height: 32 }} /></Spin>}
    <iframe ref={frame} title="SmartEye 数据拉取" src={`/smarteye/index.html${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`} className="smarteye-frame" />
    <Drawer
      title={project ? `${project.project_name} · 批量处理与 AI 工作流` : '批量处理与 AI 工作流'}
      open={!!project}
      size={1280}
      styles={{ wrapper: { maxWidth: '100vw' } }}
      destroyOnHidden
      extra={project && <Link to={`/projects/${project.id}/prepare`} onClick={() => setProject(undefined)}>打开完整批量处理页 →</Link>}
      onClose={() => {
        setProject(undefined);
        if (requestedAutomation) {
          const next = new URLSearchParams(params);
          next.delete('automation');
          setParams(next, { replace: true });
        }
      }}
    >
      {project && (
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <ProjectAnnotationScope project={project} onSaved={updated => setProject(updated)} />
          <Tabs
            activeKey={activeTab}
            onChange={key => setActiveTab(key as 'workflow' | 'history')}
            items={[
              {
                key: 'workflow',
                label: '处理工作流',
                children: (
                  <ProjectProcessingWorkflow
                    key={project.id}
                    project={project}
                    loadedHistory={loadedWorkflow}
                    attributeRevision={attributeRevision}
                    onConfigureAttributes={(labels = []) => { setAttributeCropLabels(labels); setEditingAttributes(true); }}
                    onNavigate={tab => setActiveTab(tab)}
                  />
                ),
              },
              {
                key: 'history',
                label: '历史任务',
                children: (
                  <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
                    {project.task_type === 'detection' && (
                      <Space>
                        <label htmlFor="smarteye-history-source">记录来源</label>
                        <Select
                          id="smarteye-history-source"
                          value={historySource}
                          style={{ width: 200 }}
                          options={[
                            { value: 'workflow', label: '工作流记录' },
                            { value: 'automation', label: '历史预标注与审核' },
                          ]}
                          onChange={setHistorySource}
                        />
                      </Space>
                    )}
                    {historySource === 'automation' && project.task_type === 'detection' ? (
                      <AutomationPage key={project.id} embeddedProject={project} />
                    ) : (
                      <ProjectProcessingWorkflow
                        key={project.id}
                        project={project}
                        history
                        onLoadHistory={cfg => {
                          setLoadedWorkflow(prev => ({ config: cfg, seq: (prev?.seq || 0) + 1 }));
                          setActiveTab('workflow');
                        }}
                        onConfigureAttributes={(labels = []) => { setAttributeCropLabels(labels); setEditingAttributes(true); }}
                        onNavigate={tab => setActiveTab(tab)}
                      />
                    )}
                  </Space>
                ),
              },
            ]}
          />
          <ProjectAttributeConfigDialog
            key={project.id + JSON.stringify(project.label_schema?.object_attributes)}
            project={project}
            cropLabels={attributeCropLabels}
            onTemplateSaved={() => setAttributeRevision(v => v + 1)}
            open={editingAttributes}
            onClose={() => setEditingAttributes(false)}
            onSaved={updated => setProject(updated)}
          />
        </Space>
      )}
    </Drawer>
  </>;
}

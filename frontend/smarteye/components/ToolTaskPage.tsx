import { apiFetch, download } from '../network';
import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from '../constants';
import type { PlatformOption } from '../types';

const ToolTaskPage: React.FC = () => {
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [platformName, setPlatformName] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [maxPages, setMaxPages] = useState('234');

  const [tasks, setTasks] = useState<any[]>([]);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    // 初始化时间
    const now = new Date();
    const pad = (n: number) => n.toString().padStart(2, '0');
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const d1 = `${yesterday.getFullYear()}-${pad(yesterday.getMonth() + 1)}-${pad(yesterday.getDate())}`;
    const d2 = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

    setStartTime(`${d1}T19:00:00`);
    setEndTime(`${d2}T00:00:00`);

    // 加载平台列表
    apiFetch(`${API_BASE_URL}/platform-list`)
      .then(r => r.json())
      .then(d => { if (d.code === 0) setPlatforms(d.data); })
      .catch(console.error);

    // 加载任务列表
    loadTasks();
  }, []);

  const loadTasks = async () => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/tool-tasks`);
      const data = await res.json();
      if (data.code === 0) {
        setTasks(data.data);
      }
    } catch (e) {
      console.error('加载任务列表失败', e);
    }
  };

  const handleCreate = async () => {
    if (!platformName.trim()) {
      alert('请选择平台');
      return;
    }
    if (!startTime || !endTime) {
      alert('请选择时间范围');
      return;
    }

    setCreating(true);
    try {
      const formatTime = (t: string) => {
        const parts = t.replace('T', ' ').split(':');
        if (parts.length === 2) return `${t.replace('T', ' ')}:00`;
        return t.replace('T', ' ');
      };

      const res = await apiFetch(`${API_BASE_URL}/tool-tasks/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          platform_name: platformName.trim(),
          device_id: deviceId.trim(),
          start_time: formatTime(startTime),
          end_time: formatTime(endTime),
          max_pages: parseInt(maxPages) || 0,
        }),
      });

      const data = await res.json();
      if (data.code === 0) {
        alert('任务已创建');
        loadTasks();

        // 开始轮询任务状态
        pollTask(data.data.task_id);
      } else {
        alert('创建失败: ' + data.msg);
      }
    } catch (e: any) {
      alert('创建失败: ' + e.message);
    } finally {
      setCreating(false);
    }
  };

  const pollTask = (taskId: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await apiFetch(`${API_BASE_URL}/tool-tasks/${taskId}`);
        const data = await res.json();
        if (data.code === 0) {
          setTasks(prev => {
            const idx = prev.findIndex(t => t.task_id === taskId);
            if (idx >= 0) {
              const newTasks = [...prev];
              newTasks[idx] = data.data;
              return newTasks;
            } else {
              return [data.data, ...prev];
            }
          });

          if (data.data.status === 'completed' || data.data.status === 'failed') {
            clearInterval(interval);
          }
        }
      } catch (e) {
        console.error('轮询任务状态失败', e);
      }
    }, 2000);
  };

  const handleDownload = (taskId: string) => {
    void download(`${API_BASE_URL}/tool-tasks/${taskId}/download`);
  };

  const ic = "w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 text-sm rounded-lg focus:ring-primary focus:border-primary block p-2.5";

  return (
    <div className="flex-1 overflow-auto bg-background-light dark:bg-background-dark transition-colors duration-300">
      {/* Header */}
      <div className="bg-white dark:bg-surface-dark border-b border-slate-200 dark:border-slate-800 px-6 pt-5 pb-5 shadow-sm">
        <div className="max-w-7xl mx-auto space-y-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">执行工具任务</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              批量下载台账图片，支持自定义平台、设备、时间范围和最大页数。
            </p>
          </div>

          {/* Form */}
          <div className="grid grid-cols-12 gap-4 items-end">
            <div className="col-span-12 sm:col-span-6 lg:col-span-2 space-y-1.5">
              <label htmlFor="tooltaskpage-field-1" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">
                平台 <span className="text-red-500">*</span>
              </label>
              <select id="tooltaskpage-field-1" className={ic} value={platformName} onChange={e => setPlatformName(e.target.value)}>
                <option value="">请选择平台</option>
                {platforms.map(p => (
                  <option key={p.label} value={p.label}>{p.label}</option>
                ))}
              </select>
            </div>

            <div className="col-span-12 sm:col-span-6 lg:col-span-2 space-y-1.5">
              <label htmlFor="tooltaskpage-field-2" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">设备ID</label>
              <input id="tooltaskpage-field-2" type="text" className={ic} placeholder="可选，留空查全部" value={deviceId} onChange={e => setDeviceId(e.target.value)} />
            </div>

            <div className="col-span-6 sm:col-span-3 lg:col-span-2 space-y-1.5">
              <label htmlFor="tooltaskpage-field-3" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">开始时间</label>
              <input id="tooltaskpage-field-3" type="datetime-local" step="1" className={`${ic} dark:text-white`} value={startTime} onChange={e => setStartTime(e.target.value)} />
            </div>

            <div className="col-span-6 sm:col-span-3 lg:col-span-2 space-y-1.5">
              <label htmlFor="tooltaskpage-field-4" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">结束时间</label>
              <input id="tooltaskpage-field-4" type="datetime-local" step="1" className={`${ic} dark:text-white`} value={endTime} onChange={e => setEndTime(e.target.value)} />
            </div>

            <div className="col-span-6 sm:col-span-3 lg:col-span-2 space-y-1.5">
              <label htmlFor="tooltaskpage-field-5" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">最大页数</label>
              <input id="tooltaskpage-field-5" type="number" className={ic} placeholder="0=不限" value={maxPages} onChange={e => setMaxPages(e.target.value)} />
            </div>

            <div className="col-span-6 sm:col-span-3 lg:col-span-2 flex gap-2 pb-0.5">
              <button
                onClick={handleCreate}
                disabled={creating}
                className="flex-1 text-white bg-primary hover:bg-blue-600 font-medium rounded-lg text-sm px-4 py-2.5 transition-all shadow-lg shadow-blue-500/30 disabled:opacity-50"
              >
                {creating ? '创建中...' : '创建任务'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Task List */}
      <div className="p-6">
        <div className="max-w-7xl mx-auto">
          <div className="bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm overflow-hidden">
            {tasks.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-64 text-slate-400">
                <span className="material-icons text-5xl mb-2">task_alt</span>
                <p>暂无任务</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead className="text-xs text-slate-700 dark:text-slate-300 uppercase bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                    <tr>
                      <th className="px-4 py-3">任务ID</th>
                      <th className="px-4 py-3">平台</th>
                      <th className="px-4 py-3">设备ID</th>
                      <th className="px-4 py-3">时间范围</th>
                      <th className="px-4 py-3">状态</th>
                      <th className="px-4 py-3">进度</th>
                      <th className="px-4 py-3">消息</th>
                      <th className="px-4 py-3">创建时间</th>
                      <th className="px-4 py-3">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.map(task => (
                      <tr key={task.task_id} className="border-b border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800/30">
                        <td className="px-4 py-3 font-mono text-xs">{task.task_id.substring(0, 8)}</td>
                        <td className="px-4 py-3">{task.platform_name}</td>
                        <td className="px-4 py-3 font-mono text-xs">{task.device_id || '-'}</td>
                        <td className="px-4 py-3 text-xs">
                          {task.start_time}<br />~ {task.end_time}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                            task.status === 'completed' ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' :
                            task.status === 'running' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400' :
                            task.status === 'failed' ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' :
                            'bg-slate-100 text-slate-800 dark:bg-slate-700 dark:text-slate-300'
                          }`}>
                            {task.status === 'completed' ? '已完成' :
                             task.status === 'running' ? '运行中' :
                             task.status === 'failed' ? '失败' : '等待中'}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          {task.total > 0 ? `${task.downloaded}/${task.total}` : '-'}
                        </td>
                        <td className="px-4 py-3 text-xs max-w-xs truncate" title={task.message}>{task.message}</td>
                        <td className="px-4 py-3 text-xs">{task.created_at?.substring(0, 19).replace('T', ' ')}</td>
                        <td className="px-4 py-3">
                          {task.status === 'completed' && (
                            <button
                              onClick={() => handleDownload(task.task_id)}
                              className="inline-flex items-center gap-1 text-primary hover:text-blue-600 font-medium text-xs"
                            >
                              <span className="material-icons text-[16px]">download</span>
                              下载
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ToolTaskPage;

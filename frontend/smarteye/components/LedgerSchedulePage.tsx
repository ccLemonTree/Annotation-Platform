import { Modal } from 'antd';
import { apiFetch, download } from '../network';
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { API_BASE_URL } from '../constants';
import type { PlatformOption } from '../types';

const MAX_PLATFORMS = 10;
const INTERVAL_OPTIONS = [
  { label: '30 分钟', value: 30 }, { label: '1 小时', value: 60 },
  { label: '2 小时', value: 120 }, { label: '3 小时', value: 180 },
  { label: '4 小时', value: 240 }, { label: '6 小时', value: 360 },
];
const DURATION_OPTIONS = [
  { label: '不限', value: 0 },
  ...[4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, 68, 72].map(h => ({ label: `${h} 小时`, value: h })),
];

interface SchedulerInfo {
  sched_id: string; name: string; enabled: boolean; last_task_id?: string;
  interval_minutes: number; task_duration_hours: number;
  started_at: string | null; last_run: string | null; next_run: string | null;
  platform_ids: string[] | null; alarm_type_filter: string[] | null;
  small_model_mode: string; is_running: boolean;
}

interface TaskInfo {
  task_id: string; status: string; progress: number; message: string;
  created_at: number; zip_filename: string; zip_path: string;
  platform_results: any[];
  stage?: string; download_total?: number; download_completed?: number;
  download_pending?: number; download_success?: number; download_failed?: number;
}

interface WatchJob {
  job_id: string; name: string; enabled: boolean;
  source_types: string; platform_ids: string[]; alarm_type_filter: string[];
  small_model_mode: string; poll_interval_seconds: number; time_offset_seconds: number;
  status: string; last_poll: string | null; last_trigger_count: number;
  total_downloaded: number; recent_logs: { time: string; msg: string }[];
  is_running: boolean;
}

const ic = "w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 text-sm rounded-lg focus:ring-primary focus:border-primary block p-2.5";

const fmtTime = (iso: string | null) => {
  if (!iso) return '-';
  try { return new Date(iso).toLocaleString('zh-CN'); } catch { return iso; }
};

const fmtTs = (ts: number | null) => {
  if (!ts) return '-';
  try { return new Date(ts * 1000).toLocaleString('zh-CN'); } catch { return '-'; }
};

// ── TaskForm Modal ─────────────────────────────────────────────────────
const TaskForm: React.FC<{
  platforms: PlatformOption[];
  onSubmit: (config: any, isScheduled: boolean) => void;
  onClose: () => void;
}> = ({ platforms, onSubmit, onClose }) => {
  const [name, setName] = useState('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<PlatformOption[]>([]);
  const [selectedAlarmTypes, setSelectedAlarmTypes] = useState<string[]>([]);
  const [smallModelMode, setSmallModelMode] = useState('crop');
  const [isScheduled, setIsScheduled] = useState(false);
  const [intervalMinutes, setIntervalMinutes] = useState(120);
  const [taskDuration, setTaskDuration] = useState(0);
  const [platDropdownOpen, setPlatDropdownOpen] = useState(false);
  const [platSearch, setPlatSearch] = useState('');
  const [alarmOpen, setAlarmOpen] = useState(false);
  const platRef = useRef<HTMLDivElement>(null);
  const alarmRef = useRef<HTMLDivElement>(null);

  const filtered = platSearch.trim()
    ? platforms.filter(p => p.label.toLowerCase().includes(platSearch.toLowerCase()))
    : platforms;

  const availableAlarmTypes = (() => {
    const set = new Set<string>();
    for (const sp of selectedPlatforms) {
      if (sp.alarmTypes) Object.keys(sp.alarmTypes).forEach(t => set.add(t));
    }
    return Array.from(set).sort();
  })();

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (platRef.current && !platRef.current.contains(e.target as Node)) setPlatDropdownOpen(false);
      if (alarmRef.current && !alarmRef.current.contains(e.target as Node)) setAlarmOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const togglePlat = (p: PlatformOption) => {
    setSelectedPlatforms(prev => {
      const exists = prev.find(s => s.value === p.value);
      if (exists) return prev.filter(s => s.value !== p.value);
      if (prev.length >= MAX_PLATFORMS) { alert(`最多 ${MAX_PLATFORMS} 个`); return prev; }
      return [...prev, p];
    });
  };

  const handleSubmit = () => {
    if (!name.trim()) { alert('请输入任务名称'); return; }
    if (selectedPlatforms.length === 0) { alert('请选择平台'); return; }
    const config = {
      name: name.trim(),
      platform_ids: selectedPlatforms.map(p => p.value),
      alarm_type_filter: selectedAlarmTypes.length > 0 ? selectedAlarmTypes : null,
      small_model_mode: smallModelMode,
      ...(isScheduled ? { interval_minutes: intervalMinutes, task_duration_hours: taskDuration, enabled: true } : {}),
    };
    onSubmit(config, isScheduled);
  };

  return (
    <Modal open title="新建下载任务" onCancel={onClose} footer={null} width={680} className="smarteye-legacy-form">
        <div className="space-y-4">

          {/* 名称 */}
          <div>
            <label htmlFor="ledgerschedulepage-field-1" className="block text-xs font-semibold text-slate-500 uppercase mb-1">名称 <span className="text-red-500">*</span></label>
            <input id="ledgerschedulepage-field-1" className={ic} placeholder="如：丽水移动-吸烟检测" value={name} onChange={e => setName(e.target.value)} />
          </div>

          {/* 平台 */}
          <div className="relative" ref={platRef}>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">平台 <span className="text-red-500">*</span></label>
            <button type="button" onClick={() => { setPlatDropdownOpen(!platDropdownOpen); setPlatSearch(''); }}
              className={`${ic} text-left flex items-center justify-between`}>
              <span className={selectedPlatforms.length === 0 ? 'text-slate-400' : ''}>
                {selectedPlatforms.length === 0 ? '选择平台' : selectedPlatforms.map(p => p.label).join('、')}
              </span>
              <span className="material-icons text-[18px] text-slate-400">expand_more</span>
            </button>
            {platDropdownOpen && (
              <div className="absolute left-0 mt-1 w-full bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-700 rounded-xl shadow-2xl overflow-hidden z-50">
                <div className="p-2 border-b">
                  <input type="text" placeholder="搜索..." value={platSearch} onChange={e => setPlatSearch(e.target.value)} autoFocus
                    className="w-full bg-slate-50 dark:bg-background-dark border border-slate-200 dark:border-slate-600 text-sm rounded-lg px-3 py-1.5" />
                </div>
                <div className="overflow-y-auto max-h-48">
                  {filtered.map(p => {
                    const sel = !!selectedPlatforms.find(s => s.value === p.value);
                    return (
                      <label key={p.value} className={`flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer ${sel ? 'bg-primary/5' : 'hover:bg-slate-50 dark:hover:bg-surface-hover'}`}>
                        <input type="checkbox" checked={sel} onChange={() => togglePlat(p)} className="w-4 h-4 text-primary rounded" />
                        <span>{p.label}</span>
                      </label>
                    );
                  })}
                </div>
                <div className="p-2 border-t border-slate-100 dark:border-slate-700 bg-slate-50 dark:bg-background-dark">
                  <button onClick={() => setPlatDropdownOpen(false)}
                    className="w-full text-center text-sm text-primary hover:bg-primary/5 rounded py-1 transition-colors">
                    完成（已选 {selectedPlatforms.length} 个）
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 告警类型 */}
          <div className="relative" ref={alarmRef}>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">告警类型（可选，不选则下载全部）</label>
            <button type="button" onClick={() => setAlarmOpen(!alarmOpen)} disabled={selectedPlatforms.length === 0}
              className={`${ic} text-left flex items-center justify-between disabled:opacity-40`}>
              <span className={selectedAlarmTypes.length === 0 ? 'text-slate-400' : ''}>
                {selectedAlarmTypes.length === 0 ? '全部告警类型' : selectedAlarmTypes.join('、')}
              </span>
              <span className="material-icons text-[18px] text-slate-400">expand_more</span>
            </button>
            {alarmOpen && (
              <div className="absolute left-0 mt-1 w-full bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-700 rounded-xl shadow-2xl overflow-hidden z-50">
                <div className="overflow-y-auto max-h-48">
                  {availableAlarmTypes.map(t => (
                    <label key={t} className={`flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer ${selectedAlarmTypes.includes(t) ? 'bg-primary/5' : 'hover:bg-slate-50 dark:hover:bg-surface-hover'}`}>
                      <input type="checkbox" checked={selectedAlarmTypes.includes(t)}
                        onChange={() => setSelectedAlarmTypes(prev => prev.includes(t) ? prev.filter(a => a !== t) : [...prev, t])}
                        className="w-4 h-4 text-primary rounded" />
                      <span>{t}</span>
                    </label>
                  ))}
                </div>
                <div className="p-2 border-t border-slate-100 dark:border-slate-700 bg-slate-50 dark:bg-background-dark">
                  <button onClick={() => setAlarmOpen(false)}
                    className="w-full text-center text-sm text-primary hover:bg-primary/5 rounded py-1 transition-colors">
                    完成（已选 {selectedAlarmTypes.length} 个）
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 小模型下载模式 */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-2">小模型下载模式</label>
            <div className="flex gap-4">
              {[{ value: 'crop', label: '仅切图' }, { value: 'full', label: '仅全图' }, { value: 'both', label: '全图+切图' }].map(o => (
                <label key={o.value} className="flex items-center gap-1.5 cursor-pointer">
                  <input type="radio" name="smMode" value={o.value} checked={smallModelMode === o.value}
                    onChange={() => setSmallModelMode(o.value)} className="w-4 h-4 text-primary" />
                  <span className="text-sm text-slate-700 dark:text-slate-200">{o.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* 定时执行 toggle */}
          <div className="border-t border-slate-100 dark:border-slate-700 pt-4">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div className={`relative w-10 h-5 rounded-full transition-colors ${isScheduled ? 'bg-primary' : 'bg-slate-300 dark:bg-slate-600'}`}
                onClick={() => setIsScheduled(!isScheduled)}>
                <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${isScheduled ? 'translate-x-5' : 'translate-x-0.5'}`} />
              </div>
              <span className="text-sm font-medium text-slate-700 dark:text-slate-200">定时执行（循环下载）</span>
            </label>

            {isScheduled && (
              <div className="grid grid-cols-2 gap-4 mt-3">
                <div>
                  <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">执行间隔（预警查询范围）</label>
                  <select className={ic} value={intervalMinutes} onChange={e => setIntervalMinutes(Number(e.target.value))}>
                    {INTERVAL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </div>
                <div>
                  <label htmlFor="ledgerschedulepage-field-2" className="block text-xs font-semibold text-slate-500 uppercase mb-1">总时长</label>
                  <select id="ledgerschedulepage-field-2" className={ic} value={taskDuration} onChange={e => setTaskDuration(Number(e.target.value))}>
                    {DURATION_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </div>
              </div>
            )}
          </div>

          {/* 操作按钮 */}
          <div className="flex gap-2 pt-2">
            <button onClick={onClose} className="flex-1 border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 rounded-lg py-2 text-sm">取消</button>
            <button onClick={handleSubmit} className="flex-1 bg-primary hover:bg-blue-600 text-white rounded-lg py-2 text-sm shadow-lg shadow-blue-500/30">
              {isScheduled ? '创建并启动' : '开始下载'}
            </button>
          </div>
        </div>
    </Modal>
  );
};


// ── TaskRow ────────────────────────────────────────────────────────────
const TaskRow: React.FC<{
  task: TaskInfo;
  scheduler: SchedulerInfo | null;
  platforms: PlatformOption[];
  onStop: (taskId: string) => void;
  onStopScheduler: (schedId: string) => void;
}> = ({ task, scheduler, platforms, onStop, onStopScheduler }) => {
  const isRunning = task.status === 'running' || task.status === 'pending';
  const isCancelling = task.status === 'cancelling';

  const displayName = (() => {
    if (scheduler) return scheduler.name;
    const m = task.message.match(/^\[(.+?)\]/);
    return m ? m[1] : task.task_id.slice(0, 12);
  })();

  const summary = (() => {
    const platIds = scheduler?.platform_ids ?? null;
    const platNames = platIds
      ? platIds.map(id => platforms.find(p => p.value === id)?.label ?? id)
      : [];
    const alarmFilter = scheduler?.alarm_type_filter ?? null;
    const parts: string[] = [];
    if (platNames.length > 0) parts.push(platNames.slice(0, 2).join('、') + (platNames.length > 2 ? `等${platNames.length}个` : ''));
    if (alarmFilter && alarmFilter.length > 0) parts.push(alarmFilter.join('、'));
    return parts.join(' · ') || null;
  })();

  const statusConfig = {
    completed: { dot: 'bg-emerald-500', badge: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400', label: '已完成' },
    failed: { dot: 'bg-red-500', badge: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400', label: '失败' },
    running: { dot: 'bg-blue-500', badge: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400', label: '执行中' },
    pending: { dot: 'bg-amber-400', badge: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400', label: '等待中' },
    cancelling: { dot: 'bg-slate-400', badge: 'bg-slate-100 text-slate-500', label: '取消中' },
  }[task.status] ?? { dot: 'bg-slate-300', badge: 'bg-slate-100 text-slate-500', label: task.status };

  return (
    <div className="p-4 bg-white dark:bg-surface-dark border border-slate-100 dark:border-slate-700 rounded-xl shadow-sm">
      <div className="flex items-start gap-3">
        {/* 状态点 */}
        <div className={`mt-1 w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusConfig.dot}`} />

        {/* 主体 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="font-medium text-slate-800 dark:text-white text-sm">{displayName}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${statusConfig.badge}`}>{statusConfig.label}</span>
            {scheduler && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 font-medium">定时</span>
            )}
          </div>
          {summary && <p className="text-xs text-slate-400 mb-1">{summary}</p>}

          {(isRunning || isCancelling) && (
            <div className="mt-2">
              <div className="w-full bg-slate-100 dark:bg-slate-700 h-1.5 rounded-full overflow-hidden">
                <div className="h-full bg-blue-500 rounded-full transition-all duration-500" style={{ width: `${task.progress}%` }} />
              </div>
              <p className="text-xs text-slate-500 mt-1">{task.message}</p>
              {(task.download_total ?? 0) > 0 && (
                <p className="text-xs text-slate-400 mt-0.5">
                  下载任务 {task.download_completed ?? 0}/{task.download_total} · 剩余 {task.download_pending ?? 0} ·
                  成功 {task.download_success ?? 0} · 失败 {task.download_failed ?? 0}
                </p>
              )}
            </div>
          )}
          {!isRunning && !isCancelling && (
            <p className="text-xs text-slate-400 mt-0.5">{task.message}</p>
          )}

          {scheduler?.enabled && scheduler.next_run && (
            <p className="text-xs text-blue-500 mt-1">下次执行：{fmtTime(scheduler.next_run)}</p>
          )}
        </div>

        {/* 右侧操作区 */}
        <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
          <span className="text-xs text-slate-400">{fmtTs(task.created_at)}</span>
          <div className="flex gap-1.5">
            {task.status === 'completed' && task.zip_path && (
              <a href="#" onClick={event => { event.preventDefault(); void download(`${API_BASE_URL}/ledger-image/download/${task.task_id}`); }}
                className="text-xs bg-emerald-50 hover:bg-emerald-100 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400 px-2 py-1 rounded-lg transition-colors">
                下载 ZIP
              </a>
            )}
            {(isRunning) && (
              <button onClick={() => onStop(task.task_id)}
                className="text-xs bg-red-50 hover:bg-red-100 text-red-600 dark:bg-red-900/20 dark:text-red-400 px-2 py-1 rounded-lg transition-colors">
                停止
              </button>
            )}
            {scheduler?.enabled && !isRunning && (
              <button onClick={() => onStopScheduler(scheduler.sched_id)}
                className="text-xs bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-600 dark:text-slate-300 px-2 py-1 rounded-lg transition-colors">
                停止调度
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};


// ── WatchJobForm Modal ─────────────────────────────────────────────────
const POLL_OPTIONS = [
  { label: '30 秒', value: 30 }, { label: '1 分钟', value: 60 },
  { label: '5 分钟', value: 300 }, { label: '10 分钟', value: 600 },
  { label: '30 分钟', value: 1800 },
];

const WatchJobForm: React.FC<{
  platforms: PlatformOption[];
  onSubmit: (config: any) => void;
  onClose: () => void;
}> = ({ platforms, onSubmit, onClose }) => {
  const [name, setName] = useState('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<PlatformOption[]>([]);
  const [selectedAlarmTypes, setSelectedAlarmTypes] = useState<string[]>([]);
  const [smallModelMode, setSmallModelMode] = useState('crop');
  const [pollInterval, setPollInterval] = useState(60);
  const [platDropdownOpen, setPlatDropdownOpen] = useState(false);
  const [platSearch, setPlatSearch] = useState('');
  const [alarmOpen, setAlarmOpen] = useState(false);
  const platRef = useRef<HTMLDivElement>(null);
  const alarmRef = useRef<HTMLDivElement>(null);

  const filtered = platSearch.trim()
    ? platforms.filter(p => p.label.toLowerCase().includes(platSearch.toLowerCase()))
    : platforms;

  const availableAlarmTypes = (() => {
    const set = new Set<string>();
    for (const sp of selectedPlatforms)
      if (sp.alarmTypes) Object.keys(sp.alarmTypes).forEach(t => set.add(t));
    return Array.from(set).sort();
  })();

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (platRef.current && !platRef.current.contains(e.target as Node)) setPlatDropdownOpen(false);
      if (alarmRef.current && !alarmRef.current.contains(e.target as Node)) setAlarmOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const togglePlat = (p: PlatformOption) => {
    setSelectedPlatforms(prev => {
      const exists = prev.find(s => s.value === p.value);
      if (exists) return prev.filter(s => s.value !== p.value);
      if (prev.length >= MAX_PLATFORMS) { alert(`最多 ${MAX_PLATFORMS} 个`); return prev; }
      return [...prev, p];
    });
  };

  const handleSubmit = () => {
    if (!name.trim()) { alert('请输入任务名称'); return; }
    if (selectedPlatforms.length === 0) { alert('请选择平台'); return; }
    onSubmit({
      name: name.trim(),
      platform_ids: selectedPlatforms.map(p => p.value),
      alarm_type_filter: selectedAlarmTypes.length > 0 ? selectedAlarmTypes : [],
      small_model_mode: smallModelMode,
      source_types: 'alarm',
      poll_interval_seconds: pollInterval,
      time_offset_seconds: 15,
      enabled: true,
    });
  };

  return (
    <Modal open title="新建监听任务" onCancel={onClose} footer={null} width={680} className="smarteye-legacy-form">
        <div className="space-y-4">

          <div>
            <label htmlFor="ledgerschedulepage-field-3" className="block text-xs font-semibold text-slate-500 uppercase mb-1">名称 <span className="text-red-500">*</span></label>
            <input id="ledgerschedulepage-field-3" className={ic} placeholder="如：丽水移动-吸烟监听" value={name} onChange={e => setName(e.target.value)} />
          </div>

          <div className="relative" ref={platRef}>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">平台 <span className="text-red-500">*</span></label>
            <button type="button" onClick={() => { setPlatDropdownOpen(!platDropdownOpen); setPlatSearch(''); }}
              className={`${ic} text-left flex items-center justify-between`}>
              <span className={selectedPlatforms.length === 0 ? 'text-slate-400' : ''}>
                {selectedPlatforms.length === 0 ? '选择平台' : selectedPlatforms.map(p => p.label).join('、')}
              </span>
              <span className="material-icons text-[18px] text-slate-400">expand_more</span>
            </button>
            {platDropdownOpen && (
              <div className="absolute left-0 mt-1 w-full bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-700 rounded-xl shadow-2xl overflow-hidden z-50">
                <div className="p-2 border-b">
                  <input type="text" placeholder="搜索..." value={platSearch} onChange={e => setPlatSearch(e.target.value)} autoFocus
                    className="w-full text-sm px-2 py-1 bg-transparent outline-none text-slate-800 dark:text-slate-200" />
                </div>
                <div className="overflow-y-auto max-h-48">
                  {filtered.map(p => {
                    const sel = !!selectedPlatforms.find(s => s.value === p.value);
                    return (
                      <label key={p.value} className={`flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer ${sel ? 'bg-primary/5' : 'hover:bg-slate-50 dark:hover:bg-surface-hover'}`}>
                        <input type="checkbox" checked={sel} onChange={() => togglePlat(p)} className="w-4 h-4 text-primary rounded" />
                        <span>{p.label}</span>
                      </label>
                    );
                  })}
                </div>
                <div className="p-2 border-t flex justify-end">
                  <button onClick={() => setPlatDropdownOpen(false)} className="text-xs text-primary font-medium px-3 py-1 rounded-lg hover:bg-primary/10">完成</button>
                </div>
              </div>
            )}
          </div>

          {availableAlarmTypes.length > 0 && (
            <div className="relative" ref={alarmRef}>
              <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">告警类型（不选则监听全部）</label>
              <button type="button" onClick={() => setAlarmOpen(!alarmOpen)}
                className={`${ic} text-left flex items-center justify-between`}>
                <span className={selectedAlarmTypes.length === 0 ? 'text-slate-400' : ''}>
                  {selectedAlarmTypes.length === 0 ? '全部类型' : selectedAlarmTypes.join('、')}
                </span>
                <span className="material-icons text-[18px] text-slate-400">expand_more</span>
              </button>
              {alarmOpen && (
                <div className="absolute left-0 mt-1 w-full bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-700 rounded-xl shadow-2xl z-50 overflow-hidden">
                  <div className="overflow-y-auto max-h-48">
                    {availableAlarmTypes.map(t => (
                      <label key={t} className={`flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer ${selectedAlarmTypes.includes(t) ? 'bg-primary/5' : 'hover:bg-slate-50 dark:hover:bg-surface-hover'}`}>
                        <input type="checkbox" checked={selectedAlarmTypes.includes(t)} onChange={() => setSelectedAlarmTypes(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t])} className="w-4 h-4 text-primary rounded" />
                        <span>{t}</span>
                      </label>
                    ))}
                  </div>
                  <div className="p-2 border-t flex justify-end">
                    <button onClick={() => setAlarmOpen(false)} className="text-xs text-primary font-medium px-3 py-1 rounded-lg hover:bg-primary/10">完成</button>
                  </div>
                </div>
              )}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">说明</label>
            <p className="text-xs text-slate-500 bg-slate-50 dark:bg-background-dark rounded-lg px-3 py-2">
              每轮按轮询间隔查询预警数据库，再按设备和预警时间前后各 15 秒查询台账，下载图片后丢弃查询结果。
            </p>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">小模型下载模式</label>
            <div className="flex gap-4">
              {[['crop', '仅切图'], ['full', '仅全图'], ['both', '全图+切图']].map(([v, l]) => (
                <label key={v} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="radio" name="watch_mode" value={v} checked={smallModelMode === v} onChange={() => setSmallModelMode(v)} className="w-4 h-4 text-primary" />
                  {l}
                </label>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">轮询间隔</label>
              <select className={ic} value={pollInterval} onChange={e => setPollInterval(+e.target.value)}>
                {POLL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">台账查询偏移（前后各）</label>
              <p className={ic}>固定 15 秒</p>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t">
            <button onClick={onClose} className="text-sm px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300">取消</button>
            <button onClick={handleSubmit} className="text-sm px-4 py-2 rounded-lg bg-primary hover:bg-blue-600 text-white font-medium">创建并启动</button>
          </div>
        </div>
    </Modal>
  );
};


// ── WatchJobRow ─────────────────────────────────────────────────────────
const WatchJobRow: React.FC<{
  job: WatchJob;
  platforms: PlatformOption[];
  onToggle: (job: WatchJob) => void;
  onDelete: (jobId: string) => void;
}> = ({ job, platforms, onToggle, onDelete }) => {
  const [showLogs, setShowLogs] = useState(false);
  const platNames = platforms.filter(p => job.platform_ids.includes(p.value)).map(p => p.label);

  const statusInfo = job.is_running
    ? { dot: 'bg-green-400', badge: 'bg-green-100 text-green-700', label: job.status === 'polling' ? '轮询中' : '运行中' }
    : { dot: 'bg-slate-300', badge: 'bg-slate-100 text-slate-500', label: '已停止' };

  return (
    <div className="p-4 bg-white dark:bg-surface-dark border border-slate-100 dark:border-slate-700 rounded-xl shadow-sm">
      <div className="flex items-start gap-3">
        <div className={`w-2.5 h-2.5 rounded-full mt-1.5 flex-shrink-0 ${statusInfo.dot}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-slate-800 dark:text-white text-sm">{job.name}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${statusInfo.badge}`}>{statusInfo.label}</span>
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-cyan-100 text-cyan-700 font-medium">监听</span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            {platNames.length > 0 ? platNames.slice(0, 3).join('、') + (platNames.length > 3 ? ` 等${platNames.length}个` : '') : '未绑定平台'}
            {job.alarm_type_filter.length > 0 && ` · ${job.alarm_type_filter.slice(0, 2).join('、')}${job.alarm_type_filter.length > 2 ? '...' : ''}`}
            {` · 轮询间隔 ${job.poll_interval_seconds}s · 偏移 ±15s`}
          </p>
          {job.last_poll && (
            <p className="text-xs text-slate-400 mt-0.5">
              上次轮询：{job.last_poll}
              {job.last_trigger_count > 0 && <span className="ml-2 text-green-600">本轮下载 {job.last_trigger_count} 张</span>}
              {job.total_downloaded > 0 && <span className="ml-2 text-slate-500">累计 {job.total_downloaded} 张</span>}
            </p>
          )}

          {showLogs && job.recent_logs.length > 0 && (
            <div className="mt-2 bg-slate-50 dark:bg-background-dark rounded-lg p-2 max-h-36 overflow-y-auto">
              {job.recent_logs.map((log, i) => (
                <p key={i} className="text-xs text-slate-500 font-mono">[{log.time}] {log.msg}</p>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {job.recent_logs.length > 0 && (
            <button onClick={() => setShowLogs(!showLogs)}
              className="text-xs bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-600 dark:text-slate-300 px-2 py-1 rounded-lg transition-colors">
              日志
            </button>
          )}
          <button onClick={() => onToggle(job)}
            className={`text-xs px-2 py-1 rounded-lg transition-colors ${
              job.is_running
                ? 'bg-amber-100 hover:bg-amber-200 text-amber-700'
                : 'bg-green-100 hover:bg-green-200 text-green-700'
            }`}>
            {job.is_running ? '停止' : '启动'}
          </button>
          <button onClick={() => onDelete(job.job_id)}
            className="text-xs bg-red-50 hover:bg-red-100 text-red-500 px-2 py-1 rounded-lg transition-colors">
            删除
          </button>
        </div>
      </div>
    </div>
  );
};


// ── LedgerSchedulePage ─────────────────────────────────────────────────
const LedgerSchedulePage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'schedule' | 'watch'>('schedule');
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [schedulers, setSchedulers] = useState<SchedulerInfo[]>([]);
  const [allTasks, setAllTasks] = useState<TaskInfo[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [updatingDevices, setUpdatingDevices] = useState(false);
  const [deviceUpdateMsg, setDeviceUpdateMsg] = useState('');
  const [showDeviceModal, setShowDeviceModal] = useState(false);
  const [deviceModalPlatIds, setDeviceModalPlatIds] = useState<string[]>([]);
  const statusPollRef = useRef<number | null>(null);

  // 监听下载 state
  const [watchJobs, setWatchJobs] = useState<WatchJob[]>([]);
  const [showWatchForm, setShowWatchForm] = useState(false);
  const watchPollRef = useRef<number | null>(null);

  useEffect(() => {
    apiFetch(`${API_BASE_URL}/platform-list`)
      .then(r => r.json())
      .then(d => { if (d.code === 0) setPlatforms(d.data); })
      .catch(console.error);
  }, []);

  const fetchStatus = useCallback(() => {
    apiFetch(`${API_BASE_URL}/ledger-image/scheduler/status`)
      .then(r => r.json())
      .then(d => {
        if (d.code === 0) {
          setSchedulers(d.data.schedulers || []);
          setAllTasks(d.data.all_tasks || []);
        }
      })
      .catch(console.error);
  }, []);

  useEffect(() => {
    fetchStatus();
    statusPollRef.current = window.setInterval(fetchStatus, 5000);
    return () => { if (statusPollRef.current) clearInterval(statusPollRef.current); };
  }, [fetchStatus]);

  // 监听下载轮询
  const fetchWatchJobs = useCallback(() => {
    apiFetch(`${API_BASE_URL}/ledger-watch/jobs`)
      .then(r => r.json())
      .then(d => { if (d.code === 0) setWatchJobs(d.data || []); })
      .catch(console.error);
  }, []);

  useEffect(() => {
    fetchWatchJobs();
    watchPollRef.current = window.setInterval(fetchWatchJobs, 5000);
    return () => { if (watchPollRef.current) clearInterval(watchPollRef.current); };
  }, [fetchWatchJobs]);

  const handleCreate = async (config: any, isScheduled: boolean) => {
    try {
      const url = isScheduled
        ? `${API_BASE_URL}/ledger-image/scheduler/configure`
        : `${API_BASE_URL}/ledger-image/start`;
      const res = await apiFetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (data.code === 0) {
        setShowForm(false);
        fetchStatus();
      } else {
        alert(data.message || '创建失败');
      }
    } catch (e: any) {
      alert(e.message || '请求失败');
    }
  };

  const handleStopTask = async (taskId: string) => {
    await apiFetch(`${API_BASE_URL}/ledger-image/stop/${taskId}`, { method: 'POST' });
    fetchStatus();
  };

  const handleStopScheduler = async (schedId: string) => {
    await apiFetch(`${API_BASE_URL}/ledger-image/scheduler/stop`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sched_id: schedId }),
    });
    fetchStatus();
  };

  // 监听下载操作
  const handleCreateWatchJob = async (config: any) => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/ledger-watch/jobs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (data.code === 0) { setShowWatchForm(false); fetchWatchJobs(); }
      else alert(data.message || '创建失败');
    } catch (e: any) { alert(e.message || '请求失败'); }
  };

  const handleToggleWatchJob = async (job: WatchJob) => {
    const url = job.is_running
      ? `${API_BASE_URL}/ledger-watch/jobs/${job.job_id}/stop`
      : `${API_BASE_URL}/ledger-watch/jobs/${job.job_id}/start`;
    await apiFetch(url, { method: 'POST' });
    fetchWatchJobs();
  };

  const handleDeleteWatchJob = async (jobId: string) => {
    if (!confirm('确认删除此监听任务？')) return;
    await apiFetch(`${API_BASE_URL}/ledger-watch/jobs/${jobId}`, { method: 'DELETE' });
    fetchWatchJobs();
  };

  const handleUpdateDevices = async (platIds: string[]) => {
    setShowDeviceModal(false);
    setUpdatingDevices(true);
    setDeviceUpdateMsg('');
    try {
      const body = platIds.length > 0 ? { platform_ids: platIds } : {};
      const r = await apiFetch(`${API_BASE_URL}/ledger-image/update-devices`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      setDeviceUpdateMsg(d.code === 0 ? d.message : `失败：${d.message}`);
    } catch (e) {
      setDeviceUpdateMsg('请求失败');
    } finally {
      setUpdatingDevices(false);
      setTimeout(() => setDeviceUpdateMsg(''), 6000);
    }
  };

  // 将任务与调度器关联：调度器最新的 last_task_id 关联上去
  const getSchedulerForTask = (task: TaskInfo): SchedulerInfo | null => {
    return schedulers.find(s =>
      s.last_task_id === task.task_id ||
      task.task_id.startsWith(s.sched_id + '_')
    ) ?? null;
  };

  const sortedTasks = [...allTasks].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));

  // 有运行中调度器但没有对应任务时，在列表里补一个"调度器"占位卡
  const activeSchedulersWithoutTask = schedulers.filter(s =>
    s.enabled && !allTasks.some(t => t.task_id.startsWith(s.sched_id + '_') || s.last_task_id === t.task_id)
  );

  return (
    <div className="flex-1 overflow-auto bg-background-light dark:bg-background-dark transition-colors duration-300">
      {/* Header */}
      <div className="bg-white dark:bg-surface-dark border-b border-slate-200 dark:border-slate-800 px-6 pt-5 pb-0 shadow-sm">
        <div className="max-w-4xl mx-auto">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">台账图片下载</h1>
              <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
                按执行间隔查询预警数据库，再按设备和预警时间 ±15 秒查询台账下载
              </p>
            </div>
            {activeTab === 'schedule' ? (
              <div className="flex items-center gap-2">
                {deviceUpdateMsg && (
                  <span className="text-xs text-slate-500">{deviceUpdateMsg}</span>
                )}
                <button onClick={() => { setDeviceModalPlatIds([]); setShowDeviceModal(true); }} disabled={updatingDevices}
                  className="inline-flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 text-sm font-medium px-3 py-2.5 rounded-lg transition-all disabled:opacity-50">
                  <span className="material-icons text-[18px]">{updatingDevices ? 'sync' : 'devices'}</span>
                  {updatingDevices ? '更新中...' : '更新设备'}
                </button>
                <button onClick={() => setShowForm(true)}
                  className="inline-flex items-center gap-1.5 bg-primary hover:bg-blue-600 text-white text-sm font-medium px-4 py-2.5 rounded-lg shadow-md transition-all">
                  <span className="material-icons text-[18px]">add</span> 新建下载任务
                </button>
              </div>
            ) : (
              <button onClick={() => setShowWatchForm(true)}
                className="inline-flex items-center gap-1.5 bg-primary hover:bg-blue-600 text-white text-sm font-medium px-4 py-2.5 rounded-lg shadow-md transition-all">
                <span className="material-icons text-[18px]">add</span> 新建监听任务
              </button>
            )}
          </div>
          {/* Tabs */}
          <div className="flex gap-1">
            {(['schedule', 'watch'] as const).map(tab => (
              <button key={tab} onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors ${
                  activeTab === tab
                    ? 'border-primary text-primary bg-primary/5'
                    : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                }`}>
                {tab === 'schedule' ? '定时下载' : '监听下载'}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="p-6">
        <div className="max-w-4xl mx-auto space-y-3">

          {activeTab === 'schedule' ? (
            <>
              {activeSchedulersWithoutTask.map(s => (
                <div key={s.sched_id} className="p-4 bg-white dark:bg-surface-dark border border-blue-100 dark:border-blue-900/40 rounded-xl shadow-sm">
                  <div className="flex items-center gap-3">
                    <div className="w-2.5 h-2.5 rounded-full bg-blue-400 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-slate-800 dark:text-white text-sm">{s.name}</span>
                        <span className="text-xs px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 font-medium">定时</span>
                        <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">等待首次执行</span>
                      </div>
                      {s.next_run && <p className="text-xs text-blue-500 mt-0.5">下次执行：{fmtTime(s.next_run)}</p>}
                    </div>
                    <button onClick={() => handleStopScheduler(s.sched_id)}
                      className="text-xs bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-600 dark:text-slate-300 px-2 py-1 rounded-lg transition-colors flex-shrink-0">
                      停止调度
                    </button>
                  </div>
                </div>
              ))}

              {sortedTasks.length === 0 && activeSchedulersWithoutTask.length === 0 ? (
                <div className="bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm p-16 text-center text-slate-400">
                  <span className="material-icons text-5xl mb-3 block">download</span>
                  <p className="text-lg font-medium mb-1">暂无下载任务</p>
                  <p className="text-sm">点击"新建下载任务"开始下载台账图片</p>
                </div>
              ) : (
                sortedTasks.map(task => (
                  <TaskRow key={task.task_id} task={task}
                    scheduler={getSchedulerForTask(task)} platforms={platforms}
                    onStop={handleStopTask} onStopScheduler={handleStopScheduler} />
                ))
              )}
            </>
          ) : (
            <>
              {watchJobs.length === 0 ? (
                <div className="bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm p-16 text-center text-slate-400">
                  <span className="material-icons text-5xl mb-3 block">sensors</span>
                  <p className="text-lg font-medium mb-1">暂无监听任务</p>
                  <p className="text-sm">点击"新建监听任务"，查询到预警时自动下载对应台账图片</p>
                </div>
              ) : (
                watchJobs.map(job => (
                  <WatchJobRow key={job.job_id} job={job} platforms={platforms}
                    onToggle={handleToggleWatchJob} onDelete={handleDeleteWatchJob} />
                ))
              )}
            </>
          )}
        </div>
      </div>

      {showForm && (
        <TaskForm platforms={platforms} onSubmit={handleCreate} onClose={() => setShowForm(false)} />
      )}
      {showWatchForm && (
        <WatchJobForm platforms={platforms} onSubmit={handleCreateWatchJob} onClose={() => setShowWatchForm(false)} />
      )}
      {showDeviceModal && (
        <Modal open title="更新设备" onCancel={() => setShowDeviceModal(false)} footer={null} width={480} className="smarteye-legacy-form">
            <p className="text-xs text-slate-400 mb-4">选择要更新的平台，不选则更新所有启用平台</p>
            <div className="max-h-60 overflow-y-auto border border-slate-100 dark:border-slate-700 rounded-lg mb-4">
              {platforms.map(p => (
                <label key={p.value}
                  className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer ${deviceModalPlatIds.includes(p.value) ? 'bg-primary/5' : 'hover:bg-slate-50 dark:hover:bg-surface-hover'}`}>
                  <input type="checkbox" className="w-4 h-4 text-primary rounded"
                    checked={deviceModalPlatIds.includes(p.value)}
                    onChange={() => setDeviceModalPlatIds(prev =>
                      prev.includes(p.value) ? prev.filter(id => id !== p.value) : [...prev, p.value]
                    )} />
                  <span className="text-slate-700 dark:text-slate-200">{p.label}</span>
                </label>
              ))}
            </div>
            <div className="flex gap-2">
              <button onClick={() => setShowDeviceModal(false)}
                className="flex-1 border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 rounded-lg py-2 text-sm">
                取消
              </button>
              <button onClick={() => handleUpdateDevices(deviceModalPlatIds)}
                className="flex-1 bg-primary hover:bg-blue-600 text-white rounded-lg py-2 text-sm shadow-lg shadow-blue-500/30">
                {deviceModalPlatIds.length === 0 ? '更新全部平台' : `更新 ${deviceModalPlatIds.length} 个平台`}
              </button>
            </div>
        </Modal>
      )}
    </div>
  );
};

export default LedgerSchedulePage;

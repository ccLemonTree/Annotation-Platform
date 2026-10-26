import { apiFetch } from '../network';
import React, { useEffect, useState, useRef } from 'react';
import type { PlatformOption } from '../types';
import { API_BASE_URL } from '../constants';

interface FilterBarProps {
  onSearch: (filters: any) => void;
}

const MAX_PLATFORMS = 10;

const FilterBar: React.FC<FilterBarProps> = ({ onSearch }) => {
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  const getToday = () => {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    const h = String(now.getHours()).padStart(2, '0');
    const mi = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    return {
      start: `${y}-${m}-${d}T00:00:00`,
      end: `${y}-${m}-${d}T${h}:${mi}:${s}`,
    };
  };
  const defaults = getToday();

  const [selectedPlatforms, setSelectedPlatforms] = useState<PlatformOption[]>([]);
  const [platDropdownOpen, setPlatDropdownOpen] = useState(false);
  const [platSearch, setPlatSearch] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const [dataType, setDataType] = useState('alarm');
  const [targetType, setTargetType] = useState('');
  const [device, setDevice] = useState('');
  const [startTime, setStartTime] = useState(defaults.start);
  const [endTime, setEndTime] = useState(defaults.end);
  const [availableTargets, setAvailableTargets] = useState<string[]>([]);

  const filteredDropdownPlatforms = platSearch.trim()
    ? platforms.filter(p => p.label.toLowerCase().includes(platSearch.toLowerCase()))
    : platforms;

  useEffect(() => { fetchPlatforms(); }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setPlatDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const fetchPlatforms = async () => {
    setLoading(true);
    try {
      const response = await apiFetch(`${API_BASE_URL}/platform-list`);
      const result = await response.json();
      if (result.code === 0) setPlatforms(result.data);
    } catch (error) {
      console.error('Failed to fetch platforms:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSyncPlatforms = async () => {
    if (isSyncing) return;
    setIsSyncing(true);
    try {
      const response = await apiFetch(`${API_BASE_URL}/alarmType`);
      const result = await response.json();
      if (result.code === 0) {
        setSelectedPlatforms([]); setTargetType(''); setAvailableTargets([]);
        await fetchPlatforms();
        alert('平台与预警类型同步成功！');
      } else {
        alert('同步失败: ' + (result.message || '未知错误'));
      }
    } catch (error) {
      alert('同步请求出错，请检查网络或后端服务');
    } finally {
      setIsSyncing(false);
    }
  };

  const updateTargets = (list: PlatformOption[]) => {
    const allTargets = new Set<string>();
    for (const sp of list) {
      if (sp.alarmTypes) Object.keys(sp.alarmTypes).forEach(t => allTargets.add(t));
    }
    setAvailableTargets(Array.from(allTargets));
    setTargetType('');
  };

  const togglePlatform = (p: PlatformOption) => {
    setSelectedPlatforms(prev => {
      const exists = prev.find(s => s.value === p.value);
      if (exists) {
        const next = prev.filter(s => s.value !== p.value);
        updateTargets(next);
        return next;
      }
      if (prev.length >= MAX_PLATFORMS) {
        alert(`最多只能选择 ${MAX_PLATFORMS} 个平台`);
        return prev;
      }
      const next = [...prev, p];
      updateTargets(next);
      return next;
    });
  };

  const selectAllPlatforms = () => {
    const list = filteredDropdownPlatforms.slice(0, MAX_PLATFORMS);
    setSelectedPlatforms(list);
    updateTargets(list);
  };

  const deselectAllPlatforms = () => {
    setSelectedPlatforms([]); setAvailableTargets([]); setTargetType('');
  };

  const handleSearch = () => {
    if (selectedPlatforms.length === 0) { alert('请选择至少一个平台'); return; }
    const platNames = selectedPlatforms.map(p => p.label);
    onSearch({
      platName: platNames.length === 1 ? platNames[0] : platNames,
      platNames,
      sourceType: dataType,
      targetType: targetType === 'all' ? '' : targetType,
      deviceId: device,
      startTime: startTime ? (startTime.length === 16 ? startTime.replace('T', ' ') + ':00' : startTime.replace('T', ' ')) : '',
      endTime: endTime ? (endTime.length === 16 ? endTime.replace('T', ' ') + ':00' : endTime.replace('T', ' ')) : ''
    });
  };

  const selectedLabel = selectedPlatforms.length === 0
    ? (loading ? '加载中...' : '请选择平台（可多选）')
    : selectedPlatforms.length <= 2
      ? selectedPlatforms.map(p => p.label).join('、')
      : `已选 ${selectedPlatforms.length} 个平台`;

  const inputClass = "w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 text-sm rounded-lg focus:ring-primary focus:border-primary block p-2.5";

  return (
    <div className="bg-white dark:bg-surface-dark border-b border-slate-200 dark:border-slate-800 px-6 pt-5 pb-5 shadow-sm relative transition-colors duration-300" style={{ zIndex: 30, overflow: 'visible' }}>
      <div className="max-w-7xl mx-auto space-y-4">
        {/* 标题行 */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">目标检测结果查询</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">查询内网各业务平台的目标检测记录，支持多平台批量下载。</p>
          </div>
          <button
            onClick={handleSyncPlatforms}
            disabled={isSyncing || loading}
            className="inline-flex items-center gap-1.5 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 text-sm font-medium px-4 py-2 rounded-lg shadow transition-all disabled:opacity-50"
          >
            <span className={`material-icons text-[18px] ${isSyncing ? 'animate-spin' : ''}`}>sync</span>
            {isSyncing ? '同步中...' : '同步配置'}
          </button>
        </div>

        {/* 第一行：平台选择 + 数据类型 + 检测目标 */}
        <div className="grid grid-cols-12 gap-4 items-end" style={{ overflow: 'visible' }}>
          {/* 平台多选 */}
          <div className="col-span-12 sm:col-span-6 lg:col-span-5 space-y-1.5" ref={dropdownRef}>
            <label className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">
              平台 <span className="text-red-500">*</span>
              <span className="text-slate-400 font-normal normal-case ml-1">（最多{MAX_PLATFORMS}个）</span>
            </label>
            <div className="relative">
              <button
                ref={triggerRef}
                aria-label="选择查询平台"
                aria-expanded={platDropdownOpen}
                type="button"
                onClick={() => { setPlatDropdownOpen(!platDropdownOpen); setPlatSearch(''); }}
                disabled={loading || isSyncing}
                className={`${inputClass} text-left flex items-center justify-between cursor-pointer`}
              >
                <span className={`truncate ${selectedPlatforms.length === 0 ? 'text-slate-400' : ''}`}>
                  {selectedLabel}
                </span>
                <span className="material-icons text-[18px] text-slate-400 ml-1 flex-shrink-0">
                  {platDropdownOpen ? 'expand_less' : 'expand_more'}
                </span>
              </button>

              {platDropdownOpen && (
                <div className="absolute left-0 mt-1 w-full min-w-[320px] bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-700 rounded-xl shadow-2xl overflow-hidden" style={{ zIndex: 40 }}>
                  {/* 搜索 */}
                  <div className="p-2.5 border-b border-slate-200 dark:border-slate-700">
                    <div className="relative">
                      <span className="material-icons text-[16px] text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2">search</span>
                      <input
                        type="text"
                        aria-label="搜索查询平台"
                        placeholder="输入关键词搜索平台..."
                        value={platSearch}
                        onChange={(e) => setPlatSearch(e.target.value)}
                        autoFocus
                        className="w-full bg-slate-50 dark:bg-background-dark border border-slate-200 dark:border-slate-600 text-sm rounded-lg pl-8 pr-3 py-2 text-slate-800 dark:text-slate-200 focus:ring-primary focus:border-primary outline-none"
                      />
                    </div>
                  </div>
                  {/* 操作栏 */}
                  <div className="flex items-center gap-3 px-3 py-1.5 border-b border-slate-100 dark:border-slate-700 bg-slate-50/50 dark:bg-background-dark">
                    <button onClick={selectAllPlatforms} className="text-xs text-primary hover:underline font-medium">全选</button>
                    <span className="text-slate-300 dark:text-slate-600 text-xs">|</span>
                    <button onClick={deselectAllPlatforms} className="text-xs text-slate-500 hover:underline font-medium">清空</button>
                    <span className="text-xs text-slate-400 ml-auto">
                      {selectedPlatforms.length}/{MAX_PLATFORMS}
                    </span>
                  </div>
                  {/* 列表 */}
                  <div className="overflow-y-auto" style={{ maxHeight: '240px' }}>
                    {filteredDropdownPlatforms.length === 0 ? (
                      <div className="px-3 py-6 text-center text-sm text-slate-400">无匹配平台</div>
                    ) : (
                      filteredDropdownPlatforms.map((p) => {
                        const isSelected = !!selectedPlatforms.find(s => s.value === p.value);
                        const isDisabled = !isSelected && selectedPlatforms.length >= MAX_PLATFORMS;
                        return (
                          <label
                            key={p.value}
                            className={`flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer transition-colors
                              ${isSelected ? 'bg-primary/5 dark:bg-primary/10' : 'hover:bg-slate-50 dark:hover:bg-surface-hover'}
                              ${isDisabled ? 'opacity-40 cursor-not-allowed' : ''}`}
                          >
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => !isDisabled && togglePlatform(p)}
                              disabled={isDisabled}
                              className="w-4 h-4 text-primary bg-slate-100 border-slate-300 rounded focus:ring-primary flex-shrink-0"
                            />
                            <span className="text-slate-800 dark:text-slate-200 truncate">{p.label}</span>
                          </label>
                        );
                      })
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 数据类型 */}
          <div className="col-span-6 sm:col-span-3 lg:col-span-2 space-y-1.5">
            <label className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">数据类型</label>
            <select aria-label="数据类型" className={inputClass} value={dataType} onChange={(e) => setDataType(e.target.value)}>
              <option value="alarm">报警数据</option>
              <option value="capture">抓图数据</option>
              <option value="reject">废片数据</option>
            </select>
          </div>

          {/* 检测目标 */}
          <div className="col-span-6 sm:col-span-3 lg:col-span-2 space-y-1.5">
            <label htmlFor="filterbar-field-1" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">检测目标</label>
            <select id="filterbar-field-1"
              className={inputClass}
              value={targetType}
              onChange={(e) => setTargetType(e.target.value)}
              disabled={selectedPlatforms.length === 0 || availableTargets.length === 0}
            >
              <option value="">全部目标</option>
              {availableTargets.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          {/* 设备 */}
          <div className="col-span-12 sm:col-span-6 lg:col-span-3 space-y-1.5">
            <label htmlFor="filterbar-field-2" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">设备</label>
            <input id="filterbar-field-2" type="text" className={inputClass} placeholder="例如: CAM-001" value={device} onChange={(e) => setDevice(e.target.value)} />
          </div>
        </div>

        {/* 第二行：时间范围 + 按钮 */}
        <div className="grid grid-cols-12 gap-4 items-end">
          <div className="col-span-6 sm:col-span-4 lg:col-span-3 space-y-1.5">
            <label htmlFor="filterbar-field-3" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">开始时间</label>
            <input id="filterbar-field-3" type="datetime-local" step="1" className={`${inputClass} dark:text-white`} value={startTime} onChange={(e) => setStartTime(e.target.value)} />
          </div>
          <div className="col-span-6 sm:col-span-4 lg:col-span-3 space-y-1.5">
            <label htmlFor="filterbar-field-4" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">结束时间</label>
            <input id="filterbar-field-4" type="datetime-local" step="1" className={`${inputClass} dark:text-white`} value={endTime} onChange={(e) => setEndTime(e.target.value)} />
          </div>

          <div className="col-span-12 sm:col-span-4 lg:col-span-3 flex gap-2">
            <button
              onClick={() => {
                setSelectedPlatforms([]); setStartTime(defaults.start); setEndTime(defaults.end);
                setDataType('alarm'); setTargetType(''); setDevice(''); setAvailableTargets([]);
              }}
              className="flex-1 bg-white dark:bg-transparent border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-surface-hover font-medium rounded-lg text-sm px-4 py-2.5 text-center transition-all"
            >
              重置
            </button>
            <button
              onClick={handleSearch}
              className="flex-1 text-white bg-primary hover:bg-blue-600 font-medium rounded-lg text-sm px-4 py-2.5 text-center transition-all shadow-lg shadow-blue-500/30"
            >
              查询
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default FilterBar;

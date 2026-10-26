import { download } from '../network';
import { apiFetch } from '../network';
import React, { useEffect, useState, useCallback } from 'react';
import type { SmartSearchPlatform, SmartSearchPlatformResult } from '../types';
import { API_BASE_URL } from '../constants';

const SmartSearchPage: React.FC = () => {
    // 平台列表
    const [platforms, setPlatforms] = useState<SmartSearchPlatform[]>([]);
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
    const [loadingPlatforms, setLoadingPlatforms] = useState(false);
    const [syncing, setSyncing] = useState(false);

    // 搜索参数
    const [searchText, setSearchText] = useState('');
    const [limit, setLimit] = useState(15);
    const getDefaultTimes = () => {
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
    const defaults = getDefaultTimes();
    const [startTime, setStartTime] = useState(defaults.start);
    const [endTime, setEndTime] = useState(defaults.end);

    // 任务状态
    const [, setTaskId] = useState('');
    const [, setTaskStatus] = useState('');
    const [taskProgress, setTaskProgress] = useState(0);
    const [taskMessage, setTaskMessage] = useState('');
    const [platformResults, setPlatformResults] = useState<SmartSearchPlatformResult[]>([]);
    const [isSearching, setIsSearching] = useState(false);

    // 搜索文本框
    const [searchFilter, setSearchFilter] = useState('');

    // 加载平台列表
    const fetchPlatforms = useCallback(async () => {
        setLoadingPlatforms(true);
        try {
            const rsp = await apiFetch(`${API_BASE_URL}/smart-search/platforms`);
            const result = await rsp.json();
            if (result.code === 0) {
                setPlatforms(result.data || []);
            }
        } catch (e) {
            console.error('获取平台列表失败:', e);
        } finally {
            setLoadingPlatforms(false);
        }
    }, []);

    useEffect(() => {
        fetchPlatforms();
    }, [fetchPlatforms]);

    // 同步远程平台
    const handleSync = async () => {
        if (syncing) return;
        setSyncing(true);
        try {
            const rsp = await apiFetch(`${API_BASE_URL}/smart-search/sync-platforms`, { method: 'POST' });
            const result = await rsp.json();
            if (result.code === 0) {
                await fetchPlatforms();
                setSelectedIds(new Set());
            } else {
                alert('同步失败: ' + (result.message || '未知错误'));
            }
        } catch (e) {
            alert('同步请求出错');
        } finally {
            setSyncing(false);
        }
    };

    // 选择操作（最多 10 个平台）
    const MAX_PLATFORMS = 10;

    const toggleSelect = (platId: string) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(platId)) {
                next.delete(platId);
            } else {
                if (next.size >= MAX_PLATFORMS) {
                    alert(`最多只能选择 ${MAX_PLATFORMS} 个平台`);
                    return prev;
                }
                next.add(platId);
            }
            return next;
        });
    };

    const selectAll = () => {
        const filtered = filteredPlatforms();
        const ids = filtered.slice(0, MAX_PLATFORMS).map(p => p.plat_id);
        setSelectedIds(new Set(ids));
    };
    const deselectAll = () => setSelectedIds(new Set());
    const invertSelection = () => {
        const filtered = filteredPlatforms();
        setSelectedIds(prev => {
            const next = new Set<string>();
            for (const p of filtered) {
                if (!prev.has(p.plat_id)) next.add(p.plat_id);
            }
            return next;
        });
    };

    const filteredPlatforms = () => {
        if (!searchFilter.trim()) return platforms;
        const kw = searchFilter.toLowerCase();
        return platforms.filter(
            p => p.name.toLowerCase().includes(kw) || p.base_url.toLowerCase().includes(kw)
        );
    };

    // 启动搜索
    const handleSearch = async () => {
        if (selectedIds.size === 0) { alert('请选择至少一个平台'); return; }
        if (!searchText.trim()) { alert('请输入搜索内容'); return; }

        setIsSearching(true);
        setTaskMessage('正在启动搜索任务...');
        setPlatformResults([]);
        setTaskProgress(0);

        try {
            const payload = {
                platform_ids: Array.from(selectedIds),
                text: searchText,
                limit,
                startTime: startTime ? (startTime.length === 16 ? startTime.replace('T', ' ') + ':00' : startTime.replace('T', ' ')) : '',
                endTime: endTime ? (endTime.length === 16 ? endTime.replace('T', ' ') + ':00' : endTime.replace('T', ' ')) : '',
            };
            const rsp = await apiFetch(`${API_BASE_URL}/smart-search/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const result = await rsp.json();
            if (result.code === 0) {
                const id = result.data.task_id;
                setTaskId(id);
                setTaskMessage('任务已提交，等待执行...');
                pollProgress(id);
            } else {
                setTaskMessage('启动失败: ' + result.message);
                setIsSearching(false);
            }
        } catch (e) {
            setTaskMessage('请求失败');
            setIsSearching(false);
        }
    };

    // 轮询进度
    const pollProgress = (id: string) => {
        const interval = setInterval(async () => {
            try {
                const rsp = await apiFetch(`${API_BASE_URL}/smart-search/progress/${id}`);
                const data = await rsp.json();
                if (data.code === 0) {
                    const task = data.data;
                    setTaskStatus(task.status);
                    setTaskProgress(task.progress || 0);
                    setTaskMessage(task.message || '');
                    setPlatformResults(task.platform_results || []);

                    if (task.status === 'completed') {
                        clearInterval(interval);
                        setIsSearching(false);
                        // 自动触发下载
                        if (task.zip_filename) {
                            void download(`${API_BASE_URL}/smart-search/download-zip/${id}`);
                            setTaskMessage(task.message + ' ✅ ZIP 正在下载...');
                        }
                    } else if (task.status === 'failed') {
                        clearInterval(interval);
                        setIsSearching(false);
                    }
                }
            } catch (e) {
                console.error('进度查询失败:', e);
            }
        }, 1500);
    };

    const filtered = filteredPlatforms();

    return (
        <div className="flex flex-col h-full overflow-hidden">
            {/* 头部 */}
            <div className="bg-white dark:bg-surface-dark border-b border-slate-200 dark:border-slate-800 p-6 shadow-sm z-10">
                <div className="max-w-7xl mx-auto">
                    <div className="flex items-center justify-between mb-1">
                        <div>
                            <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">
                                智搜数据下载
                            </h1>
                            <p className="text-sm text-slate-500 dark:text-slate-400">
                                多平台数据批量搜索 + 打包下载，支持跨平台一键检索图片数据。
                            </p>
                        </div>
                        <button
                            onClick={handleSync}
                            disabled={syncing}
                            className="inline-flex items-center gap-1.5 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 text-sm font-medium px-4 py-2 rounded-lg shadow transition-all disabled:opacity-50"
                        >
                            <span className={`material-icons text-[18px] ${syncing ? 'animate-spin' : ''}`}>sync</span>
                            {syncing ? '同步中...' : '同步平台'}
                        </button>
                    </div>
                </div>
            </div>

            {/* 主体 */}
            <div className="flex-1 overflow-auto p-6 bg-background-light dark:bg-background-dark">
                <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* 左侧：平台选择 */}
                    <div className="lg:col-span-2 bg-white dark:bg-surface-dark rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm flex flex-col overflow-hidden" style={{ maxHeight: 'calc(100vh - 240px)' }}>
                        {/* 平台操作栏 */}
                        <div className="px-5 py-3 border-b border-slate-200 dark:border-slate-700 flex flex-wrap items-center gap-3">
                            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mr-auto">
                                平台列表
                                <span className="text-xs font-normal text-slate-400 ml-2">
                                    已选 {selectedIds.size}/{filtered.length}
                                </span>
                            </h3>
                            <input
                                type="text"
                                placeholder="搜索平台..."
                                value={searchFilter}
                                onChange={(e) => setSearchFilter(e.target.value)}
                                className="bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-600 text-sm rounded-lg px-3 py-1.5 w-48 focus:ring-primary focus:border-primary text-slate-800 dark:text-slate-200"
                            />
                            <div className="flex gap-1.5">
                                <button onClick={selectAll} className="text-xs bg-primary/10 text-primary px-3 py-1.5 rounded-md hover:bg-primary/20 transition-colors font-medium">全选</button>
                                <button onClick={invertSelection} className="text-xs bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-3 py-1.5 rounded-md hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors font-medium">反选</button>
                                <button onClick={deselectAll} className="text-xs bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-3 py-1.5 rounded-md hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors font-medium">清空</button>
                            </div>
                        </div>

                        {/* 平台列表 */}
                        <div className="flex-1 overflow-y-auto p-2">
                            {loadingPlatforms ? (
                                <div className="flex items-center justify-center py-16 text-slate-400">
                                    <span className="material-icons animate-spin mr-2">autorenew</span>加载中...
                                </div>
                            ) : filtered.length === 0 ? (
                                <div className="flex flex-col items-center justify-center py-16 text-slate-400">
                                    <span className="material-icons text-4xl mb-2">cloud_off</span>
                                    <p>暂无平台数据，请点击「同步平台」获取</p>
                                </div>
                            ) : (
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                    {filtered.map(p => (
                                        <label
                                            key={p.plat_id}
                                            className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-all
                        ${selectedIds.has(p.plat_id)
                                                    ? 'border-primary bg-primary/5 dark:bg-primary/10 shadow-sm'
                                                    : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 hover:bg-slate-50 dark:hover:bg-surface-hover'
                                                }`}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={selectedIds.has(p.plat_id)}
                                                onChange={() => toggleSelect(p.plat_id)}
                                                className="mt-0.5 w-4 h-4 text-primary bg-slate-100 border-slate-300 rounded focus:ring-primary"
                                            />
                                            <div className="flex-1 min-w-0">
                                                <p className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">{p.name}</p>
                                                <p className="text-xs text-slate-400 truncate mt-0.5">{p.base_url}</p>
                                            </div>
                                        </label>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>

                    {/* 右侧：搜索参数 + 操作 */}
                    <div className="space-y-5">
                        {/* 搜索条件 */}
                        <div className="bg-white dark:bg-surface-dark rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 space-y-4">
                            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-1.5">
                                <span className="material-icons text-primary text-[18px]">search</span>搜索条件
                            </h3>

                            <div className="space-y-1.5">
                                <label className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">
                                    搜索内容 <span className="text-red-500">*</span>
                                </label>
                                <input
                                    type="text"
                                    value={searchText}
                                    onChange={(e) => setSearchText(e.target.value)}
                                    placeholder="例如: 绿色奔驰车"
                                    className="w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 text-sm rounded-lg p-2.5 focus:ring-primary focus:border-primary"
                                />
                            </div>

                            <div className="space-y-1.5">
                                <label htmlFor="smartsearchpage-field-1" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">
                                    结果数量
                                </label>
                                <input id="smartsearchpage-field-1"
                                    type="number"
                                    value={limit}
                                    onChange={(e) => setLimit(parseInt(e.target.value) || 15)}
                                    min={1}
                                    max={100}
                                    className="w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 text-sm rounded-lg p-2.5 focus:ring-primary focus:border-primary"
                                />
                            </div>

                            <div className="space-y-1.5">
                                <label htmlFor="smartsearchpage-field-2" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">开始时间</label>
                                <input id="smartsearchpage-field-2"
                                    type="datetime-local"
                                    step="1"
                                    value={startTime}
                                    onChange={(e) => setStartTime(e.target.value)}
                                    className="w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-white text-sm rounded-lg p-2.5 focus:ring-primary focus:border-primary"
                                />
                            </div>

                            <div className="space-y-1.5">
                                <label htmlFor="smartsearchpage-field-3" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">结束时间</label>
                                <input id="smartsearchpage-field-3"
                                    type="datetime-local"
                                    step="1"
                                    value={endTime}
                                    onChange={(e) => setEndTime(e.target.value)}
                                    className="w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-white text-sm rounded-lg p-2.5 focus:ring-primary focus:border-primary"
                                />
                            </div>

                            <button
                                onClick={handleSearch}
                                disabled={isSearching || selectedIds.size === 0}
                                className={`w-full text-white bg-primary hover:bg-blue-600 font-medium rounded-lg text-sm px-5 py-3 text-center inline-flex items-center justify-center gap-2 transition-all shadow-lg shadow-blue-500/30 ${isSearching || selectedIds.size === 0 ? 'opacity-50 cursor-not-allowed' : ''}`}
                            >
                                <span className="material-icons text-[20px]">{isSearching ? 'hourglass_empty' : 'travel_explore'}</span>
                                {isSearching ? '搜索中...' : `开始搜索（${selectedIds.size} 个平台）`}
                            </button>
                        </div>

                        {/* 任务进度 */}
                        {(taskMessage || platformResults.length > 0) && (
                            <div className="bg-white dark:bg-surface-dark rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 space-y-3">
                                <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-1.5">
                                    <span className="material-icons text-primary text-[18px]">task_alt</span>任务进度
                                </h3>

                                {/* 进度条 */}
                                <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2">
                                    <div
                                        className="bg-primary h-2 rounded-full transition-all duration-500 ease-out"
                                        style={{ width: `${taskProgress}%` }}
                                    />
                                </div>
                                <p className="text-xs text-slate-500 dark:text-slate-400">{taskMessage}</p>

                                {/* 各平台结果 */}
                                {platformResults.length > 0 && (
                                    <div className="space-y-1.5 max-h-48 overflow-y-auto">
                                        {platformResults.map((r, idx) => (
                                            <div key={idx} className="flex items-center gap-2 text-xs">
                                                <span className={`material-icons text-[14px] ${r.status === '成功' ? 'text-emerald-500' : r.status === '失败' ? 'text-red-500' : 'text-amber-500'
                                                    }`}>
                                                    {r.status === '成功' ? 'check_circle' : r.status === '失败' ? 'cancel' : 'pending'}
                                                </span>
                                                <span className="font-medium text-slate-700 dark:text-slate-300 truncate flex-1">{r.plat_name}</span>
                                                <span className="text-slate-400 whitespace-nowrap">
                                                    {r.status === '成功' ? `${r.download_count}张` : r.msg}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SmartSearchPage;

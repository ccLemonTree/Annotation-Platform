import { Drawer } from 'antd';
import ImagePreview from './ImagePreview';
import { apiFetch, AuthImage, download } from '../network';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE_URL, getBackendBaseUrl } from '../constants';
import type { AiImageAsset, AiImageHistoryRecord, AiImageTaskRuntime } from '../types';

interface AiImagePageProps {
  recentAssets?: AiImageAsset[];
  seededAssets?: AiImageAsset[];
  onConsumeSeedAssets?: () => void;
}

const inputClass = "w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 text-sm rounded-lg focus:ring-primary focus:border-primary block p-2.5";
const cardClass = "bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm";
const statusMap: Record<string, string> = {
  pending: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  running: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  completed: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  partial_success: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  expired: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
};

const AiImagePage: React.FC<AiImagePageProps> = ({
  recentAssets = [],
  seededAssets = [],
  onConsumeSeedAssets,
}) => {
  const [mode, setMode] = useState<'text_to_image' | 'image_to_image'>('text_to_image');
  const [prompt, setPrompt] = useState('');
  const [outputCount, setOutputCount] = useState(1);
  const [repeatCount, setRepeatCount] = useState(1);
  const [batchSize, setBatchSize] = useState(10);
  const [width, setWidth] = useState('');
  const [height, setHeight] = useState('');
  const [size, setSize] = useState('4194304');
  const [scale, setScale] = useState('50');
  const [minRatio, setMinRatio] = useState('0.3333');
  const [maxRatio, setMaxRatio] = useState('3');
  const [assets, setAssets] = useState<AiImageAsset[]>([]);
  const [urlText, setUrlText] = useState('');
  const [task, setTask] = useState<AiImageTaskRuntime | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<AiImageHistoryRecord[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPrompt, setHistoryPrompt] = useState('');
  const [historyStatus, setHistoryStatus] = useState('');
  const [historyStartDate, setHistoryStartDate] = useState('');
  const [historyEndDate, setHistoryEndDate] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [previewUrl, setPreviewUrl] = useState('');
  const [message, setMessage] = useState('');
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const folderRef = useRef<HTMLInputElement | null>(null);

  const currentPreviewAssets = useMemo(
    () => recentAssets.filter(asset => asset.url || asset.preview_url),
    [recentAssets]
  );

  const publicBaseUrl = getBackendBaseUrl();

  const enrichAssets = (list: AiImageAsset[]) => list.map(asset => ({
    ...asset,
    preview_url: asset.preview_url || asset.url || '',
  }));

  const importExistingAssets = async (list: AiImageAsset[]) => {
    if (!list.length) return;
    setImporting(true);
    setMessage('正在导入素材...');
    try {
      const response = await apiFetch(`${API_BASE_URL}/ai-image/import-existing`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          assets: list.map(asset => ({
            url: asset.url || asset.preview_url,
            name: asset.name,
            source_type: asset.source_type,
          })),
          public_base_url: publicBaseUrl,
        }),
      });
      const result = await response.json();
      if (result.code === 0) {
        setMode('image_to_image');
        setAssets(prev => [...prev, ...enrichAssets(result.data.assets || [])]);
        setMessage(`已导入 ${result.data.assets?.length || 0} 张素材`);
      } else {
        setMessage(result.message || '导入素材失败');
      }
    } catch (error) {
      console.error(error);
      setMessage('导入素材请求失败');
    } finally {
      setImporting(false);
    }
  };

  useEffect(() => {
    if (seededAssets.length > 0) {
      importExistingAssets(seededAssets);
      onConsumeSeedAssets?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seededAssets]);

  useEffect(() => {
    if (!task?.task_id || ['completed', 'partial_success', 'failed', 'expired'].includes(task.status)) {
      return;
    }
    const timer = window.setInterval(async () => {
      try {
        const response = await apiFetch(`${API_BASE_URL}/ai-image/progress/${task.task_id}`);
        const result = await response.json();
        if (result.code === 0) {
          setTask(result.data);
        }
      } catch (error) {
        console.error(error);
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [task?.task_id, task?.status]);

  const fetchHistory = async (page = historyPage) => {
    setHistoryLoading(true);
    try {
      const query = new URLSearchParams({
        current: String(page),
        size: '10',
        prompt: historyPrompt,
        status: historyStatus,
        startDate: historyStartDate ? `${historyStartDate} 00:00:00` : '',
        endDate: historyEndDate ? `${historyEndDate} 23:59:59` : '',
      });
      const response = await apiFetch(`${API_BASE_URL}/ai-image/history?${query.toString()}`);
      const result = await response.json();
      if (result.code === 0) {
        setHistory(result.data.records || []);
        setHistoryTotal(result.data.total || 0);
        setHistoryPage(result.data.current || page);
      }
    } catch (error) {
      console.error(error);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleFileUpload = async (files: FileList | null, asFolder = false) => {
    if (!files || files.length === 0) return;
    setImporting(true);
    setMessage('正在上传素材...');
    try {
      const formData = new FormData();
      formData.append('public_base_url', publicBaseUrl);
      Array.from(files).forEach(file => formData.append('files', file));
      const response = await apiFetch(`${API_BASE_URL}/ai-image/${asFolder ? 'upload-folder' : 'upload'}`, {
        method: 'POST',
        body: formData,
      });
      const result = await response.json();
      if (result.code === 0) {
        setMode('image_to_image');
        setAssets(prev => [...prev, ...enrichAssets(result.data.assets || [])]);
        setMessage(`已上传 ${result.data.assets?.length || 0} 张素材`);
      } else {
        setMessage(result.message || '上传失败');
      }
    } catch (error) {
      console.error(error);
      setMessage('上传素材失败');
    } finally {
      setImporting(false);
      if (uploadRef.current) uploadRef.current.value = '';
      if (folderRef.current) folderRef.current.value = '';
    }
  };

  const handleImportUrls = async () => {
    const urls = urlText.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    if (!urls.length) {
      alert('请输入至少一个图片 URL');
      return;
    }
    setImporting(true);
    setMessage('正在导入 URL 素材...');
    try {
      const response = await apiFetch(`${API_BASE_URL}/ai-image/import-urls`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls, public_base_url: publicBaseUrl }),
      });
      const result = await response.json();
      if (result.code === 0) {
        setMode('image_to_image');
        setAssets(prev => [...prev, ...enrichAssets(result.data.assets || [])]);
        setUrlText('');
        setMessage(`已导入 ${result.data.assets?.length || 0} 张 URL 素材`);
      } else {
        setMessage(result.message || '导入 URL 失败');
      }
    } catch (error) {
      console.error(error);
      setMessage('导入 URL 请求失败');
    } finally {
      setImporting(false);
    }
  };

  const handleStartTask = async () => {
    if (!prompt.trim()) {
      alert('请输入提示词');
      return;
    }
    if (mode === 'image_to_image' && assets.length === 0) {
      alert('图生图至少需要一张参考图');
      return;
    }
    setSubmitting(true);
    setMessage('正在启动生图任务...');
    try {
      const payload = {
        task_type: mode,
        prompt,
        output_count: outputCount,
        repeat_count: mode === 'text_to_image' ? repeatCount : 1,
        batch_size: mode === 'image_to_image' ? batchSize : 10,
        size: size ? Number(size) : undefined,
        width: width ? Number(width) : undefined,
        height: height ? Number(height) : undefined,
        scale: scale ? Number(scale) : undefined,
        min_ratio: minRatio ? Number(minRatio) : undefined,
        max_ratio: maxRatio ? Number(maxRatio) : undefined,
        public_base_url: publicBaseUrl,
        assets: mode === 'image_to_image' ? assets : [],
      };
      const response = await apiFetch(`${API_BASE_URL}/ai-image/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (result.code === 0) {
        const progressResponse = await apiFetch(`${API_BASE_URL}/ai-image/progress/${result.data.task_id}`);
        const progressResult = await progressResponse.json();
        if (progressResult.code === 0) {
          setTask(progressResult.data);
        }
        setMessage('任务已提交');
      } else {
        setMessage(result.message || '启动任务失败');
      }
    } catch (error) {
      console.error(error);
      setMessage('启动任务请求失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleReuseHistory = async (taskId: string, itemId = '') => {
    try {
      const response = await apiFetch(`${API_BASE_URL}/ai-image/reuse/${taskId}${itemId ? `/${itemId}` : ''}`);
      const result = await response.json();
      if (result.code === 0) {
        setMode('image_to_image');
        setAssets(prev => [...prev, ...enrichAssets(result.data.assets || [])]);
        setHistoryOpen(false);
        setMessage(`已复用 ${result.data.assets?.length || 0} 张历史结果图`);
      } else {
        setMessage(result.message || '复用历史失败');
      }
    } catch (error) {
      console.error(error);
      setMessage('复用历史失败');
    }
  };

  const allResultImages = (task?.items || []).flatMap(item =>
    (item.preview_urls || item.result_urls || []).map((url, index) => ({
      id: `${item.item_id}-${index}`,
      url,
      status: item.status,
      itemId: item.item_id,
      batchIndex: item.batch_index,
    }))
  );

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="bg-white dark:bg-surface-dark border-b border-slate-200 dark:border-slate-800 p-6 shadow-sm z-10">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">AI 生图</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
              文生图与图生图统一工作台，支持批量任务、历史复用和结果回看。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setPrompt('');
                setAssets([]);
                setTask(null);
                setMessage('');
              }}
              className="inline-flex items-center gap-2 bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              <span className="material-icons text-[18px]">note_add</span>
              新建任务
            </button>
            <button
              onClick={() => { setHistoryOpen(true); fetchHistory(1); }}
              className="inline-flex items-center gap-2 bg-primary hover:bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg shadow transition-all"
            >
              <span className="material-icons text-[18px]">history</span>
              查看历史
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6 bg-background-light dark:bg-background-dark">
        <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-6">
          <div className="space-y-6">
            <div className={`${cardClass} p-5`}>
              <div className="inline-flex rounded-lg bg-slate-100 dark:bg-slate-800 p-1 mb-4">
                {[
                  { id: 'text_to_image', label: '文生图', icon: 'auto_awesome' },
                  { id: 'image_to_image', label: '图生图', icon: 'image' },
                ].map(tab => (
                  <button
                    key={tab.id}
                    onClick={() => setMode(tab.id as 'text_to_image' | 'image_to_image')}
                    className={`inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                      mode === tab.id
                        ? 'bg-white dark:bg-surface-dark text-primary shadow-sm'
                        : 'text-slate-500 dark:text-slate-300'
                    }`}
                  >
                    <span className="material-icons text-[18px]">{tab.icon}</span>
                    {tab.label}
                  </button>
                ))}
              </div>

              <div className="space-y-4">
                <div>
                  <label htmlFor="aiimagepage-field-1" className="block text-xs font-semibold text-slate-500 uppercase mb-1">提示词</label>
                  <textarea id="aiimagepage-field-1"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    rows={5}
                    placeholder={mode === 'text_to_image' ? '描述你想生成的画面...' : '描述你希望参考图如何被重绘...'}
                    className={`${inputClass} resize-none`}
                  />
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div>
                    <label htmlFor="aiimagepage-field-2" className="block text-xs font-semibold text-slate-500 uppercase mb-1">生成张数</label>
                    <select id="aiimagepage-field-2" className={inputClass} value={outputCount} onChange={(e) => setOutputCount(Number(e.target.value))}>
                      {[1, 2, 3, 4].map(value => <option key={value} value={value}>{value} 张</option>)}
                    </select>
                  </div>

                  {mode === 'text_to_image' ? (
                    <div>
                      <label htmlFor="aiimagepage-field-3" className="block text-xs font-semibold text-slate-500 uppercase mb-1">重复次数</label>
                      <input id="aiimagepage-field-3"
                        type="number"
                        min={1}
                        max={500}
                        value={repeatCount}
                        onChange={(e) => setRepeatCount(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
                        className={inputClass}
                      />
                    </div>
                  ) : (
                    <div>
                      <label htmlFor="aiimagepage-field-4" className="block text-xs font-semibold text-slate-500 uppercase mb-1">批次大小</label>
                      <input id="aiimagepage-field-4"
                        type="number"
                        min={1}
                        max={10}
                        value={batchSize}
                        onChange={(e) => setBatchSize(Math.max(1, Math.min(10, Number(e.target.value) || 10)))}
                        className={inputClass}
                      />
                    </div>
                  )}

                  <div>
                    <label htmlFor="aiimagepage-field-5" className="block text-xs font-semibold text-slate-500 uppercase mb-1">面积 size</label>
                    <input id="aiimagepage-field-5" value={size} onChange={(e) => setSize(e.target.value)} className={inputClass} />
                  </div>
                  <div>
                    <label htmlFor="aiimagepage-field-6" className="block text-xs font-semibold text-slate-500 uppercase mb-1">scale</label>
                    <input id="aiimagepage-field-6" value={scale} onChange={(e) => setScale(e.target.value)} className={inputClass} />
                  </div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div>
                    <label htmlFor="aiimagepage-field-7" className="block text-xs font-semibold text-slate-500 uppercase mb-1">宽 width</label>
                    <input id="aiimagepage-field-7" value={width} onChange={(e) => setWidth(e.target.value)} className={inputClass} />
                  </div>
                  <div>
                    <label htmlFor="aiimagepage-field-8" className="block text-xs font-semibold text-slate-500 uppercase mb-1">高 height</label>
                    <input id="aiimagepage-field-8" value={height} onChange={(e) => setHeight(e.target.value)} className={inputClass} />
                  </div>
                  <div>
                    <label htmlFor="aiimagepage-field-9" className="block text-xs font-semibold text-slate-500 uppercase mb-1">最小比例</label>
                    <input id="aiimagepage-field-9" value={minRatio} onChange={(e) => setMinRatio(e.target.value)} className={inputClass} />
                  </div>
                  <div>
                    <label htmlFor="aiimagepage-field-10" className="block text-xs font-semibold text-slate-500 uppercase mb-1">最大比例</label>
                    <input id="aiimagepage-field-10" value={maxRatio} onChange={(e) => setMaxRatio(e.target.value)} className={inputClass} />
                  </div>
                </div>

                <div className="flex items-center justify-between rounded-lg bg-slate-50 dark:bg-background-dark border border-slate-200 dark:border-slate-700 px-4 py-3">
                  <div className="text-sm text-slate-600 dark:text-slate-300">
                    {mode === 'text_to_image'
                      ? `当前将生成 ${repeatCount} 组任务，每组 ${outputCount} 张结果图`
                      : `当前素材 ${assets.length} 张，按每批 ${batchSize} 张拆分，最多输出 ${outputCount} 张/批`}
                  </div>
                  <button
                    onClick={handleStartTask}
                    disabled={submitting || importing}
                    className={`inline-flex items-center gap-2 bg-primary hover:bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg shadow transition-all ${submitting ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <span className="material-icons text-[18px]">play_arrow</span>
                    {submitting ? '提交中...' : '开始生成'}
                  </button>
                </div>
              </div>
            </div>

            {mode === 'image_to_image' && (
              <div className={`${cardClass} p-5 space-y-4`}>
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-semibold text-slate-800 dark:text-white">素材池</h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                      支持本地上传、文件夹导入、URL 导入和当前查询结果导入。
                    </p>
                  </div>
                  <button
                    onClick={() => setAssets([])}
                    className="text-xs text-slate-500 hover:text-slate-700 dark:text-slate-300 dark:hover:text-white"
                  >
                    清空素材池
                  </button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <button
                    onClick={() => uploadRef.current?.click()}
                    className="inline-flex items-center justify-center gap-2 border border-slate-300 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm font-medium hover:bg-slate-50 dark:hover:bg-surface-hover transition-colors"
                  >
                    <span className="material-icons text-[18px]">upload_file</span>
                    上传图片
                  </button>
                  <button
                    onClick={() => folderRef.current?.click()}
                    className="inline-flex items-center justify-center gap-2 border border-slate-300 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm font-medium hover:bg-slate-50 dark:hover:bg-surface-hover transition-colors"
                  >
                    <span className="material-icons text-[18px]">folder_open</span>
                    导入文件夹
                  </button>
                  <button
                    onClick={() => importExistingAssets(currentPreviewAssets)}
                    disabled={currentPreviewAssets.length === 0 || importing}
                    className="inline-flex items-center justify-center gap-2 border border-slate-300 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm font-medium hover:bg-slate-50 dark:hover:bg-surface-hover transition-colors disabled:opacity-40"
                  >
                    <span className="material-icons text-[18px]">photo_library</span>
                    导入当前查询图
                  </button>
                </div>

                <input
                  ref={uploadRef}
                  type="file"
                  accept="image/png,image/jpeg"
                  multiple
                  className="hidden"
                  onChange={(e) => handleFileUpload(e.target.files)}
                />
                <input
                  ref={folderRef}
                  type="file"
                  accept="image/png,image/jpeg"
                  multiple
                  className="hidden"
                  onChange={(e) => handleFileUpload(e.target.files, true)}
                />

                <div className="space-y-2">
                  <label htmlFor="aiimagepage-field-11" className="block text-xs font-semibold text-slate-500 uppercase mb-1">URL 导入</label>
                  <textarea id="aiimagepage-field-11"
                    value={urlText}
                    onChange={(e) => setUrlText(e.target.value)}
                    rows={3}
                    placeholder="每行一个图片 URL"
                    className={`${inputClass} resize-none`}
                  />
                  <button
                    onClick={handleImportUrls}
                    disabled={importing}
                    className="inline-flex items-center gap-2 bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 text-sm font-medium px-4 py-2 rounded-lg transition-colors"
                  >
                    <span className="material-icons text-[18px]">link</span>
                    导入 URL
                  </button>
                </div>

                {currentPreviewAssets.length > 0 && (
                  <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
                    <div className="px-4 py-3 bg-slate-50 dark:bg-background-dark flex items-center justify-between">
                      <div className="text-sm font-medium text-slate-700 dark:text-slate-200">当前查询结果</div>
                      <span className="text-xs text-slate-400">{currentPreviewAssets.length} 张可导入</span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-3 p-3">
                      {currentPreviewAssets.slice(0, 10).map(asset => (
                        <button
                          key={asset.id}
                          onClick={() => importExistingAssets([asset])}
                          className="group text-left"
                        >
                          <div className="aspect-square rounded-lg overflow-hidden bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
                            <AuthImage src={asset.preview_url || asset.url} alt={asset.name} className="w-full h-full object-cover group-hover:scale-105 transition-transform" />
                          </div>
                          <div className="text-xs text-slate-500 dark:text-slate-400 mt-1 truncate">{asset.name}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                  {assets.map(asset => (
                    <div key={asset.id} className="group rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
                      <button
                        onClick={() => setPreviewUrl(asset.preview_url || asset.url || '')}
                        className="w-full aspect-square bg-slate-100 dark:bg-slate-800 overflow-hidden"
                      >
                        {(asset.preview_url || asset.url) ? (
                          <AuthImage src={asset.preview_url || asset.url} alt={asset.name} className="w-full h-full object-cover group-hover:scale-105 transition-transform" />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center text-slate-400">
                            <span className="material-icons">image_not_supported</span>
                          </div>
                        )}
                      </button>
                      <div className="px-3 py-2 bg-slate-50 dark:bg-background-dark">
                        <div className="text-xs font-medium text-slate-700 dark:text-slate-200 truncate">{asset.name}</div>
                        <div className="flex items-center justify-between mt-1">
                          <span className="text-[11px] text-slate-400">{asset.source_type}</span>
                          <button
                            onClick={() => setAssets(prev => prev.filter(item => item.id !== asset.id))}
                            className="text-[11px] text-red-500 hover:underline"
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="space-y-6">
            <div className={`${cardClass} p-5`}>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-base font-semibold text-slate-800 dark:text-white">任务状态</h3>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                    当前任务进度、批次结果与失败摘要。
                  </p>
                </div>
                {task && (
                  <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${statusMap[task.status] || statusMap.pending}`}>
                    {task.status}
                  </span>
                )}
              </div>

              {!task ? (
                <div className="flex flex-col items-center justify-center h-44 text-slate-400">
                  <span className="material-icons text-5xl mb-2">pending</span>
                  <p className="text-sm">还没有提交生图任务</p>
                </div>
              ) : (
                <div className="space-y-4">
                  <div>
                    <div className="w-full bg-slate-200 dark:bg-slate-700 h-2 rounded-full overflow-hidden">
                      <div className="h-full bg-primary transition-all" style={{ width: `${task.progress}%` }} />
                    </div>
                    <div className="flex items-center justify-between mt-2 text-xs text-slate-500 dark:text-slate-400">
                      <span>{task.message || '任务执行中'}</span>
                      <span>{task.progress}%</span>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-lg bg-slate-50 dark:bg-background-dark px-3 py-2">
                      <div className="text-slate-400 text-xs">总批次</div>
                      <div className="font-semibold text-slate-800 dark:text-white">{task.total_items}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 dark:bg-background-dark px-3 py-2">
                      <div className="text-slate-400 text-xs">已完成</div>
                      <div className="font-semibold text-slate-800 dark:text-white">{task.completed_items}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 dark:bg-background-dark px-3 py-2">
                      <div className="text-slate-400 text-xs">成功批次</div>
                      <div className="font-semibold text-emerald-600">{task.success_items}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 dark:bg-background-dark px-3 py-2">
                      <div className="text-slate-400 text-xs">失败批次</div>
                      <div className="font-semibold text-red-500">{task.failed_items}</div>
                    </div>
                  </div>

                  <div className="space-y-2 max-h-56 overflow-auto">
                    {task.items.map(item => (
                      <div key={item.item_id} className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <div className="text-sm font-medium text-slate-700 dark:text-slate-200">
                            批次 {item.batch_index}
                          </div>
                          <span className={`text-[11px] px-2 py-0.5 rounded-full ${statusMap[item.status] || statusMap.pending}`}>
                            {item.status}
                          </span>
                        </div>
                        <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                          参考图 {item.asset_count} 张 {item.error_message ? `· ${item.error_message}` : ''}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className={`${cardClass} p-5`}>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-base font-semibold text-slate-800 dark:text-white">结果预览</h3>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                    支持查看、下载和再次生图。
                  </p>
                </div>
                {allResultImages.length > 0 && (
                  <span className="text-xs text-slate-400">{allResultImages.length} 张</span>
                )}
              </div>

              {allResultImages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-44 text-slate-400">
                  <span className="material-icons text-5xl mb-2">photo_library</span>
                  <p className="text-sm">任务完成后会在这里展示结果图</p>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3 max-h-[540px] overflow-auto">
                  {allResultImages.map(image => (
                    <div key={image.id} className="group rounded-lg overflow-hidden border border-slate-200 dark:border-slate-700">
                      <button onClick={() => setPreviewUrl(image.url)} className="w-full aspect-square bg-slate-100 dark:bg-slate-800 overflow-hidden">
                        <AuthImage src={image.url} alt="result" className="w-full h-full object-cover group-hover:scale-105 transition-transform" />
                      </button>
                      <div className="px-3 py-2 bg-slate-50 dark:bg-background-dark flex items-center justify-between">
                        <span className="text-xs text-slate-500">批次 {image.batchIndex}</span>
                        <a
                          href="#" onClick={event => { event.preventDefault(); void download(image.url); }}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-primary hover:underline"
                        >
                          打开
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {message && (
              <div className={`${cardClass} px-4 py-3 text-sm text-slate-600 dark:text-slate-300`}>
                {message}
              </div>
            )}
          </div>
        </div>
      </div>

      {historyOpen && (
        <Drawer open title="AI 生图历史" onClose={() => setHistoryOpen(false)} size={640} className="smarteye-legacy-form">
          <p className="smarteye-destination-note">按提示词、日期和状态筛选，可复用结果图。</p>
            <div className="grid grid-cols-2 gap-3 mb-4">
              <input aria-label="历史提示词" value={historyPrompt} onChange={(e) => setHistoryPrompt(e.target.value)} placeholder="提示词" className={inputClass} />
              <select aria-label="历史任务状态" value={historyStatus} onChange={(e) => setHistoryStatus(e.target.value)} className={inputClass}>
                <option value="">全部状态</option>
                <option value="pending">pending</option>
                <option value="running">running</option>
                <option value="completed">completed</option>
                <option value="partial_success">partial_success</option>
                <option value="failed">failed</option>
              </select>
              <input aria-label="历史开始日期" type="date" value={historyStartDate} onChange={(e) => setHistoryStartDate(e.target.value)} className={inputClass} />
              <input aria-label="历史结束日期" type="date" value={historyEndDate} onChange={(e) => setHistoryEndDate(e.target.value)} className={inputClass} />
            </div>

            <div className="flex items-center justify-between mb-4">
              <button
                onClick={() => fetchHistory(1)}
                className="inline-flex items-center gap-2 bg-primary hover:bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg"
              >
                <span className="material-icons text-[18px]">search</span>
                查询历史
              </button>
              <span className="text-sm text-slate-400">共 {historyTotal} 条</span>
            </div>

            <div className="space-y-3">
              {historyLoading ? (
                <div className="flex items-center justify-center h-40 text-slate-400">
                  <span className="material-icons animate-spin text-4xl">autorenew</span>
                </div>
              ) : history.length === 0 ? (
                <div className="flex items-center justify-center h-40 text-slate-400">暂无历史记录</div>
              ) : history.map(record => (
                <div key={record.task_id} className="rounded-xl border border-slate-200 dark:border-slate-700 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`text-[11px] px-2 py-0.5 rounded-full ${statusMap[record.task_status] || statusMap.pending}`}>
                          {record.task_status}
                        </span>
                        <span className="text-xs text-slate-400">{record.task_type === 'text_to_image' ? '文生图' : '图生图'}</span>
                      </div>
                      <div className="text-sm font-medium text-slate-800 dark:text-white mt-2 line-clamp-2">{record.prompt}</div>
                      <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">
                        创建时间：{new Date(record.created_at).toLocaleString('zh-CN')}
                      </div>
                    </div>
                    <button
                      onClick={() => handleReuseHistory(record.task_id)}
                      className="text-xs text-primary hover:underline flex-shrink-0"
                    >
                      复用结果图
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex items-center justify-between mt-4">
              <button
                onClick={() => fetchHistory(Math.max(1, historyPage - 1))}
                disabled={historyPage <= 1}
                className="px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-700 text-sm disabled:opacity-40"
              >
                上一页
              </button>
              <span className="text-sm text-slate-500">第 {historyPage} 页</span>
              <button
                onClick={() => fetchHistory(historyPage + 1)}
                disabled={historyPage * 10 >= historyTotal}
                className="px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-700 text-sm disabled:opacity-40"
              >
                下一页
              </button>
            </div>
        </Drawer>
      )}

      {previewUrl && <ImagePreview url={previewUrl} onClose={() => setPreviewUrl('')} />}
    </div>
  );
};

export default AiImagePage;

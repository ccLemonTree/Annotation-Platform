import { AutoComplete } from 'antd';
import ImagePreview from './ImagePreview';
import { apiFetch, AuthImage } from '../network';
import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from '../constants';
import type { PlatformOption } from '../types';

// ── Combobox: text input + filtered dropdown ──────────────────────────────────
interface ComboboxProps {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
  disabled?: boolean;
}

const Combobox: React.FC<ComboboxProps> = ({ value, onChange, options, placeholder, disabled }) => (
  <AutoComplete aria-label={placeholder} value={value} onChange={onChange} disabled={disabled}
    options={options.map(option => ({ value: option }))} placeholder={placeholder} style={{ width: '100%' }}
    filterOption={(input, option) => String(option?.value || '').toLowerCase().includes(input.toLowerCase())}
    notFoundContent="无匹配项" />
);

// ── LedgerQueryPage ───────────────────────────────────────────────────────────
const LedgerQueryPage: React.FC = () => {
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [platInput, setPlatInput] = useState('');
  const [alarmTypeInput, setAlarmTypeInput] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');

  const [images, setImages] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasQueried, setHasQueried] = useState(false);

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loadFailedUrls, setLoadFailedUrls] = useState<Set<string>>(new Set());
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    const now = new Date();
    const pad = (n: number) => n.toString().padStart(2, '0');
    const d = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    setStartTime(`${d}T00:00:00`);
    setEndTime(`${d}T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`);

    apiFetch(`${API_BASE_URL}/platform-list`)
      .then(r => r.json())
      .then(d => { if (d.code === 0) setPlatforms(d.data); })
      .catch(console.error);
  }, []);

  const selectedPlat = platforms.find(p => p.label === platInput);
  const alarmTypeOptions = selectedPlat ? Object.keys(selectedPlat.alarmTypes) : [];
  const platformOptions = platforms.map(p => p.label);

  const handlePlatChange = (v: string) => {
    setPlatInput(v);
    setAlarmTypeInput('');
  };

  const handleSearch = async () => {
    if (!platInput.trim()) { alert('请选择或输入平台'); return; }
    if (!startTime || !endTime) { alert('请选择时间范围'); return; }
    setLoading(true);
    setError(null);
    setHasQueried(true);
    setImages([]);
    setLoadFailedUrls(new Set());
    try {
      // 确保时间格式包含秒数 (YYYY-MM-DD HH:MM:SS)
      const formatTime = (t: string) => {
        const parts = t.replace('T', ' ').split(':');
        if (parts.length === 2) return `${t.replace('T', ' ')}:00`; // 补充秒数
        return t.replace('T', ' ');
      };

      const res = await apiFetch(`${API_BASE_URL}/ledger/device/log`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          platName: platInput.trim(),
          payload: {
            current: 1, size: 1000,
            model: {
              deviceId: deviceId.trim(),
              startTime: formatTime(startTime),
              endTime: formatTime(endTime),
            },
          },
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.code === 0 && data.data?.records) {
        let records: any[] = data.data.records;
        if (alarmTypeInput.trim()) {
          records = records.filter(r => r.analyseParam?.includes(alarmTypeInput.trim()));
        }
        setImages(records);
      } else {
        throw new Error(data.msg || '获取数据失败');
      }
    } catch (err: any) {
      setError(err.message || '请求失败');
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async () => {
    const urls = images.map(img => img.backPath).filter(Boolean);
    if (!urls.length) return;
    setDownloading(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/download-images`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls, collection_name: platInput.trim(), filename: `台账查询_${platInput}` }),
      });
      if (!res.ok) throw new Error('下载失败');
      const blob = await res.blob();
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `台账查询_${platInput}.zip`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (e: any) {
      alert(e.message || '下载出错');
    } finally {
      setDownloading(false);
    }
  };

  const validImages = images.filter(img => img.backPath);
  const ic = "w-full bg-slate-50 dark:bg-background-dark border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 text-sm rounded-lg focus:ring-primary focus:border-primary block p-2.5";

  return (
    <div className="flex-1 overflow-auto bg-background-light dark:bg-background-dark transition-colors duration-300">
      {/* Filter Bar */}
      <div className="bg-white dark:bg-surface-dark border-b border-slate-200 dark:border-slate-800 px-6 pt-5 pb-5 shadow-sm" style={{ overflow: 'visible', zIndex: 30, position: 'relative' }}>
        <div className="max-w-7xl mx-auto space-y-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">台账图片查询</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">按平台、设备、时间范围查询台账抓拍图片。</p>
          </div>
          <div className="grid grid-cols-12 gap-4 items-end" style={{ overflow: 'visible' }}>
            {/* Platform combobox */}
            <div className="col-span-12 sm:col-span-6 lg:col-span-3 space-y-1.5">
              <label className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">
                平台 <span className="text-red-500">*</span>
              </label>
              <Combobox value={platInput} onChange={handlePlatChange} options={platformOptions} placeholder="输入或选择平台" />
            </div>
            {/* Alarm type combobox */}
            <div className="col-span-12 sm:col-span-6 lg:col-span-3 space-y-1.5">
              <label className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">预警类型</label>
              <Combobox value={alarmTypeInput} onChange={setAlarmTypeInput} options={alarmTypeOptions} placeholder="输入或选择预警类型" disabled={!platInput} />
            </div>
            {/* DeviceId */}
            <div className="col-span-12 sm:col-span-6 lg:col-span-2 space-y-1.5">
              <label className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">设备ID</label>
              <input type="text" className={ic} placeholder="可选" value={deviceId} onChange={e => setDeviceId(e.target.value)} />
            </div>
            {/* Start time */}
            <div className="col-span-6 sm:col-span-3 lg:col-span-2 space-y-1.5">
              <label htmlFor="ledgerquerypage-field-1" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">开始时间</label>
              <input id="ledgerquerypage-field-1" type="datetime-local" step="1" className={`${ic} dark:text-white`} value={startTime} onChange={e => setStartTime(e.target.value)} />
            </div>
            {/* End time */}
            <div className="col-span-6 sm:col-span-3 lg:col-span-2 space-y-1.5">
              <label htmlFor="ledgerquerypage-field-2" className="block text-xs font-semibold text-slate-500 dark:text-slate-300 uppercase tracking-wide">结束时间</label>
              <input id="ledgerquerypage-field-2" type="datetime-local" step="1" className={`${ic} dark:text-white`} value={endTime} onChange={e => setEndTime(e.target.value)} />
            </div>
            {/* Buttons */}
            <div className="col-span-12 sm:col-span-6 lg:col-span-2 flex gap-2 pb-0.5">
              <button
                onClick={() => { setPlatInput(''); setAlarmTypeInput(''); setDeviceId(''); setHasQueried(false); setImages([]); setError(null); }}
                className="flex-1 bg-white dark:bg-transparent border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-surface-hover font-medium rounded-lg text-sm px-4 py-2.5 transition-all"
              >重置</button>
              <button
                onClick={handleSearch}
                disabled={loading}
                className="flex-1 text-white bg-primary hover:bg-blue-600 font-medium rounded-lg text-sm px-4 py-2.5 transition-all shadow-lg shadow-blue-500/30 disabled:opacity-50"
              >{loading ? '查询中...' : '查询'}</button>
            </div>
          </div>
        </div>
      </div>

      {/* Results */}
      <div className="p-6">
        <div className="max-w-7xl mx-auto">
          {hasQueried && !loading && !error && (
            <div className="flex items-center justify-between mb-4">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                共 {images.length} 条记录，其中 {validImages.length} 张有图片
              </p>
              {validImages.length > 0 && (
                <button
                  onClick={handleDownload}
                  disabled={downloading}
                  className={`inline-flex items-center gap-2 bg-primary hover:bg-blue-600 text-white font-medium rounded-lg text-sm px-4 py-2 shadow-md shadow-blue-900/20 transition-all ${downloading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <span className="material-icons text-[18px]">download</span>
                  {downloading ? '下载中...' : `下载全部图片 (${validImages.length})`}
                </button>
              )}
            </div>
          )}
          <div className="bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm p-6">
            {loading ? (
              <div className="flex flex-col items-center justify-center h-64 text-slate-400">
                <span className="material-icons animate-spin text-4xl mb-2 text-primary">autorenew</span>
                <p>正在查询...</p>
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center h-64 text-red-500">
                <span className="material-icons text-5xl mb-2">error_outline</span>
                <p>{error}</p>
              </div>
            ) : !hasQueried ? (
              <div className="flex flex-col items-center justify-center h-64 text-slate-400">
                <span className="material-icons text-5xl mb-2">image_search</span>
                <p>请设置查询条件后点击查询</p>
              </div>
            ) : images.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-64 text-slate-400">
                <span className="material-icons text-5xl mb-2">image_not_supported</span>
                <p>该条件下没有台账记录</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                {images.map((img, idx) => (
                  <div key={idx} className="flex flex-col group rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700 hover:shadow-md transition-shadow">
                    <button type="button" aria-label="预览台账图片" disabled={!img.backPath || loadFailedUrls.has(img.backPath)}
                      className={`relative w-full border-0 p-0 aspect-video bg-slate-100 dark:bg-slate-800 flex items-center justify-center overflow-hidden ${img.backPath && !loadFailedUrls.has(img.backPath) ? 'cursor-pointer' : ''}`}
                      onClick={() => img.backPath && !loadFailedUrls.has(img.backPath) && setPreviewUrl(img.backPath)}
                    >
                      {img.backPath ? (
                        loadFailedUrls.has(img.backPath!) ? (
                          <div className="flex flex-col items-center justify-center h-full text-slate-400">
                            <span className="material-icons text-3xl">broken_image</span>
                            <span className="text-xs mt-1">加载失败</span>
                          </div>
                        ) : (
                          <>
                            <AuthImage
                              src={img.backPath} alt="台账图片"
                              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                              onError={() => {
                                setLoadFailedUrls(prev => new Set(prev).add(img.backPath));
                              }}
                            />
                            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors flex items-center justify-center">
                              <span className="material-icons text-white opacity-0 group-hover:opacity-100 transition-opacity text-3xl">zoom_in</span>
                            </div>
                          </>
                        )
                      ) : (
                        <div className="flex flex-col items-center justify-center text-slate-400">
                          <span className="material-icons text-3xl">image_not_supported</span>
                          <span className="text-xs mt-1">无图片路径</span>
                        </div>
                      )}
                      <div className="absolute top-2 left-2 flex gap-1">
                        {img.success && <span className="bg-green-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-sm">成功</span>}
                        {img.analyse && <span className="bg-blue-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-sm">已分析</span>}
                      </div>
                    </button>
                    <div className="p-3 bg-slate-50 dark:bg-slate-800/50">
                      <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">{img.captureTime || '-'}</span>
                      {img.analyseRes && img.analyseRes !== '无,{}' && img.analyseRes !== '无.,{}' && (
                        <p className="text-xs text-slate-500 dark:text-slate-400 line-clamp-2 mt-1" title={img.analyseRes}>{img.analyseRes}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Preview Modal */}
      {previewUrl && <ImagePreview url={previewUrl} onClose={() => setPreviewUrl(null)} />}
    </div>
  );
};

export default LedgerQueryPage;

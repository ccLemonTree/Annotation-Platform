import { apiFetch, AuthImage } from '../network';
import React, { useEffect, useState } from 'react';
import type { AiImageAsset, DetectionRecord } from '../types';
import { API_BASE_URL } from '../constants';

interface LedgerPageProps {
  record: DetectionRecord;
  onBack: () => void;
  onSendToAiImage?: (assets: AiImageAsset[]) => void;
}

const LedgerPage: React.FC<LedgerPageProps> = ({ record, onBack, onSendToAiImage }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [images, setImages] = useState<any[]>([]);
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    const urls = images.map(img => img.backPath).filter(Boolean);
    if (!urls.length) return;
    setDownloading(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/download-images`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls, collection_name: record.platform, filename: `台账_${record.deviceId || record.device}` }),
      });
      if (!res.ok) throw new Error('下载失败');
      const blob = await res.blob();
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `台账_${record.deviceId || record.device}.zip`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (e: any) {
      alert(e.message || '下载出错');
    } finally {
      setDownloading(false);
    }
  };

  const formatDate = (date: Date) => {
    const pad = (n: number) => n.toString().padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  };

  useEffect(() => {
    const fetchLedgerData = async () => {
      setLoading(true);
      setError(null);
      try {
        const endTimeStr = record.timestamp || '';
        let endTime = new Date();
        if (endTimeStr && endTimeStr !== '-') {
          endTime = new Date(endTimeStr.replace(' ', 'T'));
        }
        const startTime = new Date(endTime.getTime() - 5 * 60 * 1000);
        const deviceId = record.deviceId || record.device || '';

        const payload = {
          current: 1,
          size: 5,
          model: {
            deviceId,
            startTime: formatDate(startTime),
            endTime: formatDate(endTime),
          },
        };

        const response = await apiFetch(`${API_BASE_URL}/ledger/device/log`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            platName: record.platform,
            payload,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        if (data.code === 0 && data.data && data.data.records) {
          setImages(data.data.records);
        } else {
          throw new Error(data.msg || '获取数据失败');
        }
      } catch (err: any) {
        console.error('Fetch ledger failed:', err);
        setError(err.message || '网络请求失败');
      } finally {
        setLoading(false);
      }
    };

    fetchLedgerData();
  }, [record]);

  const buildAsset = (img: any, idx: number): AiImageAsset[] => (
    img.backPath ? [{
      id: `${record.id}-ledger-${idx}`,
      name: `${record.platform}_${record.deviceId || record.device || 'ledger'}_${idx + 1}.jpg`,
      source_type: 'ledger',
      url: img.backPath,
      preview_url: img.backPath,
    }] : []
  );

  return (
    <div className="flex-1 overflow-auto p-6 bg-background-light dark:bg-background-dark transition-colors duration-300">
      <div className="max-w-7xl mx-auto h-full flex flex-col">
        <div className="flex items-center gap-4 mb-6">
          <button
            onClick={onBack}
            className="w-10 h-10 flex items-center justify-center rounded-full bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-700 shadow-sm text-slate-500 hover:text-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800 transition-all"
          >
            <span className="material-icons">arrow_back</span>
          </button>
          <div className="flex-1">
            <h2 className="text-xl font-bold text-slate-800 dark:text-white">设备台账</h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
              设备ID: {record.deviceId || record.device} · 报警时间: {record.timestamp} (显示报警前 5 分钟抓拍)
            </p>
          </div>
          {images.length > 0 && (
            <button
              onClick={handleDownload}
              disabled={downloading}
              className={`inline-flex items-center gap-2 bg-primary hover:bg-blue-600 text-white font-medium rounded-lg text-sm px-4 py-2 shadow-md shadow-blue-900/20 transition-all ${downloading ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span className="material-icons text-[18px]">download</span>
              {downloading ? '下载中...' : '下载图片'}
            </button>
          )}
        </div>

        <div className="flex-1 bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm p-6 overflow-auto">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-64 text-slate-400">
              <span className="material-icons animate-spin text-4xl mb-2 text-primary">autorenew</span>
              <p>正在加载图片数据...</p>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center h-64 text-red-500">
              <span className="material-icons text-5xl mb-2">error_outline</span>
              <p>{error}</p>
            </div>
          ) : images.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-slate-400">
              <span className="material-icons text-5xl mb-2">image_not_supported</span>
              <p>该时间段内没有抓拍图片</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
              {images.map((img, idx) => (
                <div key={idx} className="flex flex-col group rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700 hover:shadow-md transition-shadow">
                  <div className="relative aspect-video bg-slate-100 dark:bg-slate-800 flex items-center justify-center overflow-hidden">
                    {img.backPath ? (
                      <AuthImage
                        src={img.backPath}
                        alt="抓拍图片"
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display = 'none';
                          const parent = (e.target as HTMLImageElement).parentElement;
                          if (parent) {
                            const div = document.createElement('div');
                            div.className = 'flex flex-col items-center justify-center h-full text-slate-400';
                            div.innerHTML = '<span class="material-icons text-3xl">broken_image</span><span class="text-xs mt-1">加载失败</span>';
                            parent.replaceChildren(div);
                          }
                        }}
                      />
                    ) : (
                      <div className="flex flex-col items-center justify-center text-slate-400">
                        <span className="material-icons text-3xl">image_not_supported</span>
                        <span className="text-xs mt-1">无图片路径</span>
                      </div>
                    )}

                    <div className="absolute top-2 left-2 flex gap-1">
                      {img.success && (
                        <span className="bg-green-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-sm">
                          成功
                        </span>
                      )}
                      {img.analyse && (
                        <span className="bg-blue-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-sm">
                          已分析
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="p-3 bg-slate-50 dark:bg-slate-800/50">
                    <div className="flex justify-between items-center mb-1 gap-2">
                      <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                        时间: {img.captureTime?.split(' ')[1] || '-'}
                      </span>
                      {img.backPath && (
                        <button
                          onClick={() => onSendToAiImage?.(buildAsset(img, idx))}
                          className="text-xs text-primary hover:text-blue-700 font-medium"
                        >
                          AI 生图
                        </button>
                      )}
                    </div>
                    {img.analyseRes && img.analyseRes !== '无,{}' && (
                      <p className="text-xs text-slate-500 dark:text-slate-400 line-clamp-2 mt-1" title={img.analyseRes}>
                        {img.analyseRes}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LedgerPage;

import { download } from './network';
import IntegrationBar from './IntegrationBar';
import { apiFetch } from './network';
import React, { useMemo, useState } from 'react';
import Sidebar from './components/Sidebar';
import FilterBar from './components/FilterBar';
import ResultsTable from './components/ResultsTable';
import SmartSearchPage from './components/SmartSearchPage';
import LedgerPage from './components/LedgerPage';
import LedgerQueryPage from './components/LedgerQueryPage';
import LedgerSchedulePage from './components/LedgerSchedulePage';
import AlarmLogPage from './components/AlarmLogPage';
import AiImagePage from './components/AiImagePage';
import ToolTaskPage from './components/ToolTaskPage';
import type { AiImageAsset, DetectionRecord } from './types';
import { API_BASE_URL } from './constants';

const PAGE_SIZE = 20;

const App: React.FC = () => {
  const [activePage, setActivePage] = useState('detection');
  const [data, setData] = useState<DetectionRecord[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadMsg, setDownloadMsg] = useState('');
  const [lastSearchParams, setLastSearchParams] = useState<any>(null);
  const [ledgerRecord, setLedgerRecord] = useState<DetectionRecord | null>(null);
  const [alarmLogRecord, setAlarmLogRecord] = useState<DetectionRecord | null>(null);
  const [seededAiAssets, setSeededAiAssets] = useState<AiImageAsset[]>([]);
  const abortControllerRef = React.useRef<AbortController | null>(null);

  const transformData = (backendRecords: any[]): DetectionRecord[] => {
    return backendRecords.map(record => ({
      id: record.id,
      platform: record.platform,
      dataType: record.dataType || 'alarm',
      targetType: record.targetType || 'unknown',
      count: 1,
      targetColor: record.targetColor || 'red',
      device: record.device || '-',
      deviceId: record.deviceId || '',
      timestamp: record.timestamp || '-',
      picUrl: record.picUrl || '',
    }));
  };

  const recentAssets = useMemo(
    () => data
      .filter(record => !!record.picUrl)
      .map(record => ({
        id: record.id,
        name: `${record.platform}_${record.deviceId || record.device || 'record'}.jpg`,
        source_type: 'detection',
        url: record.picUrl,
        preview_url: record.picUrl,
      })),
    [data]
  );

  const sendAssetsToAiImage = (assets: AiImageAsset[]) => {
    if (!assets.length) {
      return;
    }
    setSeededAiAssets(assets);
    setActivePage('ai-image');
  };

  const fetchPage = async (filters: any, page: number) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setData([]);
    try {
      const response = await apiFetch(`${API_BASE_URL}/platforms`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...filters,
          current: page,
          size: PAGE_SIZE,
        }),
        signal: controller.signal,
      });
      const res = await response.json();
      if (res.code === 0) {
        const records = res.data.records || [];
        const total = res.data.total || 0;
        const pages = res.data.pages || Math.ceil(total / PAGE_SIZE);
        setData(transformData(records));
        setTotalCount(total);
        setTotalPages(pages);
        setCurrentPage(page);
      } else {
        alert(`查询失败: ${res.message}`);
      }
    } catch (error: any) {
      if (error.name !== 'AbortError') {
        console.error('Search error:', error);
        alert('查询出错');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = (filters: any) => {
    setLastSearchParams(filters);
    setCurrentPage(1);
    fetchPage(filters, 1);
  };

  const handlePageChange = (page: number) => {
    if (!lastSearchParams || page < 1 || page > totalPages || page === currentPage) {
      return;
    }
    fetchPage(lastSearchParams, page);
  };

  const handleDownload = async () => {
    if (!lastSearchParams || (!lastSearchParams.platName && (!lastSearchParams.platNames || lastSearchParams.platNames.length === 0))) {
      alert('请先进行查询');
      return;
    }
    setDownloading(true);
    setDownloadMsg('正在启动下载任务...');
    try {
      const payload = {
        platName: lastSearchParams.platName,
        platNames: lastSearchParams.platNames || [],
        sourceType: lastSearchParams.sourceType || 'alarm',
        targetType: lastSearchParams.targetType,
        deviceId: lastSearchParams.deviceId,
        startTime: lastSearchParams.startTime,
        endTime: lastSearchParams.endTime,
      };
      const response = await apiFetch(`${API_BASE_URL}/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const res = await response.json();
      if (res.code === 0) {
        const taskId = res.data.task_id;
        setDownloadMsg(`任务已提交，ID: ${taskId}`);
        pollProgress(taskId);
      } else {
        setDownloadMsg(`下载启动失败: ${res.message}`);
        setDownloading(false);
      }
    } catch (error) {
      console.error(error);
      setDownloadMsg('请求失败');
      setDownloading(false);
    }
  };

  const pollProgress = (taskId: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await apiFetch(`${API_BASE_URL}/progress/${taskId}`);
        const data = await res.json();
        if (data.code === 0) {
          const task = data.data;
          setDownloadMsg(`[${task.status}] ${task.message} (${task.progress}%)`);

          if (task.status === 'completed') {
            clearInterval(interval);
            setDownloading(false);
            void download(`${API_BASE_URL}/download-zip/${taskId}`);
            setDownloadMsg('ZIP 打包完成，浏览器正在下载...');
          } else if (task.status === 'failed') {
            clearInterval(interval);
            setDownloading(false);
          }
        }
      } catch (error) {
        console.error(error);
      }
    }, 1000);
  };

  const handleViewLedger = (record: DetectionRecord) => {
    setLedgerRecord(record);
    setActivePage('ledger');
  };

  const handleViewAlarmLog = (record: DetectionRecord) => {
    setAlarmLogRecord(record);
    setActivePage('alarm-log');
  };

  return (
    <div className="smarteye-app"><IntegrationBar /><div className="smarteye-body">
    <div className="smarteye-layout">
      <Sidebar activePage={activePage} onPageChange={setActivePage} />
      <main className="smarteye-content flex-1 min-w-0 flex flex-col overflow-auto relative">

        {activePage === 'smart-search' ? (
          <SmartSearchPage />
        ) : activePage === 'ledger-query' ? (
          <LedgerQueryPage />
        ) : activePage === 'ledger-schedule' ? (
          <LedgerSchedulePage />
        ) : activePage === 'tool-task' ? (
          <ToolTaskPage />
        ) : activePage === 'ai-image' ? (
          <AiImagePage
            recentAssets={recentAssets}
            seededAssets={seededAiAssets}
            onConsumeSeedAssets={() => setSeededAiAssets([])}
          />
        ) : activePage === 'ledger' && ledgerRecord ? (
          <LedgerPage
            record={ledgerRecord}
            onBack={() => setActivePage('detection')}
            onSendToAiImage={sendAssetsToAiImage}
          />
        ) : activePage === 'alarm-log' && alarmLogRecord ? (
          <AlarmLogPage
            record={alarmLogRecord}
            onBack={() => setActivePage('detection')}
            onSendToAiImage={sendAssetsToAiImage}
          />
        ) : (
          <>
            <FilterBar onSearch={handleSearch} />

            <div className="flex-1 overflow-auto p-6 bg-background-light dark:bg-background-dark transition-colors duration-300">
              <div className="max-w-7xl mx-auto h-full flex flex-col">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-4 gap-4">
                  <div className="flex items-baseline gap-2">
                    <h2 className="text-lg font-semibold text-slate-800 dark:text-white">检测结果</h2>
                    <span className="text-sm text-slate-500 dark:text-slate-400 font-normal">
                      共找到 {totalCount} 条记录
                    </span>
                    {loading && <span className="text-sm text-blue-500">加载中...</span>}
                  </div>

                  <div className="flex items-center gap-4">
                    {downloadMsg && <span className="text-sm text-slate-600 dark:text-slate-300">{downloadMsg}</span>}
                    <button
                      onClick={handleDownload}
                      disabled={downloading || !lastSearchParams}
                      className={`group inline-flex items-center justify-center gap-2 bg-primary hover:bg-blue-600 text-white font-medium rounded-lg text-sm px-5 py-2.5 shadow-md shadow-blue-900/20 transition-all ${downloading ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      <span className="material-icons text-[20px] group-hover:-translate-y-0.5 transition-transform duration-200">
                        folder_zip
                      </span>
                      <span>{downloading ? '下载中...' : '下载所有图片'}</span>
                    </button>
                  </div>
                </div>

                <ResultsTable
                  data={data}
                  totalCount={totalCount}
                  totalPages={totalPages}
                  currentPage={currentPage}
                  pageSize={PAGE_SIZE}
                  onPageChange={handlePageChange}
                  onViewLedger={handleViewLedger}
                  onViewAlarmLog={handleViewAlarmLog}
                  onSendToAiImage={sendAssetsToAiImage}
                />
              </div>
            </div>
          </>
        )}
      </main>
    </div>
    </div></div>
  );
};

export default App;

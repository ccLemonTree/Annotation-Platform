import { AuthImage, download } from '../network';
import { Empty, Modal } from 'antd';
import React, { useState } from 'react';
import type { AiImageAsset, DetectionRecord } from '../types';

interface ResultsTableProps {
  data: DetectionRecord[];
  totalCount?: number;
  totalPages?: number;
  currentPage?: number;
  pageSize?: number;
  onPageChange?: (page: number) => void;
  onViewLedger?: (record: DetectionRecord) => void;
  onViewAlarmLog?: (record: DetectionRecord) => void;
  onSendToAiImage?: (assets: AiImageAsset[]) => void;
}

const ResultsTable: React.FC<ResultsTableProps> = ({
  data,
  totalCount = 0,
  totalPages = 0,
  currentPage = 1,
  pageSize = 20,
  onPageChange,
  onViewLedger,
  onViewAlarmLog,
  onSendToAiImage,
}) => {
  const [previewRecord, setPreviewRecord] = useState<DetectionRecord | null>(null);
  const startIdx = totalCount > 0 ? (currentPage - 1) * pageSize + 1 : 0;
  const endIdx = Math.min(currentPage * pageSize, totalCount);

  const buildAsset = (record: DetectionRecord): AiImageAsset[] => (
    record.picUrl ? [{
      id: record.id,
      name: `${record.platform}_${record.deviceId || record.device || 'record'}.jpg`,
      source_type: 'detection',
      url: record.picUrl,
      preview_url: record.picUrl,
    }] : []
  );

  const getPageNumbers = (): (number | '...')[] => {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const pages: (number | '...')[] = [];
    const left = Math.max(2, currentPage - 1);
    const right = Math.min(totalPages - 1, currentPage + 1);
    pages.push(1);
    if (left > 2) pages.push('...');
    for (let i = left; i <= right; i++) pages.push(i);
    if (right < totalPages - 1) pages.push('...');
    pages.push(totalPages);
    return pages;
  };

  return (
    <>
      <div className="flex-1 bg-white dark:bg-surface-dark border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm overflow-hidden flex flex-col transition-colors duration-300">
        <div className="overflow-x-auto flex-1">
          <table className="w-full text-sm text-left text-slate-500 dark:text-slate-400">
            <thead className="text-xs text-slate-700 uppercase bg-slate-50 dark:bg-slate-800/50 dark:text-slate-300 sticky top-0 z-10 backdrop-blur-sm">
              <tr>
                <th className="p-4 w-4" />
                <th className="px-6 py-4 font-semibold tracking-wider">平台</th>
                <th className="px-6 py-4 font-semibold tracking-wider">数据类型</th>
                <th className="px-6 py-4 font-semibold tracking-wider">检测目标</th>
                <th className="px-6 py-4 font-semibold tracking-wider">deviceId</th>
                <th className="px-6 py-4 font-semibold tracking-wider">检测时间</th>
                <th className="px-6 py-4 font-semibold tracking-wider text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {!data.length && <tr><td colSpan={7}><Empty description="暂无检测记录"><p className="text-slate-500">选择平台和时间范围后点击查询</p></Empty></td></tr>}
              {data.map((record) => (
                <tr
                  key={record.id}
                  className={`bg-white dark:bg-surface-dark hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors ${record.dataType === 'alarm' ? 'cursor-pointer' : ''}`}
                  onClick={() => record.dataType === 'alarm' && onViewAlarmLog?.(record)}
                >
                  <td className="w-4 p-4" />
                  <td className="px-6 py-4 font-medium text-slate-900 dark:text-white whitespace-nowrap">
                    {record.platform}
                  </td>
                  <td className="px-6 py-4">
                    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                      record.dataType === 'alarm'
                        ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300'
                        : record.dataType === 'capture'
                          ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300'
                          : 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300'
                    }`}>
                      {record.dataType === 'alarm' ? '报警数据' : record.dataType === 'capture' ? '抓图数据' : '废片数据'}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${
                        record.targetColor === 'red'
                          ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]'
                          : record.targetColor === 'blue'
                            ? 'bg-blue-500'
                            : record.targetColor === 'yellow'
                              ? 'bg-yellow-500'
                              : 'bg-slate-400'
                      }`} />
                      <span className={`font-medium ${
                        record.targetColor === 'red'
                          ? 'text-red-500 dark:text-red-400'
                          : record.targetColor === 'blue'
                            ? 'text-blue-600 dark:text-blue-400'
                            : record.targetColor === 'yellow'
                              ? 'text-yellow-600 dark:text-yellow-400'
                              : 'text-slate-600 dark:text-slate-400'
                      }`}>
                        {record.targetType}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4 font-mono text-xs text-slate-600 dark:text-slate-400">
                    {record.deviceId || '-'}
                  </td>
                  <td className="px-6 py-4 font-mono text-slate-600 dark:text-slate-400">
                    {record.timestamp}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex justify-end gap-3 flex-wrap">
                      {record.dataType === 'alarm' && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onViewLedger?.(record); }}
                          className="text-primary hover:text-blue-700 font-medium text-sm inline-flex items-center gap-1 transition-colors"
                        >
                          查看台账
                          <span className="material-icons text-[16px]">receipt_long</span>
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); setPreviewRecord(record); }}
                        className="text-primary hover:text-blue-400 font-medium text-sm inline-flex items-center gap-1 transition-colors"
                      >
                        查看详情
                        <span className="material-icons text-[16px]">arrow_forward</span>
                      </button>
                      {record.picUrl && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onSendToAiImage?.(buildAsset(record)); }}
                          className="text-primary hover:text-blue-700 font-medium text-sm inline-flex items-center gap-1 transition-colors"
                        >
                          AI 生图
                          <span className="material-icons text-[16px]">auto_awesome</span>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="bg-white dark:bg-surface-dark border-t border-slate-200 dark:border-slate-800 px-4 py-3 flex items-center justify-between">
          <div className="flex-1 flex justify-between sm:hidden">
            <button
              aria-label="上一页"
              onClick={() => onPageChange?.(currentPage - 1)}
              disabled={currentPage <= 1}
              className="relative inline-flex items-center px-4 py-2 border border-slate-300 dark:border-slate-600 text-sm font-medium rounded-md text-slate-700 dark:text-slate-200 bg-white dark:bg-slate-700 hover:bg-slate-50 dark:hover:bg-slate-600 disabled:opacity-40"
            >
              上一页
            </button>
            <span className="inline-flex items-center text-sm text-slate-600 dark:text-slate-300">
              {currentPage} / {Math.max(1, totalPages)}
            </span>
            <button
              aria-label="下一页"
              onClick={() => onPageChange?.(currentPage + 1)}
              disabled={currentPage >= totalPages}
              className="ml-3 relative inline-flex items-center px-4 py-2 border border-slate-300 dark:border-slate-600 text-sm font-medium rounded-md text-slate-700 dark:text-slate-200 bg-white dark:bg-slate-700 hover:bg-slate-50 dark:hover:bg-slate-600 disabled:opacity-40"
            >
              下一页
            </button>
          </div>

          <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
            <div>
              <p className="text-sm text-slate-700 dark:text-slate-400">
                显示第 <span className="font-medium text-slate-900 dark:text-white">{startIdx}</span> 到{' '}
                <span className="font-medium text-slate-900 dark:text-white">{endIdx}</span> 条，共{' '}
                <span className="font-medium text-slate-900 dark:text-white">{totalCount}</span> 条结果
              </p>
            </div>
            <div>
              <nav className="relative z-0 inline-flex rounded-md shadow-sm -space-x-px" aria-label="Pagination">
                <button
                  onClick={() => onPageChange?.(currentPage - 1)}
                  disabled={currentPage <= 1}
                  className="relative inline-flex items-center px-2 py-2 rounded-l-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-surface-dark text-sm font-medium text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-40"
                >
                  <span className="material-icons text-sm">chevron_left</span>
                </button>
                {getPageNumbers().map((page, index) => page === '...'
                  ? (
                    <span
                      key={`ellipsis-${index}`}
                      className="relative inline-flex items-center px-4 py-2 border border-slate-300 dark:border-slate-700 bg-white dark:bg-surface-dark text-sm font-medium text-slate-700 dark:text-slate-400"
                    >
                      ...
                    </span>
                  ) : (
                    <button
                      key={page}
                      onClick={() => onPageChange?.(page as number)}
                      className={page === currentPage
                        ? 'z-10 bg-primary/20 border-primary text-primary relative inline-flex items-center px-4 py-2 border text-sm font-medium'
                        : 'bg-white dark:bg-surface-dark border-slate-300 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800 relative inline-flex items-center px-4 py-2 border text-sm font-medium'}
                    >
                      {page}
                    </button>
                  ))}
                <button
                  onClick={() => onPageChange?.(currentPage + 1)}
                  disabled={currentPage >= totalPages}
                  className="relative inline-flex items-center px-2 py-2 rounded-r-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-surface-dark text-sm font-medium text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-40"
                >
                  <span className="material-icons text-sm">chevron_right</span>
                </button>
              </nav>
            </div>
          </div>
        </div>
      </div>

      {previewRecord && (
        <Modal open title="图片详情" onCancel={() => setPreviewRecord(null)} footer={null} width={800} className="smarteye-legacy-form">
          <p className="smarteye-destination-note">{previewRecord.platform} · {previewRecord.targetType} · {previewRecord.timestamp}</p>
            <div className="flex-1 overflow-auto p-6 flex items-center justify-center min-h-[300px]">
              {previewRecord.picUrl ? (
                <div className="flex flex-col items-center gap-4">
                  <AuthImage
                    src={previewRecord.picUrl}
                    alt={`${previewRecord.targetType} - ${previewRecord.device}`}
                    className="max-w-full max-h-[60vh] object-contain rounded-lg shadow-md"
                  />
                  <button
                    onClick={() => onSendToAiImage?.(buildAsset(previewRecord))}
                    className="inline-flex items-center gap-2 bg-primary hover:bg-blue-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
                  >
                    <span className="material-icons text-[18px]">auto_awesome</span>
                    发送到 AI 生图
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3 text-slate-400">
                  <span className="material-icons text-5xl">image_not_supported</span>
                  <p className="text-sm">该记录没有关联图片</p>
                </div>
              )}
            </div>

            <div className="border-t border-slate-200 dark:border-slate-700 px-6 py-3 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
              <div className="flex gap-4">
                <span>设备: <b className="text-slate-700 dark:text-slate-300">{previewRecord.device}</b></span>
                <span>时间: <b className="text-slate-700 dark:text-slate-300">{previewRecord.timestamp}</b></span>
              </div>
              {previewRecord.picUrl && (
                <a href="#" onClick={event => { event.preventDefault(); void download(previewRecord.picUrl || ""); }} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:text-blue-400 transition-colors">
                  <span className="material-icons text-[14px]">open_in_new</span>
                  新标签页打开
                </a>
              )}
            </div>
        </Modal>
      )}
    </>
  );
};

export default ResultsTable;

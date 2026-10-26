import { Menu } from 'antd';
import { SearchOutlined, PictureOutlined, ScheduleOutlined, GlobalOutlined, ExperimentOutlined, ToolOutlined, EllipsisOutlined } from '@ant-design/icons';

interface SidebarProps {
  activePage?: string;
  onPageChange?: (page: string) => void;
}

const items = [
  { key: 'detection', label: '检测查询', icon: <SearchOutlined /> },
  { key: 'ledger-query', label: '台账查询', icon: <PictureOutlined /> },
  { key: 'ledger-schedule', label: '台账下载', icon: <ScheduleOutlined /> },
  { key: 'smart-search', label: '智搜下载', icon: <GlobalOutlined /> },
  { key: 'ai-image', label: 'AI 生图', icon: <ExperimentOutlined /> },
  { key: 'tool-task', label: '工具任务', icon: <ToolOutlined /> },
];

export default function Sidebar({ activePage = 'detection', onPageChange }: SidebarProps) {
  const selected = ['ledger', 'alarm-log'].includes(activePage) ? 'detection' : activePage;
  return <nav aria-label="SmartEye 功能导航" className="smarteye-navigation">
    <Menu triggerSubMenuAction="click" overflowedIndicator={<span aria-label="更多功能"><EllipsisOutlined /></span>} mode="horizontal" selectedKeys={[selected]} items={items}
      onClick={({ key }) => onPageChange?.(key)} />
  </nav>;
}

import { Suspense, useEffect, useState } from 'react';
import { Layout, Menu, Avatar, Dropdown, Button, Grid, Drawer, Select, Spin } from 'antd';
import {
  ApiOutlined, AppstoreOutlined, AuditOutlined, CloudDownloadOutlined, CloudServerOutlined,
  CodeOutlined, DashboardOutlined, DatabaseOutlined, DownOutlined, EllipsisOutlined, ExperimentOutlined, FileImageOutlined,
  FolderOpenOutlined, HddOutlined, LogoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined,
  SearchOutlined, TagsOutlined, UserOutlined,
} from '@ant-design/icons';
import { Link, Navigate, Outlet, matchPath, useNavigate, useLocation } from 'react-router-dom';
import { logout } from '../api/auth';
import { useCurrentUser } from '../components/currentUser';
import PlatformBrand from '../ui/PlatformBrand';

const { Header, Sider, Content } = Layout;
const sections = [
  { key: '/', label: '工作概览', description: '查看审核进度、处理队列与算力状态', icon: <AppstoreOutlined /> },
  { key: '/smarteye', label: '数据拉取', description: 'SmartEye · 查询、下载与直接关联标注项目', icon: <CloudDownloadOutlined /> },
  { key: '/projects', label: '项目管理', description: '按项目组织图片、类别与数据任务', icon: <FolderOpenOutlined /> },
  { key: '/annotations', label: '标注与审核', description: '按标注项目查看图片标注、审核状态与历史版本', icon: <TagsOutlined /> },
  { key: '/models', label: '模型管理', description: '配置检测、描述与多模态模型服务，试运行后再应用标注', icon: <ApiOutlined /> },
  { key: '/model-preview', label: '模型测试与评估', description: '快速试用模型，或使用已标注数据评估检测、描述与检索效果', icon: <ExperimentOutlined /> },
  { key: '/automation', label: '预标注与AI审核', description: '按项目批量生成检测框，核验结果并处理管理员待审图片', icon: <TagsOutlined /> },
  { key: '/review-queue', label: '管理员待审队列', description: '按项目核对预标注与AI审核转交的图片，批准或驳回候选', icon: <TagsOutlined /> },
  { key: '/image-semantics/history', label: '图片库生成历史', description: '查看旧图片库生成批次；项目内描述与检索从标注准备进入', icon: <FileImageOutlined /> },
  { key: '/analytics', label: '质量与效率分析', description: '查看审核质量流程与人工操作时长，分别统计人工和自动工作量', icon: <DashboardOutlined /> },
  { key: '/validation', label: '模型验证', description: '固定人工真值，验证检测、图片描述与向量检索模型', icon: <DashboardOutlined /> },
  { key: '/training', label: '模型训练', description: '配置训练参数与数据来源，在GPU服务器运行任务并查看日志和产物', icon: <ApiOutlined /> },
  { key: '/model-repository', label: '模型仓库', description: '保存训练产物或上传模型，按格式与GPU型号管理版本并下载文件', icon: <DatabaseOutlined /> },
  { key: '/model-deployments', label: '模型与镜像更新', description: '从模型仓库选择模型，批量更新服务器模型和服务镜像', icon: <CloudDownloadOutlined /> },
  { key: '/storage-maintenance', label: '存储维护', description: '管理员核对无引用文件和共享存储保护', icon: <HddOutlined /> },
  { key: '/gpu-resources', label: 'GPU资源', description: '查看显卡、显存与计算进程，手动停止任务或释放GPU资源', icon: <DashboardOutlined /> },
  { key: '/gpu-servers', label: '服务器管理', description: '新增、批量导入和维护GPU服务器连接信息', icon: <CloudServerOutlined /> },
  { key: '/gpu-terminal', label: 'SSH调试', description: '连接GPU服务器的交互终端，排查环境和训练问题', icon: <CodeOutlined /> },
];

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const screens = Grid.useBreakpoint();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const desktop = !!screens.lg;
  const compact = desktop && collapsed;
  const user = useCurrentUser();
  const admin = user?.role === 'admin';
  const permitted = admin || ['/annotations', '/annotate/:assetId', '/annotate/project/:projectId'].some(path => matchPath(path, location.pathname));
  const projectImages = location.pathname.startsWith('/projects/');
  const templateManagement=location.pathname==='/projects/workflow-templates';
  const projectStage=templateManagement?'工作流模板':location.pathname.endsWith('/data-sources')?'项目管理':location.pathname.endsWith('/capture')?'项目数据采集':location.pathname.endsWith('/automation')?'预标注与AI审核':location.pathname.endsWith('/review')?'项目待审':location.pathname.endsWith('/attributes')?'目标属性分析':location.pathname.endsWith('/settings')?'项目设置':location.pathname.endsWith('/prepare')?'标注与审核':location.pathname.endsWith('/deliver')?'导出与训练':location.pathname.endsWith('/test')?'测试与评估':'项目图片';
  const key = projectImages ? '/projects' : location.pathname.startsWith('/annotate/') ? '/annotations'
    : ['/detection-query','/smarteye/'].includes(location.pathname) ? '/smarteye' : location.pathname;
  const section = sections.find(item => item.key === key) || sections[0];
  const workspace = location.pathname.startsWith('/annotate/');
  const vectorWorkspace = location.pathname.startsWith('/annotate/project/');
  const embedded = key === '/smarteye' || location.pathname.endsWith('/capture');
  const search = new URLSearchParams(location.search);
  const projectId = search.get('project_id');
  const annotationStage = key==='/annotations' && projectId
    ? admin && search.get('prepare')==='1' ? '批量处理' : '标注与审核'
    : section.label;
  const title=vectorWorkspace?'向量标注工作台':workspace?'图片标注工作台':projectImages?projectStage:annotationStage;
  useEffect(()=>{if(permitted)document.title=`${title} · 图像数据平台`;},[title,permitted]);
  if (!permitted) return <Navigate to="/annotations" replace />;
  const navigationGroups = [
    { label: '项目与数据', paths: ['/projects'] },
    { label: '标注与质量', paths: admin?[]:['/annotations'] },
    { label: '模型管理与评估', paths: ['/models', '/model-preview', '/model-repository', '/model-deployments'] },
    { label: '算力与运维', paths: ['/gpu-resources', '/gpu-servers', '/gpu-terminal', '/storage-maintenance'] },
  ];
  const visible = sections.filter(item => admin ? !['/training','/validation','/smarteye','/annotations','/automation','/review-queue','/image-semantics/history','/analytics'].includes(item.key) : item.key === '/annotations');
  const shortcuts=admin?[
    {value:'/projects',label:'检测切图 / 批量描述 / 建立向量索引（选择项目）'},
    {value:'/projects',label:'导出数据 / 冻结训练集 / 模型训练（选择项目）'},
    {value:'/models',label:'大模型提示词 / 检测模型 / 向量模型配置'},
    {value:'/projects',label:'图片分类 / 描述标注 / 文搜图 / 以图搜图（选择项目）'},
  ]:[];
  const searchOptions = [
    ...visible.map(item => ({ value: item.key, path: item.key, label: item.label, description: item.description })),
    ...shortcuts.map((item, index) => ({
      value: `shortcut-${index}`, path: item.value, label: item.label,
      description: item.value === '/projects' ? '项目管理' : '模型管理',
    })),
  ];
  const related:Record<string,string[]>={
    '/projects':['/annotations'],
    '/annotations':['/projects','/models','/automation','/review-queue'], '/automation':['/annotations','/review-queue','/models'],
    '/review-queue':['/annotations','/automation'],
    '/models':['/model-preview','/annotations','/automation'], '/training':['/projects','/model-repository','/model-preview','/validation','/gpu-resources'],
    '/model-repository':['/training','/model-deployments'],
    '/model-preview':['/models','/validation'], '/validation':['/model-preview','/training','/annotations'], '/analytics':['/annotations','/validation'],
    '/gpu-resources':['/gpu-servers','/training','/gpu-terminal'], '/gpu-servers':['/gpu-resources','/gpu-terminal'], '/gpu-terminal':['/gpu-resources','/gpu-servers','/training'],
    '/model-deployments':['/model-repository','/gpu-resources'],
    '/storage-maintenance':['/projects'], '/smarteye':['/projects'],
    '/image-semantics/history':['/annotations'],
  };
  const menuItem = ({ key: path, label, icon }: typeof sections[number]) => ({ key: path, label, icon, 'aria-label': label });
  const navigation = <nav className="platform-navigation" id={desktop ? 'platform-navigation' : 'mobile-navigation'} aria-label="平台主导航">
    <Menu mode="inline" selectedKeys={[key==='/image-semantics/history'?'/annotations':key]} inlineCollapsed={compact} items={[
      ...(admin ? [menuItem(sections[0])] : []),
      ...navigationGroups.map(group => ({ type: 'group' as const, key: group.label, label: compact ? null : group.label,
        children: group.paths.flatMap(path => visible.filter(item => item.key === path).map(menuItem)) })).filter(group => group.children.length),
    ]} onClick={({ key: path }) => { setMobileOpen(false); navigate(path); }} />
  </nav>;
  const relatedItems = workspace || projectImages || projectId ? [] :
    (related[key] || []).flatMap(path => visible.filter(item => item.key === path));

  return <Layout className={`platform-shell${workspace ? ' platform-shell-workspace' : ''}`}>
    {desktop ? <Sider width={216} collapsedWidth={64} collapsed={compact} theme="light" className="platform-sidebar">
      <PlatformBrand compact={compact} />{navigation}
    </Sider> : <Drawer title="平台导航" placement="left" size={260} open={mobileOpen} onClose={() => setMobileOpen(false)} styles={{ body: { padding: 0 } }}>
      {navigation}
    </Drawer>}
    <Layout className="platform-main" style={{ marginLeft: desktop ? compact ? 64 : 216 : 0 }}>
      <Header className="platform-header">
        <div className="platform-header-start">
          <Button type="text" aria-label={(desktop ? compact : !mobileOpen) ? '展开导航' : '收起导航'}
            aria-expanded={desktop ? !compact : mobileOpen} aria-controls={desktop ? 'platform-navigation' : 'mobile-navigation'}
            icon={compact || !desktop ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => desktop ? setCollapsed(!collapsed) : setMobileOpen(!mobileOpen)} />
          <div className="page-heading">
            <h1>{title}</h1>
          </div>
        </div>
        <div className="platform-header-actions">
          {key === '/' && <Link className="platform-review-link" to="/annotations" aria-label="继续审核" title="继续审核">
            <AuditOutlined aria-hidden /><span>继续审核</span>
          </Link>}
          {relatedItems.length > 0 && <Dropdown trigger={['click']} menu={{ items: relatedItems.map(item => ({
            key: item.key, icon: item.icon, label: <Link to={item.key}>{item.label}</Link>,
          })) }}>
            <Button type="text" aria-label="相关功能" icon={<EllipsisOutlined />}>{screens.md ? '相关功能' : null}</Button>
          </Dropdown>}
          <Select<string, (typeof searchOptions)[number]> className="platform-feature-search" aria-label="搜索平台功能" value={null}
            prefix={<SearchOutlined aria-hidden />} suffixIcon={null} placeholder={screens.sm ? admin ? '搜索平台功能' : '搜索标注功能' : '搜索'}
            showSearch={{ optionFilterProp: ['label', 'description', 'value'] }}
            notFoundContent="没有找到功能，请换个关键词" options={searchOptions}
            popupMatchSelectWidth={desktop ? 370 : 250} virtual={false}
            optionRender={({ data }) => <span className="platform-search-result"><strong>{data.label}</strong><small>{data.description}</small></span>}
            onSelect={(_, option) => navigate(option.path)} />
          <Dropdown trigger={['click']} menu={{ items: [
            { key: 'identity', label: user?.username || '用户', disabled: true },
            { type: 'divider' },
            { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, onClick: logout },
          ] }}>
            <Button type="text" className="platform-user" aria-label="账户菜单">
              <Avatar size={28} icon={<UserOutlined />} />
              <span className="platform-user-name">{user?.username || '用户'}</span><DownOutlined className="platform-user-chevron" aria-hidden />
            </Button>
          </Dropdown>
        </div>
      </Header>
      <Content className={['platform-content', embedded && 'platform-content-embedded', workspace && 'platform-content-workspace', key === '/' && 'platform-content-dashboard'].filter(Boolean).join(' ')}>
        <Suspense fallback={<div role="status"><Spin /> 正在加载页面…</div>}><Outlet /></Suspense>
      </Content>
    </Layout>
  </Layout>;
}

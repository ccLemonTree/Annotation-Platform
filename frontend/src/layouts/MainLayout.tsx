import { useEffect, useState } from 'react';
import { Layout, Menu, Avatar, Dropdown, Button, Grid, Drawer, Select } from 'antd';
import { ProjectOutlined, FileImageOutlined, TagsOutlined, CloudDownloadOutlined, DashboardOutlined, UserOutlined, LogoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined, ApiOutlined, DatabaseOutlined } from '@ant-design/icons';
import { Link, Navigate, Outlet, matchPath, useNavigate, useLocation } from 'react-router-dom';
import { logout } from '../api/auth';
import { useCurrentUser } from '../components/currentUser';

const { Header, Sider, Content } = Layout;
const sections = [
  { key: '/', label: '工作概览', description: '查看数据进度，继续最近的工作', icon: <DashboardOutlined /> },
  { key: '/smarteye', label: '数据拉取', description: 'SmartEye · 查询、下载与直接关联标注项目', icon: <CloudDownloadOutlined /> },
  { key: '/projects', label: '项目管理', description: '按项目组织图片、类别与数据任务', icon: <ProjectOutlined /> },
  { key: '/assets', label: '图片管理', description: '上传、拉取并筛选图片，再添加到标注项目', icon: <FileImageOutlined /> },
  { key: '/annotations', label: '标注与审核', description: '按标注项目查看图片标注、审核状态与历史版本', icon: <TagsOutlined /> },
  { key: '/models', label: '模型管理', description: '配置检测、描述与多模态模型服务，试运行后再应用标注', icon: <ApiOutlined /> },
  { key: '/model-preview', label: '模型效果预览', description: '上传图片，查看目标检测、图片描述或文搜图与图搜图效果', icon: <FileImageOutlined /> },
  { key: '/automation', label: '预标注与AI审核', description: '按项目批量生成检测框，核验结果并处理管理员待审图片', icon: <TagsOutlined /> },
  { key: '/image-semantics/history', label: '图片库生成历史', description: '查看旧图片库生成批次；项目内描述与检索从标注准备进入', icon: <FileImageOutlined /> },
  { key: '/analytics', label: '质量与效率分析', description: '查看审核质量流程与人工操作时长，分别统计人工和自动工作量', icon: <DashboardOutlined /> },
  { key: '/validation', label: '模型验证', description: '固定人工真值，验证检测、图片描述与向量检索模型', icon: <DashboardOutlined /> },
  { key: '/training', label: '模型训练', description: '配置训练参数与数据来源，在GPU服务器运行任务并查看日志和产物', icon: <ApiOutlined /> },
  { key: '/model-deployments', label: '模型下发', description: '统一保存模型版本，批量下发到服务器并更新推理和业务服务镜像', icon: <CloudDownloadOutlined /> },
  { key: '/storage-maintenance', label: '存储维护', description: '管理员核对无引用文件和共享存储保护', icon: <DatabaseOutlined /> },
  { key: '/gpu-resources', label: 'GPU资源', description: '查看显卡、显存与计算进程，手动停止任务或释放GPU资源', icon: <DashboardOutlined /> },
  { key: '/gpu-terminal', label: 'SSH调试', description: '连接GPU服务器的交互终端，排查环境和训练问题', icon: <ApiOutlined /> },
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
  const projectStage=location.pathname.endsWith('/prepare')?'标注准备':location.pathname.endsWith('/deliver')?'导出与训练':'项目图片';
  const key = projectImages ? '/projects' : location.pathname.startsWith('/annotate/') ? '/annotations'
    : location.pathname === '/detection-query' ? '/smarteye' : location.pathname;
  const section = sections.find(item => item.key === key) || sections[0];
  const workspace = location.pathname.startsWith('/annotate/');
  const vectorWorkspace = location.pathname.startsWith('/annotate/project/');
  const embedded = key === '/smarteye';
  const title=vectorWorkspace?'向量标注工作台':workspace?'图片标注工作台':projectImages?projectStage:section.label;
  useEffect(()=>{if(permitted)document.title=`${title} · 图像数据平台`;},[title,permitted]);
  if (!permitted) return <Navigate to="/annotations" replace />;
  const navigationGroups = [
    { label: '数据资产', paths: ['/smarteye', '/projects', '/assets'] },
    { label: '标注与质量', paths: ['/annotations', '/automation', '/analytics', '/validation'] },
    { label: '模型与算力', paths: ['/models', '/model-preview', '/training', '/model-deployments', '/gpu-resources', '/gpu-terminal', '/storage-maintenance'] },
  ];
  const visible = sections.filter(item => admin || item.key === '/annotations');
  const shortcuts=admin?[
    {value:'/annotations',label:'检测切图 / 批量描述 / 建立向量索引（选择项目）'},
    {value:'/projects',label:'导出数据 / 冻结训练集（选择项目）'},
    {value:'/models',label:'大模型提示词 / 检测模型 / 向量模型配置'},
    {value:'/annotations',label:'图片分类 / 描述标注 / 文搜图 / 以图搜图（选择项目）'},
  ]:[];
  const related:Record<string,string[]>={
    '/':['/assets','/projects','/annotations','/training','/validation'],
    '/assets':['/projects','/smarteye'], '/projects':['/assets','/annotations'],
    '/annotations':['/projects','/models','/automation'], '/automation':['/annotations','/models'],
    '/models':['/model-preview','/annotations','/automation'], '/training':['/projects','/model-preview','/validation','/gpu-resources'],
    '/model-preview':['/models','/validation'], '/validation':['/model-preview','/training','/annotations'], '/analytics':['/annotations','/validation'],
    '/gpu-resources':['/training','/gpu-terminal'], '/gpu-terminal':['/gpu-resources','/training'],
    '/model-deployments':['/training','/gpu-resources'],
    '/storage-maintenance':['/assets','/projects'], '/smarteye':['/assets','/projects'],
    '/image-semantics/history':['/annotations','/assets'],
  };
  const menuItem = ({ key: path, label, icon }: typeof sections[number]) => ({ key: path, label, icon, 'aria-label': label });
  const navigation = <nav id={desktop ? 'platform-navigation' : 'mobile-navigation'} aria-label="平台主导航">
    <Menu mode="inline" selectedKeys={[key==='/image-semantics/history'?'/annotations':key]} inlineCollapsed={compact} items={[
      ...(admin ? [menuItem(sections[0])] : []),
      ...navigationGroups.map(group => ({ type: 'group' as const, key: group.label, label: compact ? null : group.label,
        children: group.paths.flatMap(path => visible.filter(item => item.key === path).map(menuItem)) })).filter(group => group.children.length),
    ]} onClick={({ key: path }) => { setMobileOpen(false); navigate(path); }} />
  </nav>;
  const brand = <div className="platform-brand" title="图像数据平台">
    <span className="platform-brand-icon"><FileImageOutlined /></span>
    {!compact && <span>图像数据平台<small>数据 · 标注 · 迭代</small></span>}
  </div>;

  return <Layout className={`platform-shell${workspace ? ' platform-shell-workspace' : ''}`}>
    {desktop ? <Sider width={216} collapsedWidth={64} collapsed={compact} theme="light" className="platform-sidebar">
      {brand}{navigation}
      {!compact && <div className="platform-sidebar-note">{admin ? <>统一管理图片与标注<br />从数据拉取到数据集导出</> : '选择项目，完成标注与审核'}</div>}
    </Sider> : <Drawer title="平台导航" placement="left" size={260} open={mobileOpen} onClose={() => setMobileOpen(false)} styles={{ body: { padding: 0 } }}>
      {brand}{navigation}
    </Drawer>}
    <Layout className="platform-main" style={{ marginLeft: desktop ? compact ? 64 : 216 : 0 }}>
      <Header className="platform-header">
        <div className="platform-header-start">
          <Button type="text" aria-label={(desktop ? compact : !mobileOpen) ? '展开导航' : '收起导航'}
            aria-expanded={desktop ? !compact : mobileOpen} aria-controls={desktop ? 'platform-navigation' : 'mobile-navigation'}
            icon={compact || !desktop ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => desktop ? setCollapsed(!collapsed) : setMobileOpen(!mobileOpen)} />
          <span className="platform-header-label">{workspace ? '项目标注工作空间' : '视觉数据工作空间'}</span>
        </div>
        <div className="platform-header-actions">
        <Select<string> className="platform-feature-search" aria-label="搜索平台功能" showSearch value={undefined}
          placeholder={admin ? '搜索功能：切图、导出、训练…' : '搜索标注功能…'} optionFilterProp="label" notFoundContent="没有找到功能，请换个关键词"
          options={[...visible.map(item=>({value:item.key,label:`${item.label} · ${item.description}`})),...shortcuts.map((item,i)=>({...item,value:`shortcut-${i}`}))]}
          onSelect={path=>navigate(path.startsWith('shortcut-')?shortcuts[Number(path.slice(9))].value:path)}/>
        <Dropdown trigger={['click']} menu={{ items: [
          { key: 'identity', label: user?.username || '用户', disabled: true },
          { type: 'divider' },
          { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, onClick: logout },
        ] }}>
          <Button type="text" className="platform-user" aria-label="账户菜单">
            <Avatar size={28} icon={<UserOutlined />} />
            <span>{user?.username || '用户'}</span>
          </Button>
        </Dropdown>
        </div>
      </Header>
      <Content className={`platform-content ${embedded ? 'platform-content-embedded' : ''} ${workspace ? 'platform-content-workspace' : ''} ${key === '/' ? 'platform-content-dashboard' : ''}`}>
        <div className="page-heading">
          <div>
            <h1>{title}</h1>
            <p>{vectorWorkspace?'编辑图片描述，或用文字、图片检索并标注匹配结果':workspace ? '标注检测框、类别或描述，保存版本并完成审核' : projectImages ? '图片、准备、标注、审核与训练，在项目内连续完成' : section.description}</p>
          </div>
          {key === '/' && <Link className="dashboard-primary-link dashboard-heading-action" to="/annotations">继续审核 ↗</Link>}
        </div>
        {!workspace&&!projectImages&&<nav className="platform-related" aria-label="相关功能">{(related[key]||[]).flatMap(path=>visible.filter(item=>item.key===path).map(item=><Link key={path} to={path}>{item.label} →</Link>))}</nav>}
        <Outlet />
      </Content>
    </Layout>
  </Layout>;
}

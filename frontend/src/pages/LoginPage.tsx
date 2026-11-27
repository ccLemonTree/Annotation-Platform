import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Tabs } from 'antd';
import { LockOutlined, MailOutlined, UserOutlined } from '@ant-design/icons';
import { isAxiosError } from 'axios';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getCurrentUser, getPublicAuthConfig, login, register } from '../api/auth';
import type { LoginRequest, RegisterRequest } from '../api/auth';
import { appMessage } from '../ui/appFeedback';
import { authWindow, currentReturnPath, safeReturnPath } from '../utils/authNavigation';
import PlatformBrand from '../ui/PlatformBrand';
import warehouseExample from '../assets/warehouse-forklift.webp';
import './LoginPage.css';

export default function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [loading, setLoading] = useState(false);
  const [allowRegister, setAllowRegister] = useState(false);
  const [activeTab, setActiveTab] = useState('login');
  const [errorMessage, setErrorMessage] = useState('');
  const registering = allowRegister && activeTab === 'register';

  useEffect(() => {
    document.title = `${registering ? '创建账户' : '登录工作台'} · 图像数据平台`;
  }, [registering]);

  useEffect(() => {
    let mounted = true;
    getPublicAuthConfig().then(config => {
      if (!mounted) return;
      setAllowRegister(config.allow_public_register === true);
      if (!config.allow_public_register) setActiveTab('login');
    });
    return () => {
      mounted = false;
    };
  }, []);

  const handleLogin = async (values: LoginRequest) => {
    try {
      setLoading(true);
      setErrorMessage('');
      const response = await login(values);

      localStorage.setItem('token', response.access_token);
      const user = await getCurrentUser();
      localStorage.setItem('user', JSON.stringify(user));

      appMessage.success('登录成功！');
      const target = authWindow();
      const next = user.role === 'admin'
        ? safeReturnPath(params.get('next') || (target !== window ? currentReturnPath() : '/'))
        : '/annotations';
      if (target !== window) target.location.replace(next);
      else navigate(next, { replace: true });
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : undefined;
      setErrorMessage(typeof detail === 'string' ? detail : '登录失败，请检查用户名和密码');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (values: RegisterRequest) => {
    try {
      setLoading(true);
      setErrorMessage('');
      await register(values);
      appMessage.success('注册成功！请登录');
      setActiveTab('login');
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : undefined;
      setErrorMessage(typeof detail === 'string' ? detail : '注册失败，请检查填写的信息');
    } finally {
      setLoading(false);
    }
  };

  const loginForm = (
    <Form name="login" onFinish={values => void handleLogin(values)} layout="vertical" size="large">
      <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
        <Input prefix={<UserOutlined />} autoComplete="username" />
      </Form.Item>

      <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
        <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
      </Form.Item>

      <Form.Item style={{ marginBottom: 0 }}>
        <Button type="primary" htmlType="submit" loading={loading} block>
          登录
        </Button>
      </Form.Item>
    </Form>
  );

  const registerForm = (
    <Form name="register" onFinish={values => void handleRegister(values)} layout="vertical" size="large">
      <Form.Item
        name="username"
        label="用户名"
        rules={[
          { required: true, message: '请输入用户名' },
          { min: 3, message: '用户名至少3个字符' },
        ]}
      >
        <Input prefix={<UserOutlined />} placeholder="至少 3 个字符" autoComplete="username" />
      </Form.Item>

      <Form.Item
        name="email"
        label="邮箱（可选）"
        rules={[{ type: 'email', message: '请输入有效的邮箱' }]}
      >
        <Input prefix={<MailOutlined />} autoComplete="email" />
      </Form.Item>

      <Form.Item
        name="password"
        label="密码"
        rules={[
          { required: true, message: '请输入密码' },
          { min: 6, message: '密码至少6个字符' },
        ]}
      >
        <Input.Password prefix={<LockOutlined />} placeholder="至少 6 个字符" autoComplete="new-password" />
      </Form.Item>

      <Form.Item style={{ marginBottom: 0 }}>
        <Button type="primary" htmlType="submit" loading={loading} block>
          注册
        </Button>
      </Form.Item>
    </Form>
  );

  return (
    <div className="login-screen">
      <header className="login-masthead"><PlatformBrand /></header>
      <main className="login-layout">
        <section className="login-card" aria-labelledby="login-title">
          <h1 id="login-title">{registering ? '创建账户' : '登录工作台'}</h1>
          {errorMessage && <Alert type="error" showIcon title={errorMessage} style={{ marginBottom: 16 }} />}
          {allowRegister ? (
            <Tabs
              activeKey={activeTab}
              onChange={key => {
                setActiveTab(key);
                setErrorMessage('');
              }}
              items={[
                { key: 'login', label: '登录', children: loginForm },
                { key: 'register', label: '注册', children: registerForm },
              ]}
            />
          ) : (
            loginForm
          )}
        </section>
        <figure className="login-example">
          <div className="login-example-image">
            <img src={warehouseExample} width="1200" height="800" alt="标注框圈出了仓储场景中的一辆叉车" />
            <div className="login-example-box" aria-hidden="true"><span>叉车</span></div>
          </div>
        </figure>
      </main>
    </div>
  );
}

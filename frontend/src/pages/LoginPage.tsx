// 登录页面
import { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, Tabs } from 'antd';
import { LockOutlined, MailOutlined, UserOutlined } from '@ant-design/icons';
import { isAxiosError } from 'axios';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getCurrentUser, getPublicAuthConfig, login, register } from '../api/auth';
import type { LoginRequest, RegisterRequest } from '../api/auth';
import { appMessage } from '../ui/appFeedback';
import { authWindow, currentReturnPath, safeReturnPath } from '../utils/authNavigation';

export default function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [loading, setLoading] = useState(false);
  const [allowRegister, setAllowRegister] = useState(false);
  const [activeTab, setActiveTab] = useState('login');
  const [errorMessage, setErrorMessage] = useState('');

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
        <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
      </Form.Item>

      <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
        <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
      </Form.Item>

      <Form.Item style={{ marginBottom: 0 }}>
        <Button type="primary" htmlType="submit" loading={loading} block style={{ height: 40 }}>
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
        <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
      </Form.Item>

      <Form.Item
        name="email"
        label="邮箱（可选）"
        rules={[{ type: 'email', message: '请输入有效的邮箱' }]}
      >
        <Input prefix={<MailOutlined />} placeholder="邮箱（可选）" autoComplete="email" />
      </Form.Item>

      <Form.Item
        name="password"
        label="密码"
        rules={[
          { required: true, message: '请输入密码' },
          { min: 6, message: '密码至少6个字符' },
        ]}
      >
        <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="new-password" />
      </Form.Item>

      <Form.Item style={{ marginBottom: 0 }}>
        <Button type="primary" htmlType="submit" loading={loading} block style={{ height: 40 }}>
          注册
        </Button>
      </Form.Item>
    </Form>
  );

  return (
    <div className="login-screen">
      <section className="login-intro">
        <span className="platform-brand-icon"><UserOutlined /></span>
        <h1>图像数据平台</h1>
        <p>从图片拉取到标注审核，在同一个工作台中整理数据，构建可追溯的数据集。</p>
        <div className="login-steps"><span>01 数据拉取</span><span>02 图片管理</span><span>03 标注审核</span></div>
      </section>
      <Card className="login-card">
        <h2>{allowRegister && activeTab === 'register' ? '创建账户' : '登录工作台'}</h2>
        <p className="login-caption">使用平台账户继续你的工作</p>
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
      </Card>
    </div>
  );
}

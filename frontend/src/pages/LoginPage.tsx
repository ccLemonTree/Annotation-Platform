// 登录页面
import { useState } from 'react';
import { Form, Input, Button, Card, message, Tabs, Alert } from 'antd';
import { UserOutlined, LockOutlined, MailOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { login, register, getCurrentUser } from '../api/auth';
import type { LoginRequest, RegisterRequest } from '../api/auth';

export default function LoginPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('login');
  const [notice, noticeContext] = message.useMessage();
  const [errorMessage, setErrorMessage] = useState('');

  const handleLogin = async (values: LoginRequest) => {
    try {
      setLoading(true); setErrorMessage('');
      const response = await login(values);

      // 保存token
      localStorage.setItem('token', response.access_token);

      // 获取用户信息
      const user = await getCurrentUser();
      localStorage.setItem('user', JSON.stringify(user));

      notice.success('登录成功！');
      navigate(user.role === 'admin' ? '/' : '/annotations');
    } catch (error: any) {
      setErrorMessage(typeof error.response?.data?.detail === 'string' ? error.response.data.detail : '登录失败，请检查用户名和密码');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (values: RegisterRequest) => {
    try {
      setLoading(true); setErrorMessage('');
      await register(values);
      notice.success('注册成功！请登录');
      setActiveTab('login');
    } catch (error: any) {
      setErrorMessage(typeof error.response?.data?.detail === 'string' ? error.response.data.detail : '注册失败，请检查填写的信息');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-screen">
      {noticeContext}
      <section className="login-intro">
        <span className="platform-brand-icon"><UserOutlined /></span>
        <h1>图像数据平台</h1>
        <p>从图片拉取到标注审核，在同一个工作台中整理数据，构建可追溯的数据集。</p>
        <div className="login-steps"><span>01 数据拉取</span><span>02 图片管理</span><span>03 标注审核</span></div>
      </section>
      <Card className="login-card">
        <h2>{activeTab === 'login' ? '登录工作台' : '创建账户'}</h2>
        <p className="login-caption">使用平台账户继续你的工作</p>
        {errorMessage && <Alert type="error" showIcon title={errorMessage} style={{ marginBottom: 16 }} />}
        <Tabs
          activeKey={activeTab}
          onChange={key => { setActiveTab(key); setErrorMessage(''); }}
          items={[
            {
              key: 'login',
              label: '登录',
              children: (
                <Form
                  name="login"
                  onFinish={handleLogin}
                  layout="vertical"
                  size="large"
                >
                  <Form.Item
                    name="username"
                    label="用户名"
                    rules={[{ required: true, message: '请输入用户名' }]}
                  >
                    <Input
                      prefix={<UserOutlined />}
                      placeholder="用户名"
                      autoComplete="username"
                    />
                  </Form.Item>

                  <Form.Item
                    name="password"
                    label="密码"
                    rules={[{ required: true, message: '请输入密码' }]}
                  >
                    <Input.Password
                      prefix={<LockOutlined />}
                      placeholder="密码"
                      autoComplete="current-password"
                    />
                  </Form.Item>

                  <Form.Item>
                    <Button
                      type="primary"
                      htmlType="submit"
                      loading={loading}
                      block
                      style={{ height: 44 }}
                    >
                      登录
                    </Button>
                  </Form.Item>


                </Form>
              ),
            },
            {
              key: 'register',
              label: '注册',
              children: (
                <Form
                  name="register"
                  onFinish={handleRegister}
                  layout="vertical"
                  size="large"
                >
                  <Form.Item
                    name="username"
                    label="用户名"
                    rules={[
                      { required: true, message: '请输入用户名' },
                      { min: 3, message: '用户名至少3个字符' },
                    ]}
                  >
                    <Input
                      prefix={<UserOutlined />}
                      placeholder="用户名"
                      autoComplete="username"
                    />
                  </Form.Item>

                  <Form.Item
                    name="email"
                    label="邮箱（可选）"
                    rules={[
                      { type: 'email', message: '请输入有效的邮箱' },
                    ]}
                  >
                    <Input
                      prefix={<MailOutlined />}
                      placeholder="邮箱（可选）"
                      autoComplete="email"
                    />
                  </Form.Item>

                  <Form.Item
                    name="password"
                    label="密码"
                    rules={[
                      { required: true, message: '请输入密码' },
                      { min: 6, message: '密码至少6个字符' },
                    ]}
                  >
                    <Input.Password
                      prefix={<LockOutlined />}
                      placeholder="密码"
                      autoComplete="new-password"
                    />
                  </Form.Item>

                  <Form.Item>
                    <Button
                      type="primary"
                      htmlType="submit"
                      loading={loading}
                      block
                      style={{ height: 44 }}
                    >
                      注册
                    </Button>
                  </Form.Item>
                </Form>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}

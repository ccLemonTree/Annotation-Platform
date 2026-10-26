import { useEffect, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { Navigate } from 'react-router-dom';
import { getCurrentUser, logout, type User } from '../api/auth';
import { CurrentUser } from './currentUser';

export default function PrivateRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('token');
  const [user, setUser] = useState<User>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!token) return;
    let active = true;
    getCurrentUser().then(current => {
      if (!active) return;
      localStorage.setItem('user', JSON.stringify(current));
      setUser(current);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [token]);
  if (!token) return <Navigate to="/login" replace />;
  if (failed) return <Alert type="error" title="读取账户信息失败，请重新登录" action={<Button onClick={logout}>重新登录</Button>} />;
  if (!user) return <Spin />;
  return <CurrentUser.Provider value={user}>{children}</CurrentUser.Provider>;
}

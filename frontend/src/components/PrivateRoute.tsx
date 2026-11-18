import { useEffect, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { Navigate } from 'react-router-dom';
import { getCurrentUser, logout, type User } from '../api/auth';
import { CurrentUser } from './currentUser';
import { authWindow, loginUrl, redirectToLogin } from '../utils/authNavigation';

export default function PrivateRoute({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'));
  const [user, setUser] = useState<User>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.storageArea !== localStorage) return;
      if (event.key === 'token' || event.key === null) {
        const nextToken = localStorage.getItem('token');
        setUser(undefined);
        setFailed(false);
        setToken(nextToken);
      } else if (event.key === 'user' && event.newValue) {
        try {
          const parsed = JSON.parse(event.newValue) as User;
          if (parsed && typeof parsed.id === 'number') setUser(parsed);
        } catch {
          // Ignore malformed user cache; getCurrentUser remains authoritative.
        }
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  useEffect(() => {
    if (!token) {
      if (authWindow() !== window) redirectToLogin();
      return;
    }
    let active = true;
    setFailed(false);
    getCurrentUser()
      .then(current => {
        if (!active) return;
        localStorage.setItem('user', JSON.stringify(current));
        setUser(current);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [token]);

  if (!token) return <Navigate to={loginUrl()} replace />;
  if (failed) {
    return (
      <Alert
        type="error"
        showIcon
        title="读取账户信息失败，请重新登录"
        action={<Button onClick={logout}>重新登录</Button>}
      />
    );
  }
  if (!user) return <Spin />;
  return <CurrentUser.Provider value={user}>{children}</CurrentUser.Provider>;
}

import React, { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '@/src/shared/api/client';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = await apiClient<{ access_token: string }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      sessionStorage.setItem('eecp_access_token', result.access_token);
      navigate('/', { replace: true });
    } catch (reason: any) {
      setError(reason.message || 'Đăng nhập thất bại');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen grid place-items-center bg-background p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 border border-border bg-surface p-6 rounded-sm">
        <h1 className="text-xl font-bold">EECP Examiner</h1>
        <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Tên đăng nhập" required />
        <Input value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Mật khẩu" type="password" required />
        {error && <p className="text-sm text-error">{error}</p>}
        <Button type="submit" isLoading={loading} className="w-full">Đăng nhập</Button>
      </form>
    </main>
  );
};

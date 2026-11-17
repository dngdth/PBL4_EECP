import React, { FormEvent, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Eye, EyeOff, Shield } from 'lucide-react';
import { useAuth } from '@/src/features/auth';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';

const formatLoginError = (raw?: string): string => {
  const normalized = (raw || '').trim().toLowerCase();
  if (!normalized || normalized.includes('invalid credentials') || normalized.includes('invalid username or password')) {
    return 'Tên đăng nhập hoặc mật khẩu không chính xác.';
  }
  if (normalized.includes('locked')) {
    return 'Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên.';
  }
  if (normalized.includes('failed to fetch') || normalized.includes('network')) {
    return 'Không thể kết nối tới máy chủ. Vui lòng kiểm tra lại hệ thống.';
  }
  return raw || 'Đăng nhập thất bại. Vui lòng thử lại.';
};

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const { loginWithCredentials, isAuthenticated, authEnforced } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated && authEnforced) {
      navigate('/', { replace: true });
    }
  }, [isAuthenticated, authEnforced, navigate]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await loginWithCredentials(username.trim(), password);
      navigate('/', { replace: true });
    } catch (reason: any) {
      setError(formatLoginError(reason?.message));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen grid place-items-center bg-background p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 border border-border bg-surface p-6 rounded shadow-2xs"
      >
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-sm bg-primary text-surface flex items-center justify-center">
            <Shield className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-text leading-tight">EECP Exam Control</h1>
            <p className="text-xs text-text-muted">Đăng nhập Quản trị viên hoặc Giám thị</p>
          </div>
        </div>
        <Input
          label="Tên đăng nhập"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Nhập tên đăng nhập"
          autoComplete="username"
          required
        />
        <div className="relative">
          <Input
            label="Mật khẩu"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Nhập mật khẩu"
            type={showPassword ? 'text' : 'password'}
            autoComplete="current-password"
            className="pr-10"
            required
          />
          <button
            type="button"
            onClick={() => setShowPassword((prev) => !prev)}
            className="absolute right-2.5 top-[31px] text-text-muted hover:text-text p-1 rounded transition-colors"
            title={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
            aria-label={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
          >
            {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        {error && <p className="text-sm text-error font-medium">{error}</p>}
        <Button
          type="submit"
          variant="primary"
          isLoading={loading}
          className="w-full font-bold bg-primary hover:bg-primary-dark text-surface"
        >
          Đăng nhập
        </Button>
      </form>
    </main>
  );
};

'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Eye, EyeOff } from 'lucide-react';
import { isAuthenticated, login } from '@/lib/auth';
import { ApiError } from '@/lib/api/client';

const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';

export default function LoginPage() {
  const router = useRouter();
  const [role, setRole] = useState<'admin' | 'user'>('admin');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [slowLogin, setSlowLogin] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Stays false until we've confirmed the user is NOT authenticated.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (isAuthenticated()) {
      router.replace('/new');
    } else {
      setReady(true);
    }
  }, [router]);

  useEffect(() => {
    return () => {
      if (slowTimerRef.current) clearTimeout(slowTimerRef.current);
    };
  }, []);

  if (!ready) return null;

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password || loading) return;
    setLoading(true);
    setError('');
    setSlowLogin(false);

    slowTimerRef.current = setTimeout(() => setSlowLogin(true), 6000);

    try {
      await login(username.trim(), password, role);
      router.replace('/new');
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError('Invalid username or password.');
        } else if (err.status === 429) {
          setError('Too many login attempts. Please wait a moment and try again.');
        } else {
          setError('Login failed. Please try again.');
        }
      } else {
        setError('Network error. Please check your connection and try again.');
      }
    } finally {
      if (slowTimerRef.current) clearTimeout(slowTimerRef.current);
      setSlowLogin(false);
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <Card className="w-full max-w-xs animate-[fade-up_0.5s_ease-out]">
        <CardContent>
          <div className="text-center space-y-8">
            <div className="flex justify-center">
              <Image
                src={`${basePath}/milestone-logo-black.png`}
                alt="Milestone"
                width={200}
                height={113}
                priority
                style={{ height: 'auto' }}
                className="dark:hidden"
              />
              <Image
                src={`${basePath}/milestone-logo-white.png`}
                alt="Milestone"
                width={200}
                height={113}
                priority
                style={{ height: 'auto' }}
                className="hidden dark:block"
              />
            </div>

            <div className="space-y-2">
              <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                Welcome to MTI Brain
              </h1>
              <p className="text-muted-foreground text-sm">
                Your treasury intelligence, in a single question.
              </p>
            </div>

            <form onSubmit={handlePasswordLogin} className="space-y-3 text-left">
              <div className="space-y-1">
                <Label htmlFor="role">Role</Label>
                <Select
                  value={role}
                  onValueChange={(value: 'admin' | 'user') => {
                    setRole(value);
                    setError('');
                  }}
                >
                  <SelectTrigger
                    id="role"
                    className="h-10 w-full rounded-xl"
                  >
                    <SelectValue placeholder="Select role" />
                  </SelectTrigger>
                  <SelectContent className="rounded-xl p-1 shadow-md">
                    <SelectItem value="admin" className="rounded-lg px-3 py-1.5 focus:bg-accent/70 data-[state=checked]:bg-accent data-[state=checked]:text-accent-foreground">
                      Admin
                    </SelectItem>
                    <SelectItem value="user" className="rounded-lg px-3 py-1.5 focus:bg-accent/70 data-[state=checked]:bg-accent data-[state=checked]:text-accent-foreground">
                      User
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => { setUsername(e.target.value); setError(''); }}
                  placeholder="Enter username or email"
                  className="h-10 rounded-xl"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => { setPassword(e.target.value); setError(''); }}
                    placeholder="Enter password"
                    className="h-10 rounded-xl pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                    tabIndex={-1}
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>
              {error && <p className="text-destructive text-xs">{error}</p>}
              <Button
                type="submit"
                disabled={loading || !username.trim() || !password}
                className="w-full h-11 rounded-xl text-sm font-medium"
              >
                {loading ? 'Signing in…' : 'Sign in'}
              </Button>
              {slowLogin && (
                <p className="text-muted-foreground text-xs text-center">
                  Still connecting — the server may be waking up…
                </p>
              )}
            </form>

            <div className="relative flex items-center gap-3">
              <div className="flex-1 h-px bg-border" />
              <span className="text-xs text-muted-foreground">or</span>
              <div className="flex-1 h-px bg-border" />
            </div>

            <Button
              type="button"
              variant="outline"
              className="w-full h-11 rounded-xl text-sm font-medium flex items-center gap-2"
              onClick={() => {}}
            >
              <Image src={`${basePath}/MSFT.png`} alt="Microsoft" width={18} height={18} priority style={{ height: 'auto' }} />
              Sign in with Microsoft
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

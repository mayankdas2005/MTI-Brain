'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Eye, EyeOff } from 'lucide-react';
import { isAuthenticated, login, setLoginGate, setLoginError, consumeLoginError } from '@/lib/auth';
import { ApiError } from '@/lib/api/client';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  // Stays false until we've confirmed the user is NOT authenticated.
  // Prevents a one-frame flash of the login form for users who are already
  // signed in - they'll see nothing, then be redirected to /new.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Show any error surfaced from a failed optimistic login attempt.
    const pendingError = consumeLoginError();
    if (pendingError) setError(pendingError);

    if (isAuthenticated()) {
      router.replace('/new');
      // Don't setReady - the page is about to unmount.
    } else {
      setReady(true);
    }
  }, [router]);

  if (!ready) return null;

  const handlePasswordLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || loading) return;
    setLoading(true);
    setError('');

    // Fire the API call in the background and navigate immediately.
    // The authenticated layout's spinner covers the in-flight token fetch.
    // On failure, the gate's catch stores the error; the login page reads
    // it back via consumeLoginError() when it remounts.
    const gate = login(username.trim(), password)
      .then(() => {})
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          if (err.status === 401) {
            setLoginError('Invalid username or password.');
          } else if (err.status === 429) {
            setLoginError('Too many login attempts. Please wait a moment and try again.');
          } else {
            setLoginError('Login failed. Please try again.');
          }
        } else {
          setLoginError('Network error. Please check your connection and try again.');
        }
      });
    setLoginGate(gate);
    router.replace('/new');
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <Card className="w-full max-w-xs animate-[fade-up_0.5s_ease-out]">
        <CardContent>
          <div className="text-center space-y-8">
            <div className="flex justify-center">
              <Image
                src="/milestone-logo-black.png"
                alt="Milestone"
                width={200}
                height={113}
                priority
                className="dark:hidden"
              />
              <Image
                src="/milestone-logo-white.png"
                alt="Milestone"
                width={200}
                height={113}
                priority
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
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => { setUsername(e.target.value); setError(''); }}
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
                disabled={loading}
                className="w-full h-11 rounded-xl text-sm font-medium"
              >
                {loading ? 'Signing in…' : 'Sign in'}
              </Button>
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
              <Image src="/MSFT.png" alt="Microsoft" width={18} height={18} priority />
              Sign in with Microsoft
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

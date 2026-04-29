'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { isAuthenticated, login } from '@/lib/auth';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated()) {
      router.replace('/new');
    }
  }, [router]);

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await login(username.trim(), password);
      router.replace('/new');
    } catch {
      setError('Invalid username or password.');
    } finally {
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
                src="/Milestone Logo 16x9 Transparent MAIN LOGO (black text).png"
                alt="Milestone"
                width={200}
                height={113}
                loading="eager"
                className="dark:hidden"
              />
              <Image
                src="/Milestone Logo 16x9 Transparent MAIN LOGO (white text).png"
                alt="Milestone"
                width={200}
                height={113}
                loading="eager"
                className="hidden dark:block"
              />
            </div>

            <div className="space-y-2">
              <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                Welcome to Quest
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
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setError(''); }}
                  className="h-10 rounded-xl"
                />
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
              <Image src="/MSFT.png" alt="Microsoft" width={18} height={18} />
              Sign in with Microsoft
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

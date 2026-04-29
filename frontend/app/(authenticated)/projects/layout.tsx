'use client';

import { useEffect } from 'react';
import { useThreadStore } from '@/lib/store/threads';

export default function ProjectsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const setCurrentThread = useThreadStore((s) => s.setCurrentThread);

  useEffect(() => {
    setCurrentThread(null);
  }, [setCurrentThread]);

  return <>{children}</>;
}

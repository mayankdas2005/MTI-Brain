'use client';

import { useState, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { WelcomeState } from '@/components/welcome-state';
import { NewChatComposer } from '@/components/new-chat-composer';

/**
 * Thin wrapper that reads the optional ?project= search param.
 * Isolated in its own Suspense boundary so the rest of the page
 * renders immediately during SSR instead of showing a blank screen.
 */
function ComposerWithProject({ initialValue }: { initialValue: string }) {
  const searchParams = useSearchParams();
  const projectId = searchParams.get('project') || undefined;
  return <NewChatComposer initialValue={initialValue} projectId={projectId} />;
}

export default function NewPage() {
  const [pendingSuggestion, setPendingSuggestion] = useState('');

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Scrollable welcome content — vertically centered; CLS prevented by stable skeleton heights */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="flex flex-col items-center justify-center min-h-full px-4 py-10">
          <div className="w-full max-w-3xl lg:max-w-[900px]">
            <WelcomeState onSuggestion={(prompt) => setPendingSuggestion(prompt)} />
          </div>
        </div>
      </div>
      {/* Composer anchored to bottom — same position as ChatComposer on chat pages */}
      <Suspense fallback={<NewChatComposer initialValue="" />}>
        <ComposerWithProject initialValue={pendingSuggestion} />
      </Suspense>
    </div>
  );
}

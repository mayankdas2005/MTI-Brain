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
  return <NewChatComposer initialValue={initialValue} centered projectId={projectId} />;
}

export default function NewPage() {
  const [pendingSuggestion, setPendingSuggestion] = useState('');

  return (
    <div className="flex flex-col h-full bg-background">
      <div className="flex-1 flex flex-col items-center justify-center px-4">
        <div className="w-full max-w-2xl">
          <WelcomeState onSuggestion={(prompt) => setPendingSuggestion(prompt)} />
          <div className="mt-6">
            <Suspense fallback={<NewChatComposer initialValue="" centered />}>
              <ComposerWithProject initialValue={pendingSuggestion} />
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
}

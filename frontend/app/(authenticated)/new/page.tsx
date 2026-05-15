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
      {/* Single scrollable area — content + composer together.
          justify-center keeps them visually centered when there's room;
          overflow-y-auto lets them scroll on short viewports. */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="flex flex-col items-center justify-center min-h-full px-4 py-4">
          <div className="w-full max-w-2xl lg:max-w-3xl">
            <WelcomeState onSuggestion={(prompt) => setPendingSuggestion(prompt)} />
            <div className="mt-4">
              <Suspense fallback={<NewChatComposer initialValue="" centered />}>
                <ComposerWithProject initialValue={pendingSuggestion} />
              </Suspense>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

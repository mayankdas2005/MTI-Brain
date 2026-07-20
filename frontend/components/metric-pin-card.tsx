'use client';

import { useRouter } from 'next/navigation';
import { RefreshCw, X } from 'lucide-react';
import { useThreadStore, setThreadCreationGate } from '@/lib/store/threads';
import { setLoginGate } from '@/lib/auth';
import type { PinnedMetric } from '@/lib/store/pinned-metrics';
import { usePinnedMetricsStore } from '@/lib/store/pinned-metrics';
import { toast } from '@/lib/toast';
import { randomId } from '@/lib/utils';

interface MetricPinCardProps {
  metric: PinnedMetric;
  className?: string;
}

export function MetricPinCard({ metric, className }: MetricPinCardProps) {
  const router = useRouter();
  const unpin = usePinnedMetricsStore((s) => s.unpinMetric);
  const createThread = useThreadStore((s) => s.createThread);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);

  const handleRerun = () => {
    const threadId = randomId();
    const gate = createThread(undefined, undefined, threadId)
      .then(() => {})
      .catch(() => {
        toast.error('Failed to create chat.');
        setPendingQuestion(null);
        setThreadCreationGate(null);
      });
    setThreadCreationGate(gate);
    setPendingQuestion(metric.source_query, false);
    router.push(`/chat/${threadId}`);
  };

  const handleUnpin = async () => {
    try {
      await unpin(metric.id);
    } catch {
      toast.error('Failed to unpin metric.');
    }
  };

  return (
    <div className={`group relative rounded-xl border border-border bg-background px-4 py-3 hover:border-primary/30 hover:bg-accent/20 transition-colors${className ? ` ${className}` : ''}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-foreground truncate leading-relaxed flex-1">
          {metric.label}
        </p>
        <button
          onClick={handleUnpin}
          aria-label="Unpin metric"
          className="shrink-0 opacity-100 sm:opacity-0 group-hover:opacity-100 transition-opacity h-5 w-5 flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted"
        >
          <X className="w-3 h-3" />
        </button>
      </div>
      <button
        onClick={handleRerun}
        className="mt-2 inline-flex items-center gap-1 text-[11px] text-primary hover:text-primary/80 transition-colors"
      >
        <RefreshCw className="w-3 h-3" />
        Re-run
      </button>
    </div>
  );
}

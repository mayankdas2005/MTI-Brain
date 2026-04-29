'use client';

import { useState } from 'react';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { useThreadStore } from '@/lib/store/threads';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface FeedbackWidgetProps {
  threadId: string;
  conversationId: string;
  feedback?: { liked: boolean; comment?: string };
}

export function FeedbackWidget({ threadId, conversationId, feedback }: FeedbackWidgetProps) {
  const submitFeedback = useThreadStore((s) => s.submitFeedback);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [comment, setComment] = useState('');
  const [pendingLiked, setPendingLiked] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const openFeedback = (liked: boolean) => {
    if (submitting) return;
    if (feedback?.liked === liked) return; // already submitted same
    setPendingLiked(liked);
    setComment('');
    setDialogOpen(true);
  };

  const handleSubmit = async () => {
    if (pendingLiked === null || submitting) return;
    setSubmitting(true);
    try {
      await submitFeedback(threadId, conversationId, pendingLiked, comment || undefined);
    } catch {
      // handled by store
    }
    setSubmitting(false);
    setDialogOpen(false);
    setComment('');
    setPendingLiked(null);
  };

  const isPositive = pendingLiked === true;

  return (
    <>
      <div className="flex items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className={`h-7 w-7 p-0 rounded-lg transition-colors ${
                feedback?.liked === true
                  ? 'text-green-600 bg-green-500/20 ring-1 ring-green-500/30'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent'
              }`}
              onClick={() => openFeedback(true)}
              disabled={submitting || feedback?.liked === true}
            >
              <ThumbsUp className={`w-3.5 h-3.5 ${feedback?.liked === true ? 'fill-green-600' : ''}`} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Give positive feedback</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className={`h-7 w-7 p-0 rounded-lg transition-colors ${
                feedback?.liked === false
                  ? 'text-red-600 bg-red-500/20 ring-1 ring-red-500/30'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent'
              }`}
              onClick={() => openFeedback(false)}
              disabled={submitting || feedback?.liked === false}
            >
              <ThumbsDown className={`w-3.5 h-3.5 ${feedback?.liked === false ? 'fill-red-600' : ''}`} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Give negative feedback</TooltipContent>
        </Tooltip>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md p-6 gap-0" aria-describedby={undefined}>
          <DialogTitle className="text-lg font-semibold text-foreground mb-1">
            {isPositive ? 'Give positive feedback' : 'Give negative feedback'}
          </DialogTitle>
          <p className="text-sm text-muted-foreground mb-4">
            {isPositive
              ? 'Please provide details: (optional)'
              : 'What went wrong with this response?'}
          </p>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={
              isPositive
                ? 'What was satisfying about this response?'
                : 'What was incorrect, unhelpful, or could be improved?'
            }
            className="w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm resize-none h-24 focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground"
            maxLength={2000}
            autoFocus
          />
          <DialogFooter className="mt-4">
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={submitting}>
              {submitting ? 'Submitting...' : 'Submit'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

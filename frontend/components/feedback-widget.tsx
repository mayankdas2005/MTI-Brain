'use client';

import { useState, useOptimistic, useTransition } from 'react';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { useThreadStore } from '@/lib/store/threads';
import { track, Events } from '@/lib/analytics';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { toast } from '@/lib/toast';

interface FeedbackWidgetProps {
  threadId: string;
  conversationId: string;
  feedback?: { liked: boolean; comment?: string };
}

type FeedbackType = 'answer' | 'sql' | 'chart' | 'general';

const DISLIKE_TYPES: { label: string; value: FeedbackType }[] = [
  { label: 'The answer', value: 'answer' },
  { label: 'The SQL', value: 'sql' },
  { label: 'The chart', value: 'chart' },
  { label: 'Something else', value: 'general' },
];

const LIKE_TYPES: { label: string; value: FeedbackType }[] = [
  { label: 'The answer', value: 'answer' },
  { label: 'The SQL', value: 'sql' },
  { label: 'The chart', value: 'chart' },
  { label: 'Everything', value: 'general' },
];

export function FeedbackWidget({ threadId, conversationId, feedback }: FeedbackWidgetProps) {
  const submitFeedback = useThreadStore((s) => s.submitFeedback);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [comment, setComment] = useState('');
  const [pendingLiked, setPendingLiked] = useState<boolean | null>(null);
  const [feedbackType, setFeedbackType] = useState<FeedbackType>('answer');
  const [isPending, startTransition] = useTransition();
  const [optimisticLiked, setOptimisticLiked] = useOptimistic(feedback?.liked);

  const openFeedback = (liked: boolean) => {
    if (isPending) return;
    if (optimisticLiked === liked) return;
    setPendingLiked(liked);
    setComment('');
    setFeedbackType('answer');
    setDialogOpen(true);
  };

  const handleSubmit = () => {
    if (pendingLiked === null) return;
    const likedValue = pendingLiked;
    const commentValue = comment;
    const typeValue = feedbackType;
    setDialogOpen(false);
    setComment('');
    setPendingLiked(null);
    setFeedbackType('answer');
    startTransition(async () => {
      setOptimisticLiked(likedValue);
      try {
        await submitFeedback(threadId, conversationId, likedValue, commentValue || undefined, typeValue);
        track(Events.FeedbackGiven, {
          liked: likedValue,
          has_comment: commentValue.trim().length > 0,
          feedback_type: typeValue,
        });
      } catch {
        toast.error('Failed to submit feedback.');
      }
    });
  };

  const isPositive = pendingLiked === true;
  const typeOptions = isPositive ? LIKE_TYPES : DISLIKE_TYPES;

  return (
    <>
      <div className="flex items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className={`h-7 w-7 p-0 rounded-lg transition-colors ${
                optimisticLiked === true
                  ? 'text-green-700 dark:text-green-500 bg-green-500/20 ring-1 ring-green-500/30'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent'
              }`}
              onClick={() => openFeedback(true)}
              disabled={isPending || optimisticLiked === true}
              aria-label={optimisticLiked === true ? 'Positive feedback submitted' : 'Give positive feedback'}
              aria-pressed={optimisticLiked === true}
            >
              <ThumbsUp className={`w-3.5 h-3.5 ${optimisticLiked === true ? 'fill-green-700 dark:fill-green-500' : ''}`} />
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
                optimisticLiked === false
                  ? 'text-red-700 dark:text-red-500 bg-red-500/20 ring-1 ring-red-500/30'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent'
              }`}
              onClick={() => openFeedback(false)}
              disabled={isPending || optimisticLiked === false}
              aria-label={optimisticLiked === false ? 'Negative feedback submitted' : 'Give negative feedback'}
              aria-pressed={optimisticLiked === false}
            >
              <ThumbsDown className={`w-3.5 h-3.5 ${optimisticLiked === false ? 'fill-red-700 dark:fill-red-500' : ''}`} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Give negative feedback</TooltipContent>
        </Tooltip>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md p-6 gap-0">
          <DialogTitle className="text-lg font-semibold text-foreground mb-1">
            {isPositive ? 'Give positive feedback' : 'Give negative feedback'}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {isPositive ? 'Share what was satisfying about this response' : 'Tell us what went wrong with this response'}
          </DialogDescription>

          <p className="text-sm text-muted-foreground mb-3">
            {isPositive ? 'What did you like?' : 'What was wrong?'}
          </p>
          <div className="flex flex-wrap gap-2 mb-4">
            {typeOptions.map(({ label, value }) => (
              <button
                key={value}
                type="button"
                onClick={() => setFeedbackType(value)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                  feedbackType === value
                    ? isPositive
                      ? 'bg-green-500/20 border-green-500/40 text-green-700 dark:text-green-400'
                      : 'bg-red-500/20 border-red-500/40 text-red-700 dark:text-red-400'
                    : 'bg-muted/50 border-border text-muted-foreground hover:text-foreground hover:border-foreground/30'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <p className="text-sm text-muted-foreground mb-2">
            {isPositive ? 'Add a comment (optional):' : 'Tell us more (optional):'}
          </p>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={
              isPositive
                ? 'What was satisfying about this response?'
                : 'What was incorrect, unhelpful, or could be improved?'
            }
            aria-label={isPositive ? 'Positive feedback comment' : 'Negative feedback comment'}
            className="w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm resize-none h-24 focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground"
            maxLength={2000}
            autoFocus
          />
          <DialogFooter className="mt-4">
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={isPending}>
              Submit
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

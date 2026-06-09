'use client';

import { useEffect, useLayoutEffect, useState, useMemo, useRef } from 'react';
import { Button } from '@/components/ui/button';
import {
  X,
  ArrowRight,
  ArrowLeft,
  MessageSquare,
  FolderOpen,
  FolderInput,
  Search,
  Plus,
  Star,
  FileDown,
  User as UserIcon,
  Keyboard,
  Sparkles,
} from 'lucide-react';
import { modifierLabel } from '@/lib/utils/platform';

const STORAGE_KEY = 'mti-brain:onboarding-complete-v2';

interface Step {
  id: string;
  /** CSS selector pointing at the element this step describes. Omit for a
   *  centered/no-target overview slide. */
  selector?: string;
  title: string;
  body: string;
  icon: typeof MessageSquare;
  /** Where to position the popover. Ignored when selector is omitted. */
  placement?: 'top' | 'bottom' | 'right' | 'left';
  /** When true, skip this step if the target is missing (e.g. star/export
   *  only exist when a thread is open). When false/omitted, fall back to
   *  centered. */
  skipIfMissing?: boolean;
}

function makeSteps(): Step[] {
  const mod = modifierLabel();
  return [
    {
      id: 'welcome',
      title: 'Welcome to MTI Brain',
      body:
        'Your AI-powered financial data assistant. This short tour covers the key features - use Next to continue, or Skip to jump straight in.',
      icon: Sparkles,
    },
    {
      id: 'composer',
      selector: '[data-onboarding="composer"]',
      title: 'Ask in plain language',
      body: `Type any question in natural language and press Enter (⇧+Enter for a new line). MTI Brain translates it into SQL, runs it against your data, and streams results as charts, tables, or summaries. Press Esc to stop a running response.`,
      icon: MessageSquare,
      placement: 'top',
    },
    {
      id: 'sidebar',
      selector: '[data-onboarding="sidebar"]',
      title: 'Navigate your work',
      body: `Switch between Projects and Chats at the top. Starred conversations stay pinned for quick access, and Recents shows your history grouped by date. Hover any conversation to rename it. Press ${mod}+. to toggle the sidebar.`,
      icon: FolderOpen,
      placement: 'right',
      skipIfMissing: true,
    },
    {
      id: 'new-chat',
      selector: '[data-onboarding="new-chat"]',
      title: 'Start a new conversation',
      body: `Click New Chat or press ${mod}+Shift+O to begin a fresh query. Each conversation is private to you and saved automatically.`,
      icon: Plus,
      placement: 'right',
      skipIfMissing: true,
    },
    {
      id: 'cmd-k',
      selector: '[data-onboarding="cmd-k"]',
      title: 'Search your history',
      body: `Press ${mod}+K (or /) to search across all your past conversations by title or content. Use ${mod}+1 through ${mod}+9 to jump directly to a recent conversation.`,
      icon: Search,
      placement: 'bottom',
    },
    {
      id: 'star',
      selector: '[data-onboarding="star"]',
      title: 'Star important conversations',
      body: `Star any conversation to pin it to the top of your sidebar — ideal for ongoing analyses or reports you return to regularly. Shortcut: ${mod}+S.`,
      icon: Star,
      placement: 'bottom',
      skipIfMissing: true,
    },
    {
      id: 'add-to-project',
      selector: '[data-onboarding="add-to-project"]',
      title: 'Organise into projects',
      body: `Move this conversation into a project to keep related analyses grouped together. Shortcut: ${mod}+Shift+M.`,
      icon: FolderInput,
      placement: 'bottom',
      skipIfMissing: true,
    },
    // Export PDF step - hidden
    // {
    //   id: 'export-pdf',
    //   selector: '[data-onboarding="export-pdf"]',
    //   title: 'Export as PDF',
    //   body:
    //     'Save any conversation as a polished PDF - full responses, SQL, data tables, and rendered charts included.',
    //   icon: FileDown,
    //   placement: 'bottom',
    //   skipIfMissing: true,
    // },
    {
      id: 'user-menu',
      selector: '[data-onboarding="user-menu"]',
      title: 'Theme & preferences',
      body: `Open the user menu to switch between light and dark theme, adjust your preferences in Settings, or sign out. Shortcuts: ${mod}+, for Settings, ${mod}+Shift+L to toggle theme.`,
      icon: UserIcon,
      placement: 'top',
      skipIfMissing: true,
    },
    {
      id: 'shortcuts',
      title: 'Keyboard shortcuts',
      body: `Press ${mod}+/ or ? to view all shortcuts at any time.`,
      icon: Keyboard,
    },
    {
      id: 'done',
      title: "You're ready",
      body:
        'For best results, ask specific questions - include a metric, date range, or business segment. Use Deep Analysis for complex, multi-step queries.',
      icon: Sparkles,
    },
  ];
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

function readRect(selector: string): Rect | null {
  if (typeof document === 'undefined') return null;
  const el = document.querySelector(selector);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

interface OnboardingTourProps {
  /** When true, force the tour open regardless of the persisted dismiss
   *  flag (e.g. user clicked "Replay tour" from settings). */
  forceOpen?: boolean;
  onClose?: () => void;
}

export function OnboardingTour({ forceOpen, onClose }: OnboardingTourProps = {}) {
  const allSteps = useMemo(makeSteps, []);
  const [active, setActive] = useState(false);
  const [stepIdx, setStepIdx] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [popoverSize, setPopoverSize] = useState<{ w: number; h: number }>({
    w: 340,
    h: 220,
  });

  // Filter steps whose selector exists (or that don't require a target).
  // Re-computed when active flips so we re-check after the DOM settles.
  const steps = useMemo(() => {
    if (!active) return allSteps;
    return allSteps.filter((s) => {
      if (!s.selector) return true;
      const exists = !!document.querySelector(s.selector);
      return exists || !s.skipIfMissing;
    });
  }, [active, allSteps]);

  const step = steps[stepIdx];

  // Auto-open on first login OR when forceOpen flips true.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (forceOpen) {
      setStepIdx(0);
      setActive(true);
      return;
    }
    let done = false;
    try {
      done = !!localStorage.getItem(STORAGE_KEY);
    } catch {
      // private mode - re-show every load is acceptable
    }
    if (!done) {
      const t = setTimeout(() => setActive(true), 400);
      return () => clearTimeout(t);
    }
  }, [forceOpen]);

  // Recompute target rect each step, and on resize/scroll while active.
  useLayoutEffect(() => {
    if (!active || !step) return;
    if (!step.selector) {
      setRect(null);
      return;
    }
    const update = () => setRect(readRect(step.selector!));
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [active, step]);

  // Measure the actual rendered popover so we can clamp it to the viewport
  // for either placement direction. Re-measures whenever the step changes.
  useLayoutEffect(() => {
    if (!active) return;
    const el = popoverRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.width !== popoverSize.w || r.height !== popoverSize.h) {
      setPopoverSize({ w: r.width, h: r.height });
    }
  }, [active, step, rect]);

  const finish = () => {
    try {
      localStorage.setItem(STORAGE_KEY, '1');
    } catch {
      // ignore quota
    }
    setActive(false);
    setStepIdx(0);
    onClose?.();
  };

  if (!active || !step) return null;

  // Popover positioning around the target rect. Computes a top-left for the
  // raw popover (no transforms) so vertical AND horizontal clamping is
  // straightforward - we work with the visible bounding box, not a translated
  // anchor. Falls back to centered if there's no selector or target is missing.
  const isPhone = typeof window !== 'undefined' && window.innerWidth < 768;
  const popoverTargetWidth = isPhone
    ? Math.min(typeof window !== 'undefined' ? window.innerWidth - 32 : 320, 320)
    : 380;
  const popoverWidth = popoverSize.w || popoverTargetWidth;
  const popoverHeight = popoverSize.h || (isPhone ? 220 : 200);
  const edge = isPhone ? 16 : 8;
  const margin = 12;
  let popStyle: React.CSSProperties;
  if (step.selector && rect && typeof window !== 'undefined') {
    // On phones, side-anchored popovers never have room - flip to top/bottom.
    const placement = isPhone && (step.placement === 'left' || step.placement === 'right')
      ? (rect.top > window.innerHeight / 2 ? 'top' : 'bottom')
      : step.placement;
    let top = 0;
    let left = 0;
    switch (placement) {
      case 'top':
        top = rect.top - margin - popoverHeight;
        left = rect.left + rect.width / 2 - popoverWidth / 2;
        break;
      case 'bottom':
        top = rect.top + rect.height + margin;
        left = rect.left + rect.width / 2 - popoverWidth / 2;
        break;
      case 'right':
        top = rect.top + rect.height / 2 - popoverHeight / 2;
        left = rect.left + rect.width + margin;
        break;
      default: // 'left'
        top = rect.top + rect.height / 2 - popoverHeight / 2;
        left = rect.left - margin - popoverWidth;
    }
    // Clamp both axes so the popover stays fully on-screen.
    const maxLeft = window.innerWidth - popoverWidth - edge;
    const maxTop = window.innerHeight - popoverHeight - edge;
    left = Math.max(edge, Math.min(left, maxLeft));
    top = Math.max(edge, Math.min(top, maxTop));
    popStyle = { top, left };
  } else {
    popStyle = {
      top: '50%',
      left: '50%',
      transform: 'translate(-50%, -50%)',
    };
  }

  const Icon = step.icon;
  const isLast = stepIdx === steps.length - 1;
  const isFirst = stepIdx === 0;

  return (
    <div className="fixed inset-0 z-[60] pointer-events-none" role="dialog" aria-label="Onboarding tour" aria-modal="false">
      {/* Soft dim - pointer-events-none so the user can still interact
          with the highlighted element if they want. */}
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" />

      {/* Spotlight ring around the target */}
      {step.selector && rect && (
        <div
          className="absolute rounded-xl ring-2 ring-primary shadow-[0_0_0_9999px_rgba(0,0,0,0.2)] transition-all duration-300"
          style={{
            top: rect.top - 4,
            left: rect.left - 4,
            width: rect.width + 8,
            height: rect.height + 8,
          }}
          aria-hidden
        />
      )}

      {/* Popover */}
      <div
        ref={popoverRef}
        className="absolute pointer-events-auto rounded-2xl border border-border bg-popover text-popover-foreground shadow-xl p-4"
        style={{ ...popStyle, width: popoverTargetWidth }}
      >
        <div className="flex items-start gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 text-primary shrink-0">
            <Icon className="w-4 h-4" aria-hidden />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold">{step.title}</p>
            <p className="text-sm text-muted-foreground mt-1 leading-relaxed">{step.body}</p>
          </div>
          <button
            type="button"
            onClick={finish}
            aria-label="Skip tour"
            className="shrink-0 -mr-1 -mt-1 p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
          >
            <X className="w-3.5 h-3.5" aria-hidden />
          </button>
        </div>

        {/* Progress dots */}
        <div className="flex items-center justify-center gap-1.5 mt-4">
          {steps.map((_, i) => (
            <span
              key={i}
              className={`h-1.5 rounded-full transition-all ${
                i === stepIdx ? 'w-4 bg-primary' : 'w-1.5 bg-muted-foreground/30'
              }`}
              aria-hidden
            />
          ))}
        </div>

        <div className="flex items-center justify-between mt-3">
          <span className="text-[11px] text-muted-foreground/70 tabular-nums">
            {stepIdx + 1} of {steps.length}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={finish} className="h-8">
              Skip
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setStepIdx(Math.max(0, stepIdx - 1))}
              disabled={isFirst}
              className="h-8 gap-1.5"
            >
              <ArrowLeft className="w-3.5 h-3.5" aria-hidden />
              Back
            </Button>
            <Button
              size="sm"
              onClick={() => (isLast ? finish() : setStepIdx(stepIdx + 1))}
              className="h-8 gap-1.5"
            >
              {isLast ? 'Got it' : 'Next'}
              {!isLast && <ArrowRight className="w-3.5 h-3.5" aria-hidden />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

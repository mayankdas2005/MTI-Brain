/**
 * Habit-loop telemetry. Thin wrapper around posthog-js that:
 *
 * - Reads `NEXT_PUBLIC_POSTHOG_KEY` / `NEXT_PUBLIC_POSTHOG_HOST` lazily.
 * - Inits once on first call rather than at module-load (avoids competing
 *   with first-paint and first SSE chunk).
 * - No-ops cleanly when no key is configured (so the app works fine for
 *   developers without a PostHog account).
 *
 * All event names are snake_case verbs in past-tense, matching PostHog
 * funnel-builder conventions.
 */

import type { PostHog } from 'posthog-js';

let instance: PostHog | null = null;
let initStarted = false;
let initSettled = false;

function shouldInit(): boolean {
  if (typeof window === 'undefined') return false;
  return !!process.env.NEXT_PUBLIC_POSTHOG_KEY;
}

async function ensureInit(): Promise<PostHog | null> {
  if (initSettled) return instance;
  if (!shouldInit()) {
    initSettled = true;
    return null;
  }
  if (initStarted) {
    while (!initSettled) await new Promise((r) => setTimeout(r, 50));
    return instance;
  }
  initStarted = true;

  const mod = await import('posthog-js');
  const ph = mod.default;
  ph.init(process.env.NEXT_PUBLIC_POSTHOG_KEY as string, {
    api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST || 'https://us.i.posthog.com',
    person_profiles: 'identified_only',
    capture_pageview: false, // We capture manually via Next.js router events.
    capture_pageleave: true,
    autocapture: false,
    persistence: 'localStorage+cookie',
    disable_session_recording: true,
    loaded: () => {
      instance = ph;
      initSettled = true;
    },
  });
  if (!initSettled) {
    instance = ph;
    initSettled = true;
  }
  return instance;
}

/** Fire-and-forget event. Safe to call before init completes. */
export function track(event: string, properties?: Record<string, unknown>) {
  void ensureInit().then((ph) => {
    ph?.capture(event, properties);
  });
}

export function identify(distinctId: string, properties?: Record<string, unknown>) {
  void ensureInit().then((ph) => {
    ph?.identify(distinctId, properties);
  });
}

export function resetAnalytics() {
  if (instance) instance.reset();
}

export function trackPageview(path: string) {
  void ensureInit().then((ph) => {
    ph?.capture('$pageview', { $current_url: path });
  });
}

/** Standardized event names — keep in sync with the funnel builder. */
export const Events = {
  ChatCreated: 'chat_created',
  QuestionAsked: 'question_asked',
  FeedbackGiven: 'feedback_given',
  ThreadRenamed: 'thread_renamed',
  ThreadStarred: 'thread_starred',
  ThreadDeleted: 'thread_deleted',
  SuggestionClicked: 'suggestion_clicked',
  SlashCommandUsed: 'slash_command_used',
  PaletteActionUsed: 'palette_action_used',
  ResponseCopied: 'response_copied',
  ResponseRetried: 'response_retried',
  ExportPdf: 'export_pdf',
} as const;

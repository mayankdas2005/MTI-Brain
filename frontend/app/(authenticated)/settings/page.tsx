'use client';

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from 'react';
import {
  Search,
  X,
  RotateCcw,
  Palette,
  MessageSquare,
  Eye,
  Bell,
  Info,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  usePreferencesStore,
  PREFERENCES_DEFAULTS,
  type ResponseTone,
  type DefaultDataView,
  type Density,
  type TTSRate,
} from '@/lib/store/preferences';
import { useAvailableVoices } from '@/lib/hooks/use-tts';
import {
  getPermission,
  notificationsSupported,
  requestPermission,
  type NotificationPermissionState,
} from '@/lib/utils/notifications';
import { toast } from '@/lib/toast';

type SectionId =
  | 'response-style'
  | 'display'
  | 'appearance'
  | 'notifications'
  | 'about';

interface SectionDef {
  id: SectionId;
  label: string;
  icon: ComponentType<{ className?: string }>;
}

const SECTIONS: SectionDef[] = [
  { id: 'response-style', label: 'Response style', icon: MessageSquare },
  { id: 'display', label: 'Display', icon: Eye },
  { id: 'appearance', label: 'Appearance', icon: Palette },
  { id: 'notifications', label: 'Notifications', icon: Bell },
  { id: 'about', label: 'About', icon: Info },
];

const TONE_OPTIONS: { value: ResponseTone; label: string; description: string }[] = [
  { value: 'analyst', label: 'Analyst', description: 'Data-driven, detailed breakdowns' },
  { value: 'manager', label: 'Manager', description: 'Actionable insights with context' },
  { value: 'director', label: 'Director', description: 'Strategic summaries with key metrics' },
  { value: 'executive', label: 'Executive', description: 'High-level, decision-ready answers' },
];

const ROW_OPTIONS = [50, 100, 200, 500];

/**
 * /settings - full-page surface for all user preferences.
 *
 * Two-column layout on desktop (sticky left rail with section nav, right
 * pane with content). Sections deep-link via URL hash and are tracked
 * with an IntersectionObserver so the rail highlights the section the
 * user is reading.
 *
 * The search input filters by matching query tokens against each row's
 * label + description + keywords. A section with no visible rows is
 * hidden entirely so the page collapses to just what matched.
 */
export default function SettingsPage() {
  const responseTone = usePreferencesStore((s) => s.responseTone);
  const setResponseTone = usePreferencesStore((s) => s.setResponseTone);
  const showSQL = usePreferencesStore((s) => s.showSQL);
  const setShowSQL = usePreferencesStore((s) => s.setShowSQL);
  const autoShowCharts = usePreferencesStore((s) => s.autoShowCharts);
  const setAutoShowCharts = usePreferencesStore((s) => s.setAutoShowCharts);
  const showFollowUps = usePreferencesStore((s) => s.showFollowUps);
  const setShowFollowUps = usePreferencesStore((s) => s.setShowFollowUps);
  const showReasoning = usePreferencesStore((s) => s.showReasoning);
  const setShowReasoning = usePreferencesStore((s) => s.setShowReasoning);
  const defaultDataView = usePreferencesStore((s) => s.defaultDataView);
  const setDefaultDataView = usePreferencesStore((s) => s.setDefaultDataView);
  const maxResultRows = usePreferencesStore((s) => s.maxResultRows);
  const setMaxResultRows = usePreferencesStore((s) => s.setMaxResultRows);
  const notifyOnComplete = usePreferencesStore((s) => s.notifyOnComplete);
  const setNotifyOnComplete = usePreferencesStore((s) => s.setNotifyOnComplete);
  const notifySound = usePreferencesStore((s) => s.notifySound);
  const setNotifySound = usePreferencesStore((s) => s.setNotifySound);
  const density = usePreferencesStore((s) => s.density);
  const setDensity = usePreferencesStore((s) => s.setDensity);
  const ttsRate = usePreferencesStore((s) => s.ttsRate ?? 1);
  const setTTSRate = usePreferencesStore((s) => s.setTTSRate);
  const ttsVoiceURI = usePreferencesStore((s) => s.ttsVoiceURI ?? '');
  const setTTSVoiceURI = usePreferencesStore((s) => s.setTTSVoiceURI);
  const highContrast = usePreferencesStore((s) => s.highContrast ?? false);
  const setHighContrast = usePreferencesStore((s) => s.setHighContrast);
  const resetToDefaults = usePreferencesStore((s) => s.resetToDefaults);
  const hydrated = usePreferencesStore((s) => s.hydrated);

  const [query, setQuery] = useState('');
  // Always start at the first section to keep server-rendered HTML stable.
  // The initial hash (if any) is applied in a post-mount useEffect - reading
  // window.location.hash in the initializer would diverge from SSR and
  // cause an aria-current hydration mismatch on the rail.
  const [activeSection, setActiveSection] = useState<SectionId>('response-style');
  const [resetOpen, setResetOpen] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  // Timestamp of the last user-initiated jump (rail click). The
  // IntersectionObserver suppresses updates within ~800ms of this so the
  // smooth-scroll tail doesn't re-pick a different section. Without
  // this, clicking "Appearance" on a short page would snap the rail
  // back to "Response style" because every section is on screen and
  // Response style is topmost.
  const userJumpRef = useRef<number>(0);

  // Whether any preference differs from its default - drives the "Reset"
  // button's enabled state. softPromptShown is a system flag, not a user
  // setting, so it's excluded from the diff.
  const anyNonDefault = useMemo(() => {
    return (
      responseTone !== PREFERENCES_DEFAULTS.responseTone ||
      showSQL !== PREFERENCES_DEFAULTS.showSQL ||
      autoShowCharts !== PREFERENCES_DEFAULTS.autoShowCharts ||
      showFollowUps !== PREFERENCES_DEFAULTS.showFollowUps ||
      showReasoning !== PREFERENCES_DEFAULTS.showReasoning ||
      defaultDataView !== PREFERENCES_DEFAULTS.defaultDataView ||
      maxResultRows !== PREFERENCES_DEFAULTS.maxResultRows ||
      notifyOnComplete !== PREFERENCES_DEFAULTS.notifyOnComplete ||
      notifySound !== PREFERENCES_DEFAULTS.notifySound ||
      density !== PREFERENCES_DEFAULTS.density
    );
  }, [
    responseTone,
    showSQL,
    autoShowCharts,
    showFollowUps,
    showReasoning,
    defaultDataView,
    maxResultRows,
    notifyOnComplete,
    notifySound,
    density,
  ]);

  // IntersectionObserver: highlight the section nearest the top of the
  // scroll container. Only one section can be "active" at a time. We
  // observe the section headers, not the whole section, so a long
  // section doesn't beat out a shorter one further down.
  useEffect(() => {
    const root = scrollContainerRef.current;
    if (!root) return;
    const targets = SECTIONS.map((s) =>
      document.getElementById(`section-${s.id}`),
    ).filter((el): el is HTMLElement => el != null);
    if (targets.length === 0) return;

    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        // Suppress while a click-driven jump is mid-flight - the smooth
        // scroll tail otherwise overrides what the user just selected.
        if (Date.now() < userJumpRef.current + 800) return;
        // When the page fits in the viewport (no overflow) every section
        // is permanently "topmost-visible" and the observer would lock
        // the rail to the first section. Respect user clicks instead.
        if (root.scrollHeight <= root.clientHeight + 10) return;

        for (const entry of entries) {
          if (entry.isIntersecting) {
            visible.set(entry.target.id, entry.intersectionRatio);
          } else {
            visible.delete(entry.target.id);
          }
        }
        if (visible.size === 0) return;
        // Prefer the topmost visible section (lowest boundingClientRect.top).
        let best: { id: string; top: number } | null = null;
        for (const id of visible.keys()) {
          const el = document.getElementById(id);
          if (!el) continue;
          const top = el.getBoundingClientRect().top;
          if (!best || top < best.top) best = { id, top };
        }
        if (best) {
          const sectionId = best.id.replace(/^section-/, '') as SectionId;
          setActiveSection(sectionId);
        }
      },
      { root, rootMargin: '-10% 0px -70% 0px', threshold: [0, 1] },
    );
    targets.forEach((t) => observer.observe(t));
    return () => observer.disconnect();
  }, []);

  // Honour deep-link hash on mount so /settings#notifications both
  // highlights its rail entry AND scrolls to that section. We do this
  // post-mount (rather than in the useState initializer) so the initial
  // server-rendered HTML matches the client's first render and we don't
  // hydration-mismatch the rail's aria-current.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const hash = window.location.hash.replace(/^#/, '') as SectionId;
    if (SECTIONS.some((s) => s.id === hash)) {
      setActiveSection(hash);
      userJumpRef.current = Date.now();
      const el = document.getElementById(`section-${hash}`);
      if (el) {
        // Defer to next frame so the layout is settled.
        requestAnimationFrame(() => el.scrollIntoView({ block: 'start' }));
      }
    }
  }, []);

  const matches = useMemo(() => makeMatcher(query), [query]);

  // Visibility helper for each row. The `keywords` string is the
  // matchable haystack (label + description + any aliases the search
  // user might type - e.g. "dark" matches the theme row even though
  // theme isn't in the label).
  const v = (keywords: string) => matches(keywords);

  // Section visibility = at least one row in the section matches.
  const sectionVisible: Record<SectionId, boolean> = {
    'response-style': TONE_OPTIONS.some((o) =>
      matches(`response style tone ${o.label} ${o.description}`),
    ),
    display:
      v('show sql queries display') ||
      v('auto show charts visualizations') ||
      v('follow-up suggestions follow up') ||
      v('show reasoning thinking process') ||
      v('default data view sql table') ||
      v('max result rows per query'),
    appearance: v('density compact comfortable spacing rows'),
    notifications:
      v('notify when answers finish notifications stream completion') ||
      v('play sound ping audio notifications') ||
      v('browser permission notifications'),
    about: v('about version mti brain'),
  };

  const visibleCount = Object.values(sectionVisible).filter(Boolean).length;

  const jumpTo = (id: SectionId) => {
    userJumpRef.current = Date.now();
    setActiveSection(id);
    const el = document.getElementById(`section-${id}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      history.replaceState(null, '', `#${id}`);
    }
  };

  const handleReset = () => {
    resetToDefaults();
    setResetOpen(false);
    toast.success('Preferences reset to defaults');
  };

  return (
    <div ref={scrollContainerRef} className="h-full overflow-y-auto">
      <div className="max-w-5xl xl:max-w-6xl mx-auto px-6 xl:px-8 py-8">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Customize how MTI Brain responds and displays data.
          </p>
        </div>

        {/* Search */}
        <div className="relative mb-6 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search settings..."
            aria-label="Search settings"
            className="pl-9 pr-9"
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              aria-label="Clear search"
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        <div className="flex flex-col lg:flex-row gap-8 lg:gap-10">
          {/* Left rail */}
          <nav
            aria-label="Settings sections"
            className="lg:w-48 shrink-0 lg:sticky lg:top-0 lg:self-start lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto"
          >
            <ul className="flex lg:flex-col gap-1 overflow-x-auto lg:overflow-x-visible -mx-1 px-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden pb-px lg:pb-0">
              {SECTIONS.map((s) => {
                const Icon = s.icon;
                const isActive = activeSection === s.id;
                const isHidden = !sectionVisible[s.id];
                if (isHidden) return null;
                return (
                  <li key={s.id} className="shrink-0">
                    <button
                      onClick={() => jumpTo(s.id)}
                      aria-current={isActive ? 'true' : undefined}
                      className={`w-full flex items-center gap-2 px-3 py-[var(--density-pad-y-tight)] rounded-md text-sm transition-colors text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
                        isActive
                          ? 'bg-accent text-foreground font-medium'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
                      }`}
                    >
                      <Icon className="w-4 h-4 shrink-0" />
                      <span className="truncate">{s.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>

          {/* Right pane */}
          <div className="flex-1 min-w-0 space-y-10 pb-16">
            {visibleCount === 0 && query && (
              <div className="text-center py-16">
                <p className="text-sm text-muted-foreground">
                  No settings match &quot;{query}&quot;
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setQuery('')}
                  className="mt-2"
                >
                  Clear search
                </Button>
              </div>
            )}

            {/* Response style */}
            {sectionVisible['response-style'] && (
              <Section id="response-style" title="Response style">
                <p className="text-xs text-muted-foreground -mt-1 mb-4">
                  How MTI Brain frames its answers.
                </p>
                <ToneCarousel
                  options={TONE_OPTIONS.filter((o) =>
                    matches(`response style tone ${o.label} ${o.description}`),
                  )}
                  value={responseTone}
                  onChange={setResponseTone}
                  hydrated={hydrated}
                  defaultValue={PREFERENCES_DEFAULTS.responseTone}
                />
              </Section>
            )}

            {/* Display */}
            {sectionVisible.display && (
              <Section id="display" title="Display">
                <div className="space-y-1">
                  {v('show sql queries display') && (
                    <ToggleRow
                      label="Show SQL queries"
                      description="Display the generated SQL alongside results."
                      checked={showSQL}
                      onCheckedChange={setShowSQL}
                      isDefault={
                        showSQL === PREFERENCES_DEFAULTS.showSQL
                      }
                    />
                  )}
                  {v('auto show charts visualizations') && (
                    <ToggleRow
                      label="Auto-show charts"
                      description="Automatically render data visualizations when the result fits a chart."
                      checked={autoShowCharts}
                      onCheckedChange={setAutoShowCharts}
                      isDefault={
                        autoShowCharts === PREFERENCES_DEFAULTS.autoShowCharts
                      }
                    />
                  )}
                  {v('follow-up suggestions follow up') && (
                    <ToggleRow
                      label="Follow-up suggestions"
                      description="Show suggested follow-up questions after responses."
                      checked={showFollowUps}
                      onCheckedChange={setShowFollowUps}
                      isDefault={
                        showFollowUps === PREFERENCES_DEFAULTS.showFollowUps
                      }
                    />
                  )}
                  {v('show reasoning thinking process') && (
                    <ToggleRow
                      label="Show reasoning"
                      description="Display the thinking-process panel for each query."
                      checked={showReasoning}
                      onCheckedChange={setShowReasoning}
                      isDefault={
                        showReasoning === PREFERENCES_DEFAULTS.showReasoning
                      }
                    />
                  )}
                </div>

                {v('default data view sql table') && (
                  <SettingBlock
                    label="Default result view"
                    description="Which tab opens first when query results arrive."
                  >
                    <div className="grid grid-cols-2 gap-2 max-w-sm">
                      {(['sql', 'table'] as DefaultDataView[]).map((view) => {
                        const isSelected = defaultDataView === view;
                        const isDefault =
                          view === PREFERENCES_DEFAULTS.defaultDataView;
                        return (
                          <div key={view} className="flex flex-col">
                            <button
                              onClick={() => setDefaultDataView(view)}
                              aria-pressed={isSelected}
                              className={`rounded-lg px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background transition-colors ${
                                isSelected
                                  ? 'ring-2 ring-primary bg-primary/10 font-medium text-foreground'
                                  : 'bg-muted/50 hover:bg-accent text-muted-foreground'
                              }`}
                            >
                              {view === 'sql' ? 'SQL Query' : 'Data Table'}
                            </button>
                            <DefaultTag visible={isDefault} />
                          </div>
                        );
                      })}
                    </div>
                  </SettingBlock>
                )}

                {v('max result rows per query') && (
                  <SettingBlock
                    label="Max rows per query"
                    description="Higher values return more data but take longer."
                  >
                    <div className="grid grid-cols-4 gap-2 max-w-sm">
                      {ROW_OPTIONS.map((rows) => {
                        const isSelected = maxResultRows === rows;
                        const isDefault =
                          rows === PREFERENCES_DEFAULTS.maxResultRows;
                        return (
                          <div key={rows} className="flex flex-col">
                            <button
                              onClick={() => setMaxResultRows(rows)}
                              aria-pressed={isSelected}
                              className={`rounded-lg px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background transition-colors tabular-nums ${
                                isSelected
                                  ? 'ring-2 ring-primary bg-primary/10 font-medium text-foreground'
                                  : 'bg-muted/50 hover:bg-accent text-muted-foreground'
                              }`}
                            >
                              {rows}
                            </button>
                            <DefaultTag visible={isDefault} />
                          </div>
                        );
                      })}
                    </div>
                  </SettingBlock>
                )}
              </Section>
            )}

            {/* Appearance */}
            {sectionVisible.appearance && (
              <Section id="appearance" title="Appearance">
                {v('density compact comfortable spacing rows') && (
                  <SettingBlock
                    label="Density"
                    description="Compact tightens row padding for more on screen at once."
                  >
                    <div className="grid grid-cols-2 gap-2 max-w-sm">
                      {(['comfortable', 'compact'] as Density[]).map((d) => {
                        const isSelected = density === d;
                        const isDefault = d === PREFERENCES_DEFAULTS.density;
                        return (
                          <div key={d} className="flex flex-col">
                            <button
                              onClick={() => setDensity(d)}
                              aria-pressed={isSelected}
                              className={`rounded-lg px-3 py-2 text-sm outline-none capitalize transition-colors ${
                                isSelected
                                  ? 'ring-2 ring-primary bg-primary/10 font-medium text-foreground'
                                  : 'bg-muted/50 hover:bg-accent text-muted-foreground'
                              }`}
                            >
                              {d}
                            </button>
                            <DefaultTag visible={isDefault} />
                          </div>
                        );
                      })}
                    </div>
                  </SettingBlock>
                )}
                {/* Voice speed - hidden */}
                {/* {v('voice speed tts read aloud playback rate') && (
                  <SettingBlock
                    label="Voice speed"
                    description="Playback speed for the Read Aloud feature."
                  >
                    <div className="grid grid-cols-4 gap-2 max-w-sm">
                      {([0.75, 1, 1.25, 1.5] as TTSRate[]).map((rate) => {
                        const isSelected = ttsRate === rate;
                        const isDefault = rate === PREFERENCES_DEFAULTS.ttsRate;
                        return (
                          <div key={rate} className="flex flex-col">
                            <button
                              onClick={() => setTTSRate(rate)}
                              aria-pressed={isSelected}
                              className={`rounded-lg px-3 py-2 text-sm outline-none transition-colors ${
                                isSelected
                                  ? 'ring-2 ring-primary bg-primary/10 font-medium text-foreground'
                                  : 'bg-muted/50 hover:bg-accent text-muted-foreground'
                              }`}
                            >
                              {rate}×
                            </button>
                            <DefaultTag visible={isDefault} />
                          </div>
                        );
                      })}
                    </div>
                  </SettingBlock>
                )} */}
                {/* Voice selector - hidden */}
                {/* {v('voice voice type gender female male voice speaker') && (
                  <VoiceSelector
                    voiceURI={ttsVoiceURI}
                    onChange={setTTSVoiceURI}
                    v={v}
                  />
                )} */}
                {/* Microphone permission - hidden */}
                {/* {v('microphone voice permission browser mic') && (
                  <MicrophonePermissionRow />
                )} */}
                {v('high contrast accessibility bold text sharp') && (
                  <SettingBlock
                    label="High contrast"
                    description="Makes text darker and borders sharper - easier to read in bright environments or for users with low vision."
                  >
                    <Switch
                      checked={highContrast}
                      onCheckedChange={setHighContrast}
                      aria-label="Toggle high contrast mode"
                    />
                  </SettingBlock>
                )}
              </Section>
            )}

            {/* Notifications */}
            {sectionVisible.notifications && (
              <Section id="notifications" title="Notifications">
                <NotificationsPanel
                  notifyOnComplete={notifyOnComplete}
                  setNotifyOnComplete={setNotifyOnComplete}
                  notifySound={notifySound}
                  setNotifySound={setNotifySound}
                  v={v}
                />
              </Section>
            )}

            {/* About */}
            {sectionVisible.about && (
              <Section id="about" title="About">
                <AboutBlock />
              </Section>
            )}

            {/* Reset all */}
            {visibleCount > 0 && !query && (
              <div className="border-t border-border pt-6 mt-2">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      Reset to defaults
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Restore every preference to its original value.
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setResetOpen(true)}
                    disabled={!anyNonDefault}
                    className="gap-1.5"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    Reset
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <AlertDialog open={resetOpen} onOpenChange={setResetOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset all preferences?</AlertDialogTitle>
            <AlertDialogDescription>
              Every setting on this page will return to its default value.
              This can&apos;t be undone - you&apos;ll need to re-apply any
              changes manually.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleReset}>
              Reset preferences
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ─── Helpers ───────────────────────────────────────────────────────────

/** Build a token-AND matcher for the search input. Lowercases both sides
 *  and splits the query on whitespace; a haystack matches when EVERY
 *  token appears somewhere in it. Empty query always matches. */
function makeMatcher(query: string): (haystack: string) => boolean {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return () => true;
  return (haystack: string) => {
    const hay = haystack.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  };
}

/** Reserves a fixed-height line below an option button so all options
 *  in a segmented control stay vertically aligned regardless of which
 *  one is the "default". The tag itself only renders text when `visible`,
 *  but the slot is always present (non-breaking space). */
function DefaultTag({ visible }: { visible: boolean }) {
  return (
    <span
      aria-hidden={!visible}
      className="block text-center mt-1 text-[9px] uppercase tracking-wider text-muted-foreground/60 select-none"
    >
      {visible ? 'default' : ' '}
    </span>
  );
}
function ToneCarousel({
  options,
  value,
  onChange,
  hydrated,
  defaultValue,
}: {
  options: { value: ResponseTone; label: string; description: string }[];
  value: ResponseTone;
  onChange: (tone: ResponseTone) => void;
  hydrated: boolean;
  defaultValue: ResponseTone;
}) {
  // ── Mobile: browse index (separate from selection) ───────────────────────
  // Arrows/dots update browseIndex only. Clicking the card commits via onChange.
  // This way only the card matching `value` ever gets the ring.
  const [browseIndex, setBrowseIndex] = useState(() => {
    const idx = options.findIndex((o) => o.value === value);
    return idx === -1 ? 0 : idx;
  });

  // Keep in sync when the selection changes externally (e.g. Reset to defaults).
  useEffect(() => {
    const idx = options.findIndex((o) => o.value === value);
    if (idx !== -1) setBrowseIndex(idx);
  }, [value, options]);

  const [animState, setAnimState] = useState<{ key: number; dir: 'left' | 'right' | null }>({
    key: 0,
    dir: null,
  });

  // ── Desktop: paginated 4-up ───────────────────────────────────────────────
  // 4 per page so the current 4 tones all fit in one row. If more tones are
  // added later they spill cleanly onto page 2 with the same arrow/dot UI.
  const PER_PAGE = 4;

  const [desktopPage, setDesktopPage] = useState(() => {
    const idx = options.findIndex((o) => o.value === value);
    return idx === -1 ? 0 : Math.floor(idx / PER_PAGE);
  });

  // If the value changes externally (e.g. "Reset to defaults"), jump pages.
  useEffect(() => {
    const idx = options.findIndex((o) => o.value === value);
    if (idx !== -1) setDesktopPage(Math.floor(idx / PER_PAGE));
  }, [value, options]);

  if (options.length === 0) return null;

  // Desktop page math
  const totalPages  = Math.ceil(options.length / PER_PAGE);
  const hasPrevPage = desktopPage > 0;
  const hasNextPage = desktopPage < totalPages - 1;
  const pageOptions = options.slice(desktopPage * PER_PAGE, (desktopPage + 1) * PER_PAGE);
  const emptySlots  = PER_PAGE - pageOptions.length;

  // Mobile single-card math (uses browseIndex, not value)
  const opt              = options[browseIndex] ?? options[0];
  const isBrowsedActive  = hydrated && opt.value === value;
  const canPrev          = browseIndex > 0;
  const canNext          = browseIndex < options.length - 1;

  const navigate = (dir: 'prev' | 'next') => {
    const newIndex = dir === 'prev' ? browseIndex - 1 : browseIndex + 1;
    if (newIndex < 0 || newIndex >= options.length) return;
    setAnimState((s) => ({ key: s.key + 1, dir: dir === 'next' ? 'left' : 'right' }));
    setBrowseIndex(newIndex);
    // Arrows browse only - clicking the card commits the selection.
  };

  const animName =
    animState.dir === 'left'  ? 'toneSlideInRight' :
    animState.dir === 'right' ? 'toneSlideInLeft'  : undefined;

  const arrowCls =
    'self-center shrink-0 w-7 h-7 rounded-full flex items-center justify-center ' +
    'border border-border bg-background hover:bg-accent disabled:invisible ' +
    'transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring ' +
    'focus-visible:ring-offset-2 focus-visible:ring-offset-background';

  const cardCls = (isSelected: boolean) =>
    isSelected && hydrated
      ? 'ring-2 ring-primary bg-primary/10 text-foreground border border-primary/40'
      : 'bg-muted/50 hover:bg-accent text-muted-foreground border border-transparent';

  return (
    <>
      <style>{`
        @keyframes toneSlideInRight {
          from { transform: translateX(18px); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
        @keyframes toneSlideInLeft {
          from { transform: translateX(-18px); opacity: 0; }
          to   { transform: translateX(0);     opacity: 1; }
        }
      `}</style>

      {/* ── Desktop / tablet: 4-per-page paginated grid ── */}
      <div className="hidden md:block">
        <div className="flex items-end gap-2">
          {/* Prev arrow - invisible keeps layout stable */}
          <button
            onClick={() => setDesktopPage((p) => p - 1)}
            disabled={!hasPrevPage}
            aria-label="Previous page"
            className={arrowCls}
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>

          {/* 4-column grid - ghost slots keep card widths even on partial last page */}
          <div className="flex-1 grid grid-cols-4 gap-2">
            {pageOptions.map((o) => {
              const isSelected = value === o.value;
              const isDefault  = o.value === defaultValue;
              return (
                <div key={o.value} className="flex flex-col">
                  <button
                    onClick={() => onChange(o.value)}
                    aria-pressed={isSelected}
                    className={`flex-1 rounded-xl px-4 py-3.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background transition-colors ${cardCls(isSelected)}`}
                  >
                    <span className="text-sm font-semibold block">{o.label}</span>
                    <span className="text-[11px] text-muted-foreground leading-tight block mt-0.5">
                      {o.description}
                    </span>
                  </button>
                  <DefaultTag visible={isDefault} />
                </div>
              );
            })}
            {Array.from({ length: emptySlots }).map((_, i) => (
              <div key={`ghost-${i}`} aria-hidden className="flex flex-col">
                <div className="flex-1" />
                <DefaultTag visible={false} />
              </div>
            ))}
          </div>

          {/* Next arrow */}
          <button
            onClick={() => setDesktopPage((p) => p + 1)}
            disabled={!hasNextPage}
            aria-label="Next page"
            className={arrowCls}
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Page dots - only when multiple pages exist */}
        {totalPages > 1 && (
          <div className="flex justify-center gap-1.5 mt-2.5">
            {Array.from({ length: totalPages }).map((_, i) => (
              <button
                key={i}
                onClick={() => setDesktopPage(i)}
                aria-label={`Page ${i + 1}`}
                aria-pressed={i === desktopPage}
                className={`rounded-full transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  i === desktopPage
                    ? 'w-4 h-1.5 bg-primary'
                    : 'w-1.5 h-1.5 bg-muted-foreground/30 hover:bg-muted-foreground/60'
                }`}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Mobile: one-at-a-time carousel ── */}
      <div className="md:hidden flex flex-col gap-3 max-w-xs">
        {/* overflow-hidden on the flex row clips the slide animation;
            the card wrapper has no overflow-hidden so ring-2 renders on all 4 edges */}
        <div className="flex items-center gap-2 overflow-hidden">
          <button
            onClick={() => navigate('prev')}
            disabled={!canPrev}
            aria-label="Previous tone"
            className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center border border-border bg-background hover:bg-accent disabled:opacity-25 disabled:cursor-not-allowed transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>

          {/* p-[2px] gives the ring-2 box-shadow exactly 2px clearance on all sides */}
          <div className="flex-1 p-[2px]">
            <div
              key={animState.key}
              style={animName ? { animation: `${animName} 0.22s ease-out` } : undefined}
            >
              <button
                onClick={() => onChange(opt.value)}
                aria-pressed={isBrowsedActive}
                className={`w-full rounded-xl px-4 py-3.5 text-left outline-none transition-colors ${
                  isBrowsedActive
                    ? 'ring-2 ring-primary bg-primary/10 text-foreground'
                    : 'bg-muted/50 text-muted-foreground'
                }`}
              >
                <span className="text-sm font-semibold block">{opt.label}</span>
                <span className="text-[11px] text-muted-foreground leading-tight block mt-0.5">
                  {opt.description}
                </span>
              </button>
              <DefaultTag visible={opt.value === defaultValue} />
            </div>
          </div>

          <button
            onClick={() => navigate('next')}
            disabled={!canNext}
            aria-label="Next tone"
            className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center border border-border bg-background hover:bg-accent disabled:opacity-25 disabled:cursor-not-allowed transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="flex justify-center gap-1.5">
          {options.map((o, i) => (
            <button
              key={o.value}
              onClick={() => {
                if (i === browseIndex) return;
                setAnimState((s) => ({
                  key: s.key + 1,
                  dir: i > browseIndex ? 'left' : 'right',
                }));
                setBrowseIndex(i);
              }}
              aria-label={`Browse ${o.label}`}
              aria-pressed={i === browseIndex}
              className={`rounded-full transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                i === browseIndex
                  ? 'w-4 h-1.5 bg-primary'
                  : 'w-1.5 h-1.5 bg-muted-foreground/30 hover:bg-muted-foreground/60'
              }`}
            />
          ))}
        </div>
      </div>
    </>
  );
}
function Section({
  id,
  title,
  children,
}: {
  id: SectionId;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="scroll-mt-4">
      <h2
        id={`section-${id}`}
        className="text-base font-semibold tracking-tight mb-3 scroll-mt-4"
      >
        {title}
      </h2>
      <div>{children}</div>
    </section>
  );
}

function SettingBlock({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="mt-5 first:mt-0">
      <Label className="text-sm font-medium text-foreground">{label}</Label>
      {description && (
        <p className="text-[11px] text-muted-foreground mt-0.5 mb-2">
          {description}
        </p>
      )}
      {children}
    </div>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
  isDefault,
}: {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  isDefault?: boolean;
}) {
  return (
    <div
      className={`flex items-center justify-between gap-4 py-[var(--density-pad-y)] border-b border-border/60 last:border-b-0 ${
        disabled ? 'opacity-50' : ''
      }`}
    >
      <div className="min-w-0">
        <p className="text-sm text-foreground flex items-center gap-2">
          {label}
          {isDefault === false && (
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground/70 font-medium">
              changed
            </span>
          )}
        </p>
        <p className="text-[11px] text-muted-foreground">{description}</p>
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        aria-label={label}
      />
    </div>
  );
}

function VoiceSelector({
  voiceURI,
  onChange,
  v,
}: {
  voiceURI: string;
  onChange: (uri: string) => void;
  v: (keywords: string) => boolean;
}) {
  const voices = useAvailableVoices();

  if (!v('voice type gender female male speaker') || voices.length === 0) return null;

  const females = voices.filter((vx) => vx.gender === 'female');
  const males = voices.filter((vx) => vx.gender === 'male');

  return (
    <SettingBlock
      label="Voice"
      description="Choose the voice for reading responses aloud."
    >
      <select
        value={voiceURI}
        onChange={(e) => onChange(e.target.value)}
        className="w-full max-w-sm rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
      >
        <option value="">Auto (recommended)</option>
        {females.length > 0 && (
          <optgroup label="Female">
            {females.map((vx) => (
              <option key={vx.voice.voiceURI} value={vx.voice.voiceURI}>
                {vx.label}
              </option>
            ))}
          </optgroup>
        )}
        {males.length > 0 && (
          <optgroup label="Male">
            {males.map((vx) => (
              <option key={vx.voice.voiceURI} value={vx.voice.voiceURI}>
                {vx.label}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </SettingBlock>
  );
}

function MicrophonePermissionRow() {
  type MicState = 'unknown' | 'prompt' | 'granted' | 'denied' | 'unsupported';
  const [state, setState] = useState<MicState>('unknown');

  useEffect(() => {
    if (typeof navigator === 'undefined' || !navigator.permissions) {
      setState('unsupported');
      return;
    }
    const check = () => {
      navigator.permissions
        .query({ name: 'microphone' as PermissionName })
        .then((result) => {
          setState(result.state as MicState);
          result.onchange = () => setState(result.state as MicState);
        })
        .catch(() => setState('unsupported'));
    };
    check();
    const onFocus = () => check();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, []);

  const handleEnable = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      setState('granted');
    } catch {
      setState('denied');
    }
  };

  const httpsRequired = typeof window !== 'undefined' && !window.isSecureContext;

  if (state === 'unsupported') return null;

  return (
    <div className="flex items-center justify-between gap-4 py-[var(--density-pad-y)]">
      <div className="min-w-0">
        <p className="text-sm text-foreground">Microphone permission</p>
        <p className="text-[11px] text-muted-foreground">
          {httpsRequired && 'Voice input requires a secure connection (HTTPS). It will work automatically once the app is deployed.'}
          {!httpsRequired && state === 'granted' && 'Allowed - voice input is ready to use.'}
          {!httpsRequired && state === 'prompt' && 'Not yet granted - click Enable to allow microphone access.'}
          {!httpsRequired && state === 'denied' && 'Blocked - open your browser permissions for this site and set Microphone to Allow.'}
          {!httpsRequired && state === 'unknown' && 'Checking…'}
        </p>
      </div>
      {state === 'prompt' && (
        <Button size="sm" onClick={handleEnable} className="shrink-0">
          Enable
        </Button>
      )}
      {state === 'granted' && (
        <span className="shrink-0 inline-flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" aria-hidden />
          Allowed
        </span>
      )}
      {state === 'denied' && (
        <span className="shrink-0 text-[11px] text-muted-foreground">Blocked</span>
      )}
    </div>
  );
}

function NotificationsPanel({
  notifyOnComplete,
  setNotifyOnComplete,
  notifySound,
  setNotifySound,
  v,
}: {
  notifyOnComplete: 'when-hidden' | 'off';
  setNotifyOnComplete: (val: 'when-hidden' | 'off') => void;
  notifySound: boolean;
  setNotifySound: (val: boolean) => void;
  v: (keywords: string) => boolean;
}) {
  // Start in a SSR-stable state and resolve the real permission post-mount.
  // Initialising directly from `notificationsSupported()` would diverge
  // between server (no window) and client (window present), causing a
  // hydration mismatch on the description text.
  const [permission, setPermission] = useState<NotificationPermissionState>('default');

  useEffect(() => {
    if (!notificationsSupported()) {
      setPermission('unsupported');
      return;
    }
    setPermission(getPermission());
    // Re-read on focus in case the user changed it from another tab or
    // browser site settings.
    const onFocus = () => setPermission(getPermission());
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, []);

  const handleEnable = async () => {
    const next = await requestPermission();
    setPermission(next);
  };

  const enabled = notifyOnComplete === 'when-hidden';

  return (
    <div className="space-y-1">
      {v('notify when answers finish notifications stream completion') && (
        <ToggleRow
          label="Notify when answers finish"
          description="Pings you when a stream completes and you're not on that chat."
          checked={enabled}
          onCheckedChange={(val) =>
            setNotifyOnComplete(val ? 'when-hidden' : 'off')
          }
          isDefault={
            (enabled ? 'when-hidden' : 'off') ===
            PREFERENCES_DEFAULTS.notifyOnComplete
          }
        />
      )}
      {v('play sound ping audio notifications') && (
        <ToggleRow
          label="Play a sound"
          description={
            enabled
              ? 'Soft ping alongside notifications.'
              : 'Enable notifications above to use this.'
          }
          checked={enabled && notifySound}
          onCheckedChange={setNotifySound}
          disabled={!enabled}
          isDefault={notifySound === PREFERENCES_DEFAULTS.notifySound}
        />
      )}
      {v('browser permission notifications') && (
        <div className="flex items-center justify-between gap-4 py-[var(--density-pad-y)]">
          <div className="min-w-0">
            <p className="text-sm text-foreground">Browser permission</p>
            <p className="text-[11px] text-muted-foreground">
              {permission === 'granted' &&
                'Allowed - to revoke, click the lock/site-info icon in the address bar.'}
              {permission === 'default' &&
                'Not yet granted - click Enable to allow.'}
              {permission === 'denied' &&
                "Blocked - change in your browser's site settings to re-enable."}
              {permission === 'unsupported' &&
                "Your browser doesn't support desktop notifications."}
            </p>
          </div>
          {permission === 'default' && (
            <Button size="sm" onClick={handleEnable} className="shrink-0">
              Enable
            </Button>
          )}
          {permission === 'granted' && (
            <span className="shrink-0 inline-flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400">
              <span
                className="w-1.5 h-1.5 rounded-full bg-emerald-500"
                aria-hidden
              />
              Allowed
            </span>
          )}
          {permission === 'denied' && (
            <span className="shrink-0 text-[11px] text-muted-foreground">
              Blocked
            </span>
          )}
        </div>
      )}
    </div>
  );
}

const VERSION_QUOTES = [
  '"We are the architects of the future." - CEO',
  '"The numbers don\'t lie. But they do tell stories." - CFO',
  '"Revenue is a team sport." - CRO',
  '"People first, always." - CPO',
  '"Operational excellence is not optional." - COO',
  '"Strategy is nothing without execution." - Chief of Staff',
  '"The cloud is just someone else\'s computer. Ours runs better." - President, DW/Cloud',
  '"Ship it. Then make it beautiful." - President, Apps & DE',
  '"Process is poetry in disguise." - EVP, BPS',
  '"Solutions aren\'t found - they\'re engineered." - EVP, Industry Solutions',
  '"Growth is a mindset." - VP, Corp Dev',
  '"We skipped v1. Too many features, not enough bugs." - QA Team',
];

function AboutBlock() {
  const [hovered, setHovered] = useState(false);
  const [quote] = useState(
    () => VERSION_QUOTES[Math.floor(Math.random() * VERSION_QUOTES.length)],
  );
  return (
    <div className="text-xs text-muted-foreground space-y-1">
      <p>MTI Brain - AI-powered decision intelligence</p>
      <p
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="text-muted-foreground/60 hover:text-muted-foreground transition-colors cursor-default"
      >
        {hovered ? <span className="italic">{quote}</span> : 'Version 2026.05.0'}
      </p>
    </div>
  );
}

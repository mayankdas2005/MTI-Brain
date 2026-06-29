'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
  type RefObject,
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
  BookText,
  Plus,
  Trash2,
  Loader2,
  Database,
  ShieldAlert,
  Copy,
  Check,
  Ban,
} from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { highlightQueryInText } from '@/lib/utils/highlight';
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
  type ThinkingPlacement,
} from '@/lib/store/preferences';
import { useInstructionsStore } from '@/lib/store/instructions';
import type { UserInstruction } from '@/lib/api/instructions';
import { listFeedbackHistory, getFeedbackPatterns, type FeedbackHistoryPage, type FeedbackPattern } from '@/lib/api/feedback-history';
import { listQueryPatterns, listAntiPatterns, listEnabledQueryPatterns, listEnabledAntiPatterns, setQueryPatternEnabled, setAntiPatternEnabled, deleteQueryPattern, deleteAntiPattern, type PatternRecord } from '@/lib/api/pattern-library';
import { getStoredUser } from '@/lib/auth';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
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
  | 'instructions'
  | 'query-patterns'
  | 'about';

interface SectionDef {
  id: SectionId;
  label: string;
  icon: ComponentType<{ className?: string }>;
  adminOnly?: boolean;
}

const SECTIONS: SectionDef[] = [
  { id: 'response-style', label: 'Response style', icon: MessageSquare },
  { id: 'instructions', label: 'Instructions', icon: BookText, adminOnly: true },
  { id: 'display', label: 'Display', icon: Eye },
  { id: 'appearance', label: 'Appearance', icon: Palette },
  { id: 'notifications', label: 'Notifications', icon: Bell },
  { id: 'query-patterns', label: 'Pattern library', icon: Database },
  { id: 'about', label: 'About', icon: Info },
];

const TONE_OPTIONS: { value: ResponseTone; label: string; description: string }[] = [
  { value: 'analyst', label: 'Analyst', description: 'Data-driven, detailed breakdowns' },
  { value: 'manager', label: 'Manager', description: 'Actionable insights with context' },
  { value: 'director', label: 'Director', description: 'Strategic summaries with key metrics' },
  { value: 'executive', label: 'Executive', description: 'High-level, decision-ready answers' },
];

const ROW_OPTIONS = [50, 100, 200, 500];

export default function SettingsPage() {
  const [isAdmin, setIsAdmin] = useState(false);
  useEffect(() => {
    const user = getStoredUser();
    setIsAdmin((user?.groups ?? []).includes('admin'));
  }, []);

  const visibleSections = useMemo(
    () => SECTIONS.filter((s) => !s.adminOnly || isAdmin),
    [isAdmin],
  );

  const responseTone = usePreferencesStore((s) => s.responseTone);
  const setResponseTone = usePreferencesStore((s) => s.setResponseTone);
  const showSQL = usePreferencesStore((s) => s.showSQL);
  const setShowSQL = usePreferencesStore((s) => s.setShowSQL);
  const showData = usePreferencesStore((s) => s.showData);
  const setShowData = usePreferencesStore((s) => s.setShowData);
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
  const highContrast = usePreferencesStore((s) => s.highContrast ?? false);
  const setHighContrast = usePreferencesStore((s) => s.setHighContrast);
  const thinkingPlacement = usePreferencesStore((s) => s.thinkingPlacement);
  const setThinkingPlacement = usePreferencesStore((s) => s.setThinkingPlacement);
  const resetToDefaults = usePreferencesStore((s) => s.resetToDefaults);
  const hydrated = usePreferencesStore((s) => s.hydrated);

  const [query, setQuery] = useState('');
  const [activeSection, setActiveSection] = useState<SectionId>('response-style');
  const [resetOpen, setResetOpen] = useState(false);

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
      density !== PREFERENCES_DEFAULTS.density ||
      thinkingPlacement !== PREFERENCES_DEFAULTS.thinkingPlacement
    );
  }, [
    responseTone, showSQL, autoShowCharts, showFollowUps, showReasoning,
    defaultDataView, maxResultRows, notifyOnComplete, notifySound, density,
    thinkingPlacement,
  ]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const hash = window.location.hash.replace(/^#/, '') as SectionId;
    if (visibleSections.some((s) => s.id === hash)) setActiveSection(hash);
  }, [visibleSections]);

  const matches = useMemo(() => makeMatcher(query), [query]);
  const v = (keywords: string) => matches(keywords);

  const sectionVisible: Record<SectionId, boolean> = {
    'response-style': TONE_OPTIONS.some((o) =>
      matches(`response style tone ${o.label} ${o.description}`),
    ),
    instructions: isAdmin && v('instructions standing rules claude apply always'),
    display:
      v('show sql queries display') ||
      v('auto show charts visualizations') ||
      v('follow-up suggestions follow up') ||
      v('show reasoning thinking process') ||
      v('thinking placement inline sidebar position') ||
      v('default data view sql table') ||
      v('max result rows per query'),
    appearance:
      v('density compact comfortable spacing rows') ||
      v('high contrast accessibility bold text sharp'),
    notifications:
      v('notify when answers finish notifications stream completion') ||
      v('play sound ping audio notifications') ||
      v('browser permission notifications'),
    'query-patterns':
      v('query patterns antipatterns anti-patterns sql patterns') ||
      v('pattern library training enabled'),
    about: v('about version mti brain'),
  };

  const visibleCount = Object.values(sectionVisible).filter(Boolean).length;
  const isSearching = query.trim().length > 0;

  const navigateTo = (id: SectionId) => {
    setActiveSection(id);
    setQuery('');
    history.replaceState(null, '', `#${id}`);
  };

  const handleReset = () => {
    resetToDefaults();
    setResetOpen(false);
    toast.success('Preferences reset to defaults');
  };

  const sharedProps = {
    showSQL, setShowSQL,
    showData, setShowData,
    autoShowCharts, setAutoShowCharts,
    showFollowUps, setShowFollowUps,
    showReasoning, setShowReasoning,
    thinkingPlacement, setThinkingPlacement,
    defaultDataView, setDefaultDataView,
    maxResultRows, setMaxResultRows,
    density, setDensity,
    highContrast, setHighContrast,
    notifyOnComplete, setNotifyOnComplete,
    notifySound, setNotifySound,
    responseTone, setResponseTone,
    hydrated,
  };

  return (
    <div className="h-full flex overflow-hidden">
      {/* Left nav */}
      <nav
        aria-label="Settings sections"
        className="w-52 shrink-0 flex flex-col border-r border-border bg-muted/20"
      >
        {/* Search */}
        <div className="px-3 pt-3 pb-2 shrink-0">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search..."
              aria-label="Search settings"
              className="pl-8 pr-6 h-9 text-sm bg-background border-border/60"
            />
            {query && (
              <button
                onClick={() => setQuery('')}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
        </div>

        {/* Nav items */}
        <ul className="flex-1 px-2 pt-1 space-y-0.5 overflow-y-auto">
          {visibleSections.map((s) => {
            const Icon = s.icon;
            const isActive = !isSearching && activeSection === s.id;
            const isDimmed = isSearching && !sectionVisible[s.id];
            return (
              <li key={s.id}>
                <button
                  onClick={() => navigateTo(s.id)}
                  aria-current={isActive ? 'true' : undefined}
                  className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-md text-sm transition-colors text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 ${
                    isActive
                      ? 'bg-muted/60 text-foreground font-medium'
                      : isDimmed
                        ? 'text-muted-foreground/35 cursor-default'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted/40'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5 shrink-0" />
                  <span className="truncate">{s.label}</span>
                </button>
              </li>
            );
          })}
        </ul>

        {/* Reset — mirrors sidebar user-button structure exactly so border-t aligns at all densities:
             same h-8 w-8 icon container + 2-line text = identical button height as the avatar+name/email button */}
        <div
          className="px-2 shrink-0 border-t border-border/60 min-h-[3.5rem] flex flex-col justify-center"
          style={{ paddingTop: 'var(--density-pad-y)', paddingBottom: 'var(--density-pad-y)' }}
        >
          <button
            onClick={() => setResetOpen(true)}
            disabled={!anyNonDefault}
            className="w-full flex items-center gap-2.5 rounded-lg px-2 py-[var(--density-pad-y-tight)] min-h-[48px] md:min-h-0 transition-colors text-left outline-none hover:bg-muted/40 disabled:opacity-35 disabled:pointer-events-none"
          >
            <div className="h-8 w-8 shrink-0 flex items-center justify-center rounded-full bg-muted/50">
              <RotateCcw className="w-3.5 h-3.5 text-muted-foreground" />
            </div>
            <div className="flex-1 text-left min-w-0">
              <p className="text-sm font-medium text-foreground truncate">Reset to defaults</p>
              <p className="text-[11px] text-muted-foreground/50 truncate">Restore all preferences</p>
            </div>
          </button>
        </div>
      </nav>

      {/* Right panel */}
      <div className={`flex-1 ${!isSearching && activeSection === 'query-patterns' ? 'overflow-hidden flex flex-col' : 'overflow-y-auto'}`}>
        {isSearching ? (
          <SearchResults
            visibleCount={visibleCount}
            query={query}
            setQuery={setQuery}
            sectionVisible={sectionVisible}
            matches={matches}
            v={v}
            {...sharedProps}
          />
        ) : (
          <SectionPanel
            activeSection={activeSection}
            v={() => true}
            {...sharedProps}
          />
        )}
      </div>

      <AlertDialog open={resetOpen} onOpenChange={setResetOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset all preferences?</AlertDialogTitle>
            <AlertDialogDescription>
              Every setting will return to its default value. This can&apos;t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleReset}>Reset preferences</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ─── Section panel (active section only) ───────────────────────────────

function SectionPanel({
  activeSection, v,
  responseTone, setResponseTone, hydrated,
  showSQL, setShowSQL,
  showData, setShowData,
  autoShowCharts, setAutoShowCharts,
  showFollowUps, setShowFollowUps,
  showReasoning, setShowReasoning,
  thinkingPlacement, setThinkingPlacement,
  defaultDataView, setDefaultDataView,
  maxResultRows, setMaxResultRows,
  density, setDensity,
  highContrast, setHighContrast,
  notifyOnComplete, setNotifyOnComplete,
  notifySound, setNotifySound,
}: {
  activeSection: SectionId;
  v: (k: string) => boolean;
  responseTone: ResponseTone; setResponseTone: (v: ResponseTone) => void; hydrated: boolean;
  showSQL: boolean; setShowSQL: (v: boolean) => void;
  showData: boolean; setShowData: (v: boolean) => void;
  autoShowCharts: boolean; setAutoShowCharts: (v: boolean) => void;
  showFollowUps: boolean; setShowFollowUps: (v: boolean) => void;
  showReasoning: boolean; setShowReasoning: (v: boolean) => void;
  thinkingPlacement: ThinkingPlacement; setThinkingPlacement: (v: ThinkingPlacement) => void;
  defaultDataView: DefaultDataView; setDefaultDataView: (v: DefaultDataView) => void;
  maxResultRows: number; setMaxResultRows: (v: number) => void;
  density: Density; setDensity: (v: Density) => void;
  highContrast: boolean; setHighContrast: (v: boolean) => void;
  notifyOnComplete: 'when-hidden' | 'off'; setNotifyOnComplete: (v: 'when-hidden' | 'off') => void;
  notifySound: boolean; setNotifySound: (v: boolean) => void;
}) {
  const isQP = activeSection === 'query-patterns';
  const isAdminUser = (getStoredUser()?.groups ?? []).includes('admin');

  const titles: Record<SectionId, { title: string; description: string }> = {
    'response-style': { title: 'Response style', description: 'How MTI Brain frames its answers.' },
    instructions: { title: 'Instructions', description: 'Standing rules applied to every response across all chats.' },
    display: { title: 'Display', description: 'Control what\'s shown alongside responses.' },
    appearance: { title: 'Appearance', description: 'Adjust layout density and accessibility.' },
    notifications: { title: 'Notifications', description: 'Configure how MTI Brain alerts you.' },
    'query-patterns': {
      title: 'Pattern library',
      description: isAdminUser
        ? 'Used to enable, disable or delete query patterns and anti-patterns.'
        : 'Query patterns and anti-patterns enabled for training.',
    },
    about: { title: 'About', description: '' },
  };

  const { title, description } = titles[activeSection];

  return (
    <div className={isQP ? 'h-full flex flex-col px-8 pt-5 overflow-hidden' : 'px-8 pt-5 pb-8'}>
      {/* Section heading */}
      <div className="mb-6 pb-4 border-b border-border shrink-0">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {description && (
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        )}
      </div>

      {/* Section content */}
      <div className={isQP ? 'flex-1 min-h-0 flex flex-col overflow-hidden' : ''}>
        {activeSection === 'response-style' && (
          <ToneGrid
            options={TONE_OPTIONS}
            value={responseTone}
            onChange={setResponseTone}
            hydrated={hydrated}
            defaultValue={PREFERENCES_DEFAULTS.responseTone}
          />
        )}
        {activeSection === 'display' && (
          <DisplayContent
            v={v}
            showSQL={showSQL} setShowSQL={setShowSQL}
            showData={showData} setShowData={setShowData}
            autoShowCharts={autoShowCharts} setAutoShowCharts={setAutoShowCharts}
            showFollowUps={showFollowUps} setShowFollowUps={setShowFollowUps}
            showReasoning={showReasoning} setShowReasoning={setShowReasoning}
            thinkingPlacement={thinkingPlacement} setThinkingPlacement={setThinkingPlacement}
            defaultDataView={defaultDataView} setDefaultDataView={setDefaultDataView}
            maxResultRows={maxResultRows} setMaxResultRows={setMaxResultRows}
          />
        )}
        {activeSection === 'appearance' && (
          <AppearanceContent
            v={v}
            density={density} setDensity={setDensity}
            highContrast={highContrast} setHighContrast={setHighContrast}
          />
        )}
        {activeSection === 'notifications' && (
          <NotificationsPanel
            notifyOnComplete={notifyOnComplete} setNotifyOnComplete={setNotifyOnComplete}
            notifySound={notifySound} setNotifySound={setNotifySound}
            v={v}
          />
        )}
        {activeSection === 'instructions' && <InstructionsPanel />}
        {activeSection === 'query-patterns' && <QueryPatternsPanel />}
        {activeSection === 'about' && <AboutBlock />}
      </div>
    </div>
  );
}

// ─── Search results view ─────────────────────────────────────────────────

function SearchResults({
  visibleCount, query, setQuery, sectionVisible, matches, v,
  responseTone, setResponseTone, hydrated,
  showSQL, setShowSQL,
  showData, setShowData,
  autoShowCharts, setAutoShowCharts,
  showFollowUps, setShowFollowUps,
  showReasoning, setShowReasoning,
  thinkingPlacement, setThinkingPlacement,
  defaultDataView, setDefaultDataView,
  maxResultRows, setMaxResultRows,
  density, setDensity,
  highContrast, setHighContrast,
  notifyOnComplete, setNotifyOnComplete,
  notifySound, setNotifySound,
}: {
  visibleCount: number; query: string; setQuery: (v: string) => void;
  sectionVisible: Record<SectionId, boolean>;
  matches: (h: string) => boolean; v: (k: string) => boolean;
  responseTone: ResponseTone; setResponseTone: (v: ResponseTone) => void; hydrated: boolean;
  showSQL: boolean; setShowSQL: (v: boolean) => void;
  showData: boolean; setShowData: (v: boolean) => void;
  autoShowCharts: boolean; setAutoShowCharts: (v: boolean) => void;
  showFollowUps: boolean; setShowFollowUps: (v: boolean) => void;
  showReasoning: boolean; setShowReasoning: (v: boolean) => void;
  thinkingPlacement: ThinkingPlacement; setThinkingPlacement: (v: ThinkingPlacement) => void;
  defaultDataView: DefaultDataView; setDefaultDataView: (v: DefaultDataView) => void;
  maxResultRows: number; setMaxResultRows: (v: number) => void;
  density: Density; setDensity: (v: Density) => void;
  highContrast: boolean; setHighContrast: (v: boolean) => void;
  notifyOnComplete: 'when-hidden' | 'off'; setNotifyOnComplete: (v: 'when-hidden' | 'off') => void;
  notifySound: boolean; setNotifySound: (v: boolean) => void;
}) {
  if (visibleCount === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-48 text-center px-8">
        <p className="text-sm text-muted-foreground">No settings match &quot;{query}&quot;</p>
        <Button variant="ghost" size="sm" onClick={() => setQuery('')} className="mt-2">
          Clear search
        </Button>
      </div>
    );
  }

  return (
    <div className="px-8 pt-5 pb-8 space-y-8">
      {sectionVisible['response-style'] && (
        <SearchGroup title="Response style">
          <ToneGrid
            options={TONE_OPTIONS.filter((o) =>
              matches(`response style tone ${o.label} ${o.description}`),
            )}
            value={responseTone}
            onChange={setResponseTone}
            hydrated={hydrated}
            defaultValue={PREFERENCES_DEFAULTS.responseTone}
          />
        </SearchGroup>
      )}
      {sectionVisible.display && (
        <SearchGroup title="Display">
          <DisplayContent
            v={v}
            showSQL={showSQL} setShowSQL={setShowSQL}
            showData={showData} setShowData={setShowData}
            autoShowCharts={autoShowCharts} setAutoShowCharts={setAutoShowCharts}
            showFollowUps={showFollowUps} setShowFollowUps={setShowFollowUps}
            showReasoning={showReasoning} setShowReasoning={setShowReasoning}
            thinkingPlacement={thinkingPlacement} setThinkingPlacement={setThinkingPlacement}
            defaultDataView={defaultDataView} setDefaultDataView={setDefaultDataView}
            maxResultRows={maxResultRows} setMaxResultRows={setMaxResultRows}
          />
        </SearchGroup>
      )}
      {sectionVisible.appearance && (
        <SearchGroup title="Appearance">
          <AppearanceContent
            v={v}
            density={density} setDensity={setDensity}
            highContrast={highContrast} setHighContrast={setHighContrast}
          />
        </SearchGroup>
      )}
      {sectionVisible.notifications && (
        <SearchGroup title="Notifications">
          <NotificationsPanel
            notifyOnComplete={notifyOnComplete} setNotifyOnComplete={setNotifyOnComplete}
            notifySound={notifySound} setNotifySound={setNotifySound}
            v={v}
          />
        </SearchGroup>
      )}
      {sectionVisible.instructions && (
        <SearchGroup title="Instructions">
          <InstructionsPanel />
        </SearchGroup>
      )}
      {sectionVisible['query-patterns'] && (
        <SearchGroup title="Query patterns">
          <QueryPatternsPanel />
        </SearchGroup>
      )}
      {sectionVisible.about && (
        <SearchGroup title="About">
          <AboutBlock />
        </SearchGroup>
      )}
    </div>
  );
}

function SearchGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest font-semibold text-muted-foreground/50 mb-3">
        {title}
      </p>
      {children}
    </div>
  );
}

// ─── Section content components ──────────────────────────────────────────

const TONE_EXAMPLES: Record<string, string> = {
  analyst: 'Revenue declined 8.3% MoM. Primary driver: APAC segment — down $1.2M. SQL attached.',
  manager: 'Revenue is down 8% from last month, mostly from APAC. Action needed on Q3 targets.',
  director: 'Q3 revenue is tracking below plan. APAC is the main headwind — worth a closer look.',
  executive: "We're behind on Q3. APAC needs attention.",
};

function ToneGrid({
  options, value, onChange, hydrated, defaultValue,
}: {
  options: { value: ResponseTone; label: string; description: string }[];
  value: ResponseTone;
  onChange: (v: ResponseTone) => void;
  hydrated: boolean;
  defaultValue: ResponseTone;
}) {
  if (options.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-4">
      {options.map((o) => {
        const isSelected = value === o.value && hydrated;
        const isDefault = o.value === defaultValue;
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            aria-pressed={isSelected}
            className={`w-74 shrink-0 grow-0 relative rounded-2xl p-5 text-left transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
              isSelected
                ? 'bg-primary shadow-sm'
                : 'bg-muted/25 border border-border hover:bg-muted/40 hover:border-border/80'
            }`}
          >
            {isDefault && (
              <span className={`absolute top-3.5 right-3.5 text-[10px] uppercase tracking-widest font-semibold ${isSelected ? 'text-primary-foreground/60' : 'text-muted-foreground/40'}`}>
                Default
              </span>
            )}
            <span className={`text-4xl font-black block mb-4 leading-none select-none ${isSelected ? 'text-primary-foreground/20' : 'text-foreground/8'}`}>
              {o.label[0]}
            </span>
            <span className={`text-sm font-semibold block ${isSelected ? 'text-primary-foreground' : 'text-foreground'}`}>
              {o.label}
            </span>
            <span className={`text-xs block mt-1 leading-snug ${isSelected ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>
              {o.description}
            </span>
            {TONE_EXAMPLES[o.value] && (
              <span className={`text-[11px] block mt-4 pt-3.5 border-t leading-relaxed italic ${isSelected ? 'border-primary-foreground/20 text-primary-foreground/55' : 'border-border/40 text-muted-foreground/40'}`}>
                &ldquo;{TONE_EXAMPLES[o.value]}&rdquo;
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function DisplayContent({
  v,
  showSQL, setShowSQL,
  showData, setShowData,
  autoShowCharts, setAutoShowCharts,
  showFollowUps, setShowFollowUps,
  showReasoning, setShowReasoning,
  thinkingPlacement, setThinkingPlacement,
  defaultDataView, setDefaultDataView,
  maxResultRows, setMaxResultRows,
}: {
  v: (k: string) => boolean;
  showSQL: boolean; setShowSQL: (v: boolean) => void;
  showData: boolean; setShowData: (v: boolean) => void;
  autoShowCharts: boolean; setAutoShowCharts: (v: boolean) => void;
  showFollowUps: boolean; setShowFollowUps: (v: boolean) => void;
  showReasoning: boolean; setShowReasoning: (v: boolean) => void;
  thinkingPlacement: ThinkingPlacement; setThinkingPlacement: (v: ThinkingPlacement) => void;
  defaultDataView: DefaultDataView; setDefaultDataView: (v: DefaultDataView) => void;
  maxResultRows: number; setMaxResultRows: (v: number) => void;
}) {
  const visibilitySettings = [
    { key: 'show sql queries display', label: 'Show SQL', desc: 'Display the generated SQL query alongside each result.', checked: showSQL, set: setShowSQL, isDefault: showSQL === PREFERENCES_DEFAULTS.showSQL },
    { key: 'show data table results display', label: 'Show data table', desc: 'Display the raw data table alongside results.', checked: showData, set: setShowData, isDefault: showData === PREFERENCES_DEFAULTS.showData },
    { key: 'auto show charts visualizations', label: 'Auto-show charts', desc: 'Automatically render visualizations when the result fits a chart.', checked: autoShowCharts, set: setAutoShowCharts, isDefault: autoShowCharts === PREFERENCES_DEFAULTS.autoShowCharts },
    { key: 'follow-up suggestions follow up', label: 'Follow-up suggestions', desc: 'Show AI-suggested follow-up questions after each response.', checked: showFollowUps, set: setShowFollowUps, isDefault: showFollowUps === PREFERENCES_DEFAULTS.showFollowUps },
    { key: 'show reasoning thinking process', label: 'Show reasoning', desc: 'Display the thinking-process panel for each query.', checked: showReasoning, set: setShowReasoning, isDefault: showReasoning === PREFERENCES_DEFAULTS.showReasoning },
  ];
  return (
    <div className="space-y-10">
      {/* Visibility — grid of toggle cards */}
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground/35 mb-4">Visibility</p>
        <div className="flex flex-wrap gap-3">
          {visibilitySettings.map(({ key, label, desc, checked, set, isDefault }) =>
            v(key) ? (
              <div key={key} className="w-74 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5 flex flex-col gap-4 hover:bg-muted/30 transition-colors">
                <div className="flex-1">
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <p className="text-sm font-medium text-foreground leading-snug">{label}</p>
                    {isDefault === false && (
                      <span className="shrink-0 text-[10px] uppercase tracking-widest font-semibold text-primary/60 mt-0.5">changed</span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed">{desc}</p>
                </div>
                <div className="flex justify-center">
                  <SegmentControl checked={checked} onCheckedChange={set} />
                </div>
              </div>
            ) : null
          )}
        </div>
      </div>

      {/* Layout & Limits — option cards */}
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground/35 mb-4">Layout & limits</p>
        <div className="flex flex-wrap gap-4">
          {v('thinking placement inline sidebar position') && (
            <div className="w-74 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5">
              <div className="flex items-start justify-between gap-2 mb-0.5">
                <p className="text-sm font-medium text-foreground">Thinking panel</p>
                <span className={`shrink-0 text-[10px] uppercase tracking-widest font-semibold mt-0.5 ${thinkingPlacement === PREFERENCES_DEFAULTS.thinkingPlacement ? 'text-muted-foreground/40' : 'text-primary/60'}`}>
                  {thinkingPlacement === PREFERENCES_DEFAULTS.thinkingPlacement ? 'Default' : 'Changed'}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mb-4">Where reasoning steps are shown.</p>
              <div className="grid grid-cols-2 gap-2">
                {([
                  { value: 'inline' as ThinkingPlacement, label: 'Inline', sub: 'In the stream' },
                  { value: 'sidebar' as ThinkingPlacement, label: 'Side panel', sub: 'Beside chat' },
                ]).map((opt) => {
                  const sel = thinkingPlacement === opt.value;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => setThinkingPlacement(opt.value)}
                      className={`rounded-xl p-3 text-left text-xs transition-all outline-none focus-visible:ring-2 focus-visible:ring-ring ${sel ? 'bg-primary text-primary-foreground font-semibold' : 'bg-muted/40 hover:bg-muted/60 text-foreground border border-border/50 hover:border-border'}`}
                    >
                      <span className="font-semibold block">{opt.label}</span>
                      <span className={`mt-0.5 block text-[11px] ${sel ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>{opt.sub}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {v('default data view sql table') && (
            <div className="w-74 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5">
              <div className="flex items-start justify-between gap-2 mb-0.5">
                <p className="text-sm font-medium text-foreground">Default data view</p>
                <span className={`shrink-0 text-[10px] uppercase tracking-widest font-semibold mt-0.5 ${defaultDataView === PREFERENCES_DEFAULTS.defaultDataView ? 'text-muted-foreground/40' : 'text-primary/60'}`}>
                  {defaultDataView === PREFERENCES_DEFAULTS.defaultDataView ? 'Default' : 'Changed'}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mb-4">Which tab opens first when data arrives.</p>
              <div className="grid grid-cols-2 gap-2">
                {(['sql', 'table'] as DefaultDataView[]).map((view) => {
                  const sel = defaultDataView === view;
                  return (
                    <button
                      key={view}
                      onClick={() => setDefaultDataView(view)}
                      className={`rounded-xl py-3 text-sm font-medium text-center transition-all outline-none focus-visible:ring-2 focus-visible:ring-ring ${sel ? 'bg-primary text-primary-foreground font-semibold' : 'bg-muted/40 hover:bg-muted/60 text-foreground border border-border/50 hover:border-border'}`}
                    >
                      {view === 'sql' ? 'SQL' : 'Data table'}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {v('max result rows per query') && (
            <div className="w-74 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5">
              <div className="flex items-start justify-between gap-2 mb-0.5">
                <p className="text-sm font-medium text-foreground">Max rows per query</p>
                <span className={`shrink-0 text-[10px] uppercase tracking-widest font-semibold mt-0.5 ${maxResultRows === PREFERENCES_DEFAULTS.maxResultRows ? 'text-muted-foreground/40' : 'text-primary/60'}`}>
                  {maxResultRows === PREFERENCES_DEFAULTS.maxResultRows ? 'Default' : 'Changed'}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mb-4">Higher values return more data but take longer.</p>
              <div className="grid grid-cols-4 gap-1.5">
                {ROW_OPTIONS.map((rows) => {
                  const sel = maxResultRows === rows;
                  return (
                    <button
                      key={rows}
                      onClick={() => setMaxResultRows(rows)}
                      className={`rounded-xl py-2.5 text-xs font-medium tabular-nums transition-all outline-none focus-visible:ring-2 focus-visible:ring-ring ${sel ? 'bg-primary text-primary-foreground font-semibold' : 'bg-muted/40 hover:bg-muted/60 text-foreground border border-border/50 hover:border-border'}`}
                    >
                      {rows}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AppearanceContent({
  v, density, setDensity, highContrast, setHighContrast,
}: {
  v: (k: string) => boolean;
  density: Density; setDensity: (v: Density) => void;
  highContrast: boolean; setHighContrast: (v: boolean) => void;
}) {
  return (
    <div className="space-y-10">
      {/* Density — large visual preview cards */}
      {v('density compact comfortable spacing rows') && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground/35 mb-4">Density</p>
          <div className="flex flex-wrap gap-4">
            {(['comfortable', 'compact'] as Density[]).map((d) => {
              const sel = density === d;
              const isDefault = d === PREFERENCES_DEFAULTS.density;
              const lines = d === 'comfortable' ? [70, 50, 85, 60] : [70, 50, 85, 60, 40, 75];
              const gap = d === 'comfortable' ? 'gap-2.5' : 'gap-1.5';
              return (
                <button
                  key={d}
                  onClick={() => setDensity(d)}
                  aria-pressed={sel}
                  className={`w-74 shrink-0 grow-0 relative rounded-2xl p-5 text-left transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring ${sel ? 'bg-primary shadow-sm' : 'bg-muted/25 border border-border hover:bg-muted/40 hover:border-border/80'}`}
                >
                  {isDefault && (
                    <span className={`absolute top-3.5 right-3.5 text-[9px] uppercase tracking-widest font-semibold ${sel ? 'text-primary-foreground/60' : 'text-muted-foreground/35'}`}>
                      Default
                    </span>
                  )}
                  <div className={`flex flex-col ${gap} mb-4`}>
                    {lines.map((w, i) => (
                      <div
                        key={i}
                        className={`h-1.5 rounded-full ${sel ? 'bg-primary-foreground/30' : 'bg-foreground/10'}`}
                        style={{ width: `${w}%` }}
                      />
                    ))}
                  </div>
                  <p className={`text-sm font-semibold capitalize ${sel ? 'text-primary-foreground' : 'text-foreground'}`}>{d}</p>
                  <p className={`text-xs mt-0.5 ${sel ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>
                    {d === 'comfortable' ? 'Generous spacing, easier to scan.' : 'Tighter rows, more content visible.'}
                  </p>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Accessibility */}
      {v('high contrast accessibility bold text sharp') && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground/35 mb-4">Accessibility</p>
          <div className="w-74 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5 flex flex-col gap-4 hover:bg-muted/30 transition-colors">
            <div className="flex-1">
              <p className="text-sm font-medium text-foreground mb-1.5">High contrast</p>
              <p className="text-xs text-muted-foreground leading-relaxed">Makes text darker and borders sharper — easier to read in bright environments.</p>
            </div>
            <div className="flex justify-center">
              <SegmentControl checked={highContrast} onCheckedChange={setHighContrast} labelOn="On" labelOff="Off" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Primitive components ────────────────────────────────────────────────

function SegmentControl({
  checked, onCheckedChange, disabled, labelOn = 'On', labelOff = 'Off',
}: {
  checked: boolean; onCheckedChange: (v: boolean) => void;
  disabled?: boolean; labelOn?: string; labelOff?: string;
}) {
  return (
    <div className={`w-fit inline-flex rounded-full p-0.5 bg-muted border border-border ${disabled ? 'opacity-40 pointer-events-none' : ''}`}>
      <button
        onClick={() => onCheckedChange(true)}
        className={`w-9 py-1 rounded-full text-xs font-medium text-center transition-all ${checked ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
      >
        {labelOn}
      </button>
      <button
        onClick={() => onCheckedChange(false)}
        className={`w-9 py-1 rounded-full text-xs font-medium text-center transition-all ${!checked ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
      >
        {labelOff}
      </button>
    </div>
  );
}

function SettingBlock({
  label, description, children,
}: {
  label: string; description?: string; children: ReactNode;
}) {
  return (
    <div>
      <Label className="text-sm font-medium text-foreground">{label}</Label>
      {description && (
        <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
      )}
      {children}
    </div>
  );
}

function DefaultTag({ visible }: { visible: boolean }) {
  return (
    <span
      aria-hidden={!visible}
      className="block text-center mt-1 text-[9px] uppercase tracking-wider text-muted-foreground/50 select-none"
    >
      {visible ? 'default' : ' '}
    </span>
  );
}


function NotificationsPanel({
  notifyOnComplete, setNotifyOnComplete, notifySound, setNotifySound, v,
}: {
  notifyOnComplete: 'when-hidden' | 'off'; setNotifyOnComplete: (v: 'when-hidden' | 'off') => void;
  notifySound: boolean; setNotifySound: (v: boolean) => void;
  v: (k: string) => boolean;
}) {
  const [permission, setPermission] = useState<NotificationPermissionState>('default');

  useEffect(() => {
    if (!notificationsSupported()) { setPermission('unsupported'); return; }
    setPermission(getPermission());
    const onFocus = () => setPermission(getPermission());
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, []);

  const handleEnable = async () => setPermission(await requestPermission());
  const enabled = notifyOnComplete === 'when-hidden';

  return (
    <div className="space-y-10">
      {/* Preferences — notification toggle cards */}
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground/35 mb-4">Preferences</p>
        <div className="flex flex-wrap gap-3">
          {v('notify when answers finish notifications stream completion') && (
            <div className="w-74 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5 flex flex-col gap-4 hover:bg-muted/30 transition-colors">
              <div className="flex-1">
                <div className="flex items-start justify-between gap-2 mb-1.5">
                  <p className="text-sm font-medium text-foreground">Notify on completion</p>
                  {(enabled ? 'when-hidden' : 'off') !== PREFERENCES_DEFAULTS.notifyOnComplete && (
                    <span className="text-[10px] uppercase tracking-widest font-semibold text-primary/60 mt-0.5">changed</span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">Pings you when a stream completes and you're not on that chat.</p>
              </div>
              <div className="flex justify-center">
                <SegmentControl
                  checked={enabled}
                  onCheckedChange={(val) => setNotifyOnComplete(val ? 'when-hidden' : 'off')}
                />
              </div>
            </div>
          )}
          {v('play sound ping audio notifications') && (
            <div className={`w-74 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5 flex flex-col gap-4 transition-colors ${!enabled ? 'opacity-50' : 'hover:bg-muted/30'}`}>
              <div className="flex-1">
                <div className="flex items-start justify-between gap-2 mb-1.5">
                  <p className="text-sm font-medium text-foreground">Play a sound</p>
                  {notifySound !== PREFERENCES_DEFAULTS.notifySound && (
                    <span className="text-[10px] uppercase tracking-widest font-semibold text-primary/60 mt-0.5">changed</span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {enabled ? 'Soft ping alongside the notification.' : 'Enable notifications to use this.'}
                </p>
              </div>
              <div className="flex justify-center">
                <SegmentControl checked={enabled && notifySound} onCheckedChange={setNotifySound} disabled={!enabled} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Browser permission */}
      {v('browser permission notifications') && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground/35 mb-4">Browser</p>
          <div className="w-150 shrink-0 grow-0 rounded-2xl border border-border bg-muted/20 p-5 flex items-start justify-between gap-6">
            <div className="flex items-start gap-3.5 min-w-0">
              <Bell className="w-4 h-4 text-muted-foreground shrink-0 mt-0.5" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">Notification permission</p>
                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                  {permission === 'granted' && 'Allowed — to revoke, click the lock icon in the address bar.'}
                  {permission === 'default' && 'Not yet granted. Allow browser notifications to receive alerts.'}
                  {permission === 'denied' && "Blocked — open your browser's site settings to re-enable."}
                  {permission === 'unsupported' && "Your browser doesn't support desktop notifications."}
                </p>
              </div>
            </div>
            <div className="shrink-0">
              {permission === 'granted' && (
                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 px-3 py-1.5 rounded-full">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" aria-hidden />
                  Allowed
                </span>
              )}
              {permission === 'denied' && (
                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-destructive bg-destructive/10 px-3 py-1.5 rounded-full">
                  <span className="w-1.5 h-1.5 rounded-full bg-destructive" aria-hidden />
                  Blocked
                </span>
              )}
              {permission === 'unsupported' && (
                <span className="text-xs text-muted-foreground/50">Unavailable</span>
              )}
              {permission === 'default' && (
                <Button size="sm" onClick={handleEnable}>Enable</Button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Query Patterns panel ────────────────────────────────────────────────────

const BATCH_SIZE = 20;

type PatternLoadState = {
  items: PatternRecord[];
  total: number;
  enabledTotal: number;
  disabledTotal: number;
  allFetched: boolean;
  nextSkip: number;
};

function useResizablePanel(defaultPx = 750) {
  const [leftWidth, setLeftWidth] = useState(defaultPx);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const { left, width } = containerRef.current.getBoundingClientRect();
      setLeftWidth(Math.max(280, Math.min(ev.clientX - left, width - 280)));
    };
    const onUp = () => {
      dragging.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, []);

  const reset = useCallback(() => setLeftWidth(defaultPx), [defaultPx]);

  return { leftWidth, containerRef, onDragStart, reset };
}

function NonAdminPatternView() {
  const { leftWidth, containerRef, onDragStart, reset } = useResizablePanel();
  const [activeTab, setActiveTab] = useState<'patterns' | 'antipatterns'>('patterns');
  const [qpItems, setQpItems] = useState<PatternRecord[]>([]);
  const [apItems, setApItems] = useState<PatternRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [detail, setDetail] = useState<{ record: PatternRecord; variant: 'pattern' | 'antipattern' } | null>(null);

  useEffect(() => {
    if (!detail) reset();
  }, [detail, reset]);

  useEffect(() => {
    Promise.all([listEnabledQueryPatterns(), listEnabledAntiPatterns()])
      .then(([qp, ap]) => { setQpItems(qp.items); setApItems(ap.items); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const total = qpItems.length + apItems.length;
  const noop = () => {};

  const visibleQP = qpItems.filter((item) => matchesPatternSearch(item, debouncedSearch));
  const visibleAP = apItems.filter((item) => matchesPatternSearch(item, debouncedSearch));

  const handleOpen = (record: PatternRecord, variant: 'pattern' | 'antipattern') =>
    setDetail((prev) => prev?.record.id === record.id ? null : { record, variant });

  return (
    <div ref={containerRef} className="flex flex-1 min-h-0 overflow-hidden">
      {/* Left: card list */}
      <div style={{ width: leftWidth }} className="shrink-0 flex flex-col min-h-0 overflow-hidden pr-4">
        {/* Info banner */}
        <div className="flex items-start gap-2.5 rounded-lg border border-primary/25 bg-primary/5 px-4 py-3 mb-3 shrink-0">
          <Info className="w-4 h-4 text-primary shrink-0 mt-0.5" />
          <div>
            <p className="text-xs font-medium text-foreground">
              {total > 0
                ? `${qpItems.length} query pattern${qpItems.length !== 1 ? 's' : ''} and ${apItems.length} anti-pattern${apItems.length !== 1 ? 's' : ''} are enabled for training.`
                : 'No patterns are currently enabled for training.'}
            </p>
            <p className="text-[11px] text-muted-foreground/60 mt-0.5">
              These patterns guide the AI when generating SQL and answers.
            </p>
          </div>
        </div>

        {/* Search */}
        <div className="relative shrink-0 mb-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search patterns…"
            className="pl-8 pr-8 h-9 text-sm"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-border/50 shrink-0">
          {(['patterns', 'antipatterns'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-2 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-t-md border-b-2 -mb-px ${
                activeTab === tab ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {tab === 'patterns' ? (
                <span className="flex items-center gap-1.5">
                  <Database className="w-3 h-3" />
                  Query patterns
                  <span className="tabular-nums text-muted-foreground/60">({visibleQP.length})</span>
                </span>
              ) : (
                <span className="flex items-center gap-1.5">
                  <ShieldAlert className="w-3 h-3" />
                  Anti-patterns
                  <span className="tabular-nums text-muted-foreground/60">({visibleAP.length})</span>
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Card list */}
        <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pt-3 pr-1 pb-4">
          {loading ? (
            <div className="flex justify-center py-10">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : activeTab === 'patterns' ? (
            visibleQP.length === 0
              ? <p className="text-xs text-muted-foreground/50 py-8 text-center">{search ? 'No patterns match your search.' : 'No query patterns enabled for training.'}</p>
              : visibleQP.map((item, i) => (
                  <PatternCard
                    key={(item.id as string) ?? i}
                    record={item}
                    variant="pattern"
                    selected={false}
                    onSelect={noop}
                    readOnly
                    query={debouncedSearch}
                    onOpen={handleOpen}
                  />
                ))
          ) : (
            visibleAP.length === 0
              ? <p className="text-xs text-muted-foreground/50 py-8 text-center">{search ? 'No patterns match your search.' : 'No anti-patterns enabled for training.'}</p>
              : visibleAP.map((item, i) => (
                  <PatternCard
                    key={(item.id as string) ?? i}
                    record={item}
                    variant="antipattern"
                    selected={false}
                    onSelect={noop}
                    readOnly
                    query={debouncedSearch}
                    onOpen={handleOpen}
                  />
                ))
          )}
        </div>
      </div>

      {/* Drag handle — always visible */}
      <div
        onMouseDown={onDragStart}
        className="w-1 shrink-0 cursor-col-resize group relative flex items-center justify-center hover:bg-primary/20 transition-colors"
      >
        <div className="w-px h-full bg-border/50 group-hover:bg-primary/40 transition-colors" />
      </div>

      {/* Right: detail pane */}
      <PatternDetailPane
        record={detail?.record ?? null}
        variant={detail?.variant ?? 'pattern'}
        query={debouncedSearch}
        onClose={() => setDetail(null)}
      />
    </div>
  );
}

type FilterVal = 'all' | 'enabled' | 'disabled';

function QueryPatternsPanel() {
  // ── Access check (must be first, before any conditional return) ──────────
  const [accessMode, setAccessMode] = useState<'checking' | 'admin' | 'user'>('checking');

  // ── All admin-panel state (hooks must always be called) ──────────────────
  const { leftWidth, containerRef, onDragStart, reset } = useResizablePanel();
  const [activeTab, setActiveTab] = useState<'patterns' | 'antipatterns'>('patterns');
  const [patternsData, setPatternsData] = useState<PatternLoadState | null>(null);
  const [antiData, setAntiData] = useState<PatternLoadState | null>(null);
  const [loadingP, setLoadingP] = useState(false);
  const [loadingA, setLoadingA] = useState(false);
  const pAbortRef = useRef<AbortController | null>(null);
  const aAbortRef = useRef<AbortController | null>(null);
  const pSentinelRef = useRef<HTMLDivElement>(null);
  const aSentinelRef = useRef<HTMLDivElement>(null);
  const [selectModeP, setSelectModeP] = useState(false);
  const [selectModeA, setSelectModeA] = useState(false);
  const [selectedP, setSelectedP] = useState<Set<string>>(new Set());
  const [selectedA, setSelectedA] = useState<Set<string>>(new Set());
  const [filterP, setFilterP] = useState<FilterVal>('all');
  const [filterA, setFilterA] = useState<FilterVal>('all');
  const [searchP, setSearchP] = useState('');
  const [searchA, setSearchA] = useState('');
  const [debouncedSearchP, setDebouncedSearchP] = useState('');
  const [debouncedSearchA, setDebouncedSearchA] = useState('');
  const [detail, setDetail] = useState<{ record: PatternRecord; variant: 'pattern' | 'antipattern' } | null>(null);
  const [enablingP, setEnablingP] = useState(false);
  const [enablingA, setEnablingA] = useState(false);
  const [disablingP, setDisablingP] = useState(false);
  const [disablingA, setDisablingA] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<'patterns' | 'antipatterns' | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  useEffect(() => {
    if (!detail) reset();
  }, [detail, reset]);

  const loadPatterns = useCallback(async (skip: number, search: string, filter: FilterVal) => {
    pAbortRef.current?.abort();
    const ctrl = new AbortController();
    pAbortRef.current = ctrl;
    setLoadingP(true);
    try {
      const result = await listQueryPatterns(skip, BATCH_SIZE, search, filter);
      if (ctrl.signal.aborted) return;
      setPatternsData(prev => {
        const base = skip === 0 ? [] : (prev?.items ?? []);
        const seen = new Set(base.map(x => String(x.id)));
        const fresh = result.items.filter(x => !seen.has(String(x.id)));
        const next = [...base, ...fresh];
        return {
          items: next,
          total: result.total,
          enabledTotal: result.enabled_total,
          disabledTotal: result.disabled_total,
          allFetched: next.length >= result.total,
          nextSkip: next.length,
        };
      });
    } catch { } finally { if (!ctrl.signal.aborted) setLoadingP(false); }
  }, []);

  const loadAnti = useCallback(async (skip: number, search: string, filter: FilterVal) => {
    aAbortRef.current?.abort();
    const ctrl = new AbortController();
    aAbortRef.current = ctrl;
    setLoadingA(true);
    try {
      const result = await listAntiPatterns(skip, BATCH_SIZE, search, filter);
      if (ctrl.signal.aborted) return;
      setAntiData(prev => {
        const base = skip === 0 ? [] : (prev?.items ?? []);
        const seen = new Set(base.map(x => String(x.id)));
        const fresh = result.items.filter(x => !seen.has(String(x.id)));
        const next = [...base, ...fresh];
        return {
          items: next,
          total: result.total,
          enabledTotal: result.enabled_total,
          disabledTotal: result.disabled_total,
          allFetched: next.length >= result.total,
          nextSkip: next.length,
        };
      });
    } catch { } finally { if (!ctrl.signal.aborted) setLoadingA(false); }
  }, []);

  const loadMoreP = useCallback(() => {
    if (!patternsData || patternsData.allFetched || loadingP) return;
    void loadPatterns(patternsData.nextSkip, debouncedSearchP, filterP);
  }, [patternsData, loadingP, loadPatterns, debouncedSearchP, filterP]);

  const loadMoreA = useCallback(() => {
    if (!antiData || antiData.allFetched || loadingA) return;
    void loadAnti(antiData.nextSkip, debouncedSearchA, filterA);
  }, [antiData, loadingA, loadAnti, debouncedSearchA, filterA]);

  // Access check effect — runs once on mount
  useEffect(() => {
    listQueryPatterns(0, 1)
      .then(() => setAccessMode('admin'))
      .catch((err: unknown) => {
        const status = (err as { status?: number })?.status;
        setAccessMode(status === 403 ? 'user' : 'admin');
      });
  }, []);

  // Reload from skip=0 when accessMode becomes 'admin' or search/filter changes
  useEffect(() => {
    if (accessMode !== 'admin') return;
    void loadPatterns(0, debouncedSearchP, filterP);
  }, [accessMode, debouncedSearchP, filterP, loadPatterns]);
  useEffect(() => {
    if (accessMode !== 'admin') return;
    void loadAnti(0, debouncedSearchA, filterA);
  }, [accessMode, debouncedSearchA, filterA, loadAnti]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearchP(searchP), 300);
    return () => clearTimeout(timer);
  }, [searchP]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearchA(searchA), 300);
    return () => clearTimeout(timer);
  }, [searchA]);

  useEffect(() => {
    const sentinel = pSentinelRef.current;
    if (!sentinel) return;
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) loadMoreP(); },
      { rootMargin: '200px' }
    );
    obs.observe(sentinel);
    return () => obs.disconnect();
  }, [loadMoreP]);

  useEffect(() => {
    const sentinel = aSentinelRef.current;
    if (!sentinel) return;
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) loadMoreA(); },
      { rootMargin: '200px' }
    );
    obs.observe(sentinel);
    return () => obs.disconnect();
  }, [loadMoreA]);

  const handleSelect = (set: Set<string>, setFn: (s: Set<string>) => void) =>
    (id: string, checked: boolean) => {
      const next = new Set(set);
      checked ? next.add(id) : next.delete(id);
      setFn(next);
    };

  const handleBulkEnable = async (tab: 'patterns' | 'antipatterns') => {
    const ids = tab === 'patterns' ? [...selectedP] : [...selectedA];
    const setLoading = tab === 'patterns' ? setEnablingP : setEnablingA;
    setLoading(true);
    try {
      await Promise.all(ids.map((id) =>
        tab === 'patterns' ? setQueryPatternEnabled(id, true) : setAntiPatternEnabled(id, true)
      ));
      if (tab === 'patterns') {
        setSelectedP(new Set());
        await loadPatterns(0, debouncedSearchP, filterP);
      } else {
        setSelectedA(new Set());
        await loadAnti(0, debouncedSearchA, filterA);
      }
    } catch { } finally { setLoading(false); }
  };

  const handleBulkDisable = async (tab: 'patterns' | 'antipatterns') => {
    const ids = tab === 'patterns' ? [...selectedP] : [...selectedA];
    const setLoading = tab === 'patterns' ? setDisablingP : setDisablingA;
    setLoading(true);
    try {
      await Promise.all(ids.map((id) =>
        tab === 'patterns' ? setQueryPatternEnabled(id, false) : setAntiPatternEnabled(id, false)
      ));
      if (tab === 'patterns') {
        setSelectedP(new Set());
        await loadPatterns(0, debouncedSearchP, filterP);
      } else {
        setSelectedA(new Set());
        await loadAnti(0, debouncedSearchA, filterA);
      }
    } catch { } finally { setLoading(false); }
  };

  const handleBulkDelete = async () => {
    if (!deleteConfirm) return;
    const ids = deleteConfirm === 'patterns' ? [...selectedP] : [...selectedA];
    setBulkDeleting(true);
    try {
      await Promise.all(ids.map((id) =>
        deleteConfirm === 'patterns' ? deleteQueryPattern(id) : deleteAntiPattern(id)
      ));
      if (deleteConfirm === 'patterns') {
        setSelectedP(new Set());
        await loadPatterns(0, debouncedSearchP, filterP);
      } else {
        setSelectedA(new Set());
        await loadAnti(0, debouncedSearchA, filterA);
      }
    } catch { } finally {
      setBulkDeleting(false);
      setDeleteConfirm(null);
    }
  };

  const renderTab = (tab: 'patterns' | 'antipatterns', sentinelRef: RefObject<HTMLDivElement | null>) => {
    const isP = tab === 'patterns';
    const data = isP ? patternsData : antiData;
    const loading = isP ? loadingP : loadingA;
    const selected = isP ? selectedP : selectedA;
    const setSelected = isP ? setSelectedP : setSelectedA;
    const enabling = isP ? enablingP : enablingA;
    const disabling = isP ? disablingP : disablingA;
    const filter = isP ? filterP : filterA;
    const setFilter = isP ? setFilterP : setFilterA;
    const search = isP ? searchP : searchA;
    const setSearch = isP ? setSearchP : setSearchA;
    const debouncedSearch = isP ? debouncedSearchP : debouncedSearchA;
    const selectMode = isP ? selectModeP : selectModeA;
    const setSelectMode = isP ? setSelectModeP : setSelectModeA;

    if (loading && !data) return (
      <div className="flex flex-col h-full min-h-0">
        <Skeleton className="h-11 w-full rounded-lg mb-3" />
        <div className="flex items-center justify-between mb-2 min-h-8">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-12" />
        </div>
        <div className="flex gap-1.5 mb-3">
          <Skeleton className="h-6 w-16 rounded-full" />
          <Skeleton className="h-6 w-20 rounded-full" />
          <Skeleton className="h-6 w-20 rounded-full" />
        </div>
        <div className="flex-1 min-h-0 overflow-hidden space-y-2">
          {[1, 0.85, 0.7, 0.55].map((opacity, i) => (
            <div key={i} className="rounded-lg border border-border/50 p-4 space-y-2" style={{ opacity }}>
              <Skeleton className="h-4 w-4/5" />
              <Skeleton className="h-4 w-3/5" />
              <Skeleton className="h-3 w-12 mt-1" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-11/12" />
              <div className="flex gap-1.5 pt-1">
                <Skeleton className="h-5 w-20 rounded" />
                <Skeleton className="h-5 w-24 rounded" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );

    const filteredItems = data?.items ?? [];
    const total = data?.total ?? 0;
    const enabledTotal = data?.enabledTotal ?? 0;
    const disabledTotal = data?.disabledTotal ?? 0;
    const stillLoading = !(data?.allFetched ?? true);
    const hasSelection = selected.size > 0;
    const allSelected = filteredItems.length > 0 && filteredItems.every((x) => selected.has(x.id as string));

    return (
      <div className="flex flex-col h-full min-h-0">
        {/* Search — full-width */}
        <div className="relative shrink-0 mb-3">
          <div className="absolute left-3 top-3 h-4 w-4 text-muted-foreground pointer-events-none">
            {loading && data ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          </div>
          <Input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setSelected(new Set()); }}
            placeholder={`Search ${isP ? 'patterns' : 'anti-patterns'}…`}
            className={`pl-10 h-11 text-sm transition-all ${search ? 'pr-9' : ''}`}
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-3 top-3 h-4 w-4 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Selection bar */}
        {(total > 0 || selectMode) && (
          <div className="flex items-center justify-between shrink-0 mb-2 min-h-8">
            <div className="flex items-center gap-3">
              {selectMode ? (
                <>
                  <Checkbox
                    checked={allSelected}
                    onCheckedChange={() => allSelected
                      ? setSelected(new Set())
                      : setSelected(new Set(filteredItems.map((x) => String(x.id)).filter(Boolean)))
                    }
                    className="h-5 w-5"
                  />
                  <span className="text-sm text-foreground font-medium">
                    {hasSelection ? `${selected.size} selected` : 'Select all'}
                  </span>
                  {hasSelection && (
                    <div className="flex items-center gap-1 ml-1">
                      {filter !== 'enabled' && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-8 w-8 p-0"
                              onClick={() => handleBulkEnable(tab)} disabled={enabling}>
                              {enabling ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent side="top">Enable selected</TooltipContent>
                        </Tooltip>
                      )}
                      {filter !== 'disabled' && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-8 w-8 p-0"
                              onClick={() => handleBulkDisable(tab)} disabled={disabling}>
                              {disabling ? <Loader2 className="w-4 h-4 animate-spin" /> : <Ban className="w-4 h-4" />}
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent side="top">Disable selected</TooltipContent>
                        </Tooltip>
                      )}
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button variant="ghost" size="sm"
                            className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                            onClick={() => setDeleteConfirm(tab)}>
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top">Delete selected</TooltipContent>
                      </Tooltip>
                    </div>
                  )}
                </>
              ) : (
                <span className="text-sm text-muted-foreground">
                  {enabledTotal + disabledTotal} {isP ? 'pattern' : 'anti-pattern'}{(enabledTotal + disabledTotal) !== 1 ? 's' : ''}
                </span>
              )}
            </div>

            <button
              onClick={() => {
                if (selectMode) { setSelectMode(false); setSelected(new Set()); }
                else setSelectMode(true);
              }}
              className={`flex items-center gap-1 text-sm transition-colors ${
                selectMode
                  ? 'text-muted-foreground hover:text-foreground'
                  : 'text-primary hover:underline'
              }`}
            >
              {selectMode ? <><X className="w-4 h-4" /> Cancel</> : 'Select'}
            </button>
          </div>
        )}

        {/* Filter pills */}
        <div className="flex items-center gap-1.5 flex-wrap shrink-0 mb-3">
          {([
            { value: 'all',      label: `All (${enabledTotal + disabledTotal})` },
            { value: 'enabled',  label: `Enabled (${enabledTotal})` },
            { value: 'disabled', label: `Disabled (${disabledTotal})` },
          ] as { value: FilterVal; label: string }[]).map(({ value, label }) => (
            <button
              key={value}
              onClick={() => { setFilter(value); setSelected(new Set()); }}
              className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors ${
                filter === value
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted/50 text-muted-foreground hover:bg-muted/80 hover:text-foreground'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Scrollable cards */}
        {(!data || total === 0) ? (
          <p className="text-xs text-muted-foreground/50 py-8 text-center">
            No {isP ? 'query patterns' : 'anti-patterns'} recorded yet.
          </p>
        ) : filteredItems.length === 0 && !stillLoading ? (
          <p className="text-xs text-muted-foreground/50 py-6 text-center">
            No {filter === 'all' ? '' : filter + ' '}{isP ? 'query patterns' : 'anti-patterns'}{debouncedSearch ? ' match your search' : ''}.
          </p>
        ) : (
          <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1 pb-2">
            {filteredItems.map((item, i) => (
              <PatternCard
                key={(item.id as string) ?? i}
                record={item}
                variant={isP ? 'pattern' : 'antipattern'}
                selected={selected.has(item.id as string)}
                onSelect={(id, checked) => {
                  handleSelect(selected, setSelected)(id, checked);
                  if (checked) setSelectMode(true);
                }}
                query={debouncedSearch}
                onOpen={(r, v) => setDetail((prev) => prev?.record.id === r.id ? null : { record: r, variant: v })}
              />
            ))}
            <div ref={sentinelRef} className="h-1" />
            {stillLoading && (
              <div className="flex justify-center py-3">
                <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground/40" />
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  if (accessMode === 'checking') return (
    <div className="flex justify-center py-10">
      <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
    </div>
  );
  if (accessMode === 'user') return <NonAdminPatternView />;

  return (
    <div ref={containerRef} className="flex flex-1 min-h-0 overflow-hidden">
      {/* Left: tabs + card list */}
      <div style={{ width: leftWidth }} className="shrink-0 flex flex-col min-h-0 overflow-hidden pr-4">
        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-border/50 shrink-0">
          {(['patterns', 'antipatterns'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-2 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-t-md border-b-2 -mb-px ${
                activeTab === tab ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {tab === 'patterns' ? (
                <span className="flex items-center gap-1.5">
                  <Database className="w-3 h-3" />
                  Query patterns
                  {patternsData && <span className="tabular-nums text-muted-foreground/60">({patternsData.enabledTotal + patternsData.disabledTotal})</span>}
                </span>
              ) : (
                <span className="flex items-center gap-1.5">
                  <ShieldAlert className="w-3 h-3" />
                  Anti-patterns
                  {antiData && <span className="tabular-nums text-muted-foreground/60">({antiData.enabledTotal + antiData.disabledTotal})</span>}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="flex-1 min-h-0 overflow-hidden pt-3">
          {activeTab === 'patterns' && renderTab('patterns', pSentinelRef)}
          {activeTab === 'antipatterns' && renderTab('antipatterns', aSentinelRef)}
        </div>
      </div>

      {/* Drag handle — always visible */}
      <div
        onMouseDown={onDragStart}
        className="w-1 shrink-0 cursor-col-resize group relative flex items-center justify-center hover:bg-primary/20 transition-colors"
      >
        <div className="w-px h-full bg-border/50 group-hover:bg-primary/40 transition-colors" />
      </div>

      {/* Right: detail pane */}
      <PatternDetailPane
        record={detail?.record ?? null}
        variant={detail?.variant ?? 'pattern'}
        query={activeTab === 'patterns' ? debouncedSearchP : debouncedSearchA}
        onClose={() => setDetail(null)}
      />

      {/* Delete confirmation dialog (portal, unaffected by layout) */}
      <AlertDialog open={!!deleteConfirm} onOpenChange={(open) => !open && setDeleteConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {deleteConfirm === 'patterns' ? selectedP.size : selectedA.size} {deleteConfirm === 'patterns' ? 'query pattern' : 'anti-pattern'}{(deleteConfirm === 'patterns' ? selectedP.size : selectedA.size) !== 1 ? 's' : ''}?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <span className="block font-medium text-destructive">⚠ This action cannot be undone.</span>
              <span className="block">Once deleted, these patterns are permanently removed from the database and cannot be recovered.</span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={bulkDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBulkDelete}
              disabled={bulkDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {bulkDeleting ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : null}
              Delete permanently
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function matchesPatternSearch(item: PatternRecord, q: string): boolean {
  if (!q.trim()) return true;
  const lower = q.toLowerCase();
  for (const val of Object.values(item)) {
    if (val === null || val === undefined || val === '') continue;
    if (Array.isArray(val)) {
      if (val.some((t) => String(t).toLowerCase().includes(lower))) return true;
    } else if (String(val).toLowerCase().includes(lower)) {
      return true;
    }
  }
  return false;
}

// Keys rendered as code blocks in the expanded view
const SQL_KEYS = new Set(['sql_text', 'sql_cte_outline', 'directive_summary', 'join_outline', 'filter_summary', 'measure_summary', 'dimension_summary']);
// Keys shown in the collapsed summary row
const SUMMARY_KEYS_QP = ['intent', 'complexity', 'occurrence_count', 'liked_count', 'disliked_count', 'confidence_score', 'promotion_status', 'repair_count', 'last_seen'];
const SUMMARY_KEYS_AP = ['error_type', 'intent', 'complexity', 'occurrence_count', 'success_count', 'failing_element', 'last_seen'];
// Keys rendered as tag chips
const TAG_KEYS = new Set(['tables_used']);

function renderValue(key: string, val: unknown): React.ReactNode {
  if (val === null || val === undefined || val === '') return null;
  if (Array.isArray(val)) {
    if (val.length === 0) return null;
    if (TAG_KEYS.has(key)) {
      return (
        <div className="flex flex-wrap gap-1">
          {val.map((t, i) => (
            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-muted/50 text-muted-foreground/60 font-mono">{String(t)}</span>
          ))}
        </div>
      );
    }
    return val.map(String).join(', ');
  }
  if (typeof val === 'boolean') return val ? 'true' : 'false';
  return String(val);
}

function SqlCopyButton({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(sql).then(() => {
      setCopied(true);
      toast.success('Copied');
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => toast.error('Copy failed'));
  };
  return (
    <button
      onClick={handleCopy}
      className="flex items-center gap-1 text-[10px] text-muted-foreground/50 hover:text-foreground transition-colors"
      aria-label="Copy SQL"
    >
      {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

function PatternDetailPane({ record, variant, query, onClose }: {
  record: PatternRecord | null;
  variant: 'pattern' | 'antipattern';
  query: string;
  onClose: () => void;
}) {
  const isAnti = variant === 'antipattern';
  const questionText = record?.question_text as string | null | undefined;
  const isEnabled = record?.is_enabled === true;
  const resolved = isAnti && Number(record?.success_count ?? 0) > 0;
  const summaryKeys = isAnti ? SUMMARY_KEYS_AP : SUMMARY_KEYS_QP;

  const HEADER_KEYS = new Set([
    'question_text', 'tables_used',
    ...SUMMARY_KEYS_QP, ...SUMMARY_KEYS_AP,
  ]);

  const allKeys = record ? Object.keys(record).filter((k) => !HEADER_KEYS.has(k)).sort((a, b) => {
    const aLast = SQL_KEYS.has(a) ? 1 : 0;
    const bLast = SQL_KEYS.has(b) ? 1 : 0;
    if (aLast !== bLast) return aLast - bLast;
    return a.localeCompare(b);
  }) : [];

  return (
    <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
      {!record ? null : (
        /* Single scroll container — header + fields scroll together so a tall
           header never squeezes the fields out of view */
        <div className="grow h-0 overflow-y-auto">
          {/* Badges + close */}
          <div className="flex items-start justify-between gap-2 px-5 pt-5 mb-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide ${
                isAnti
                  ? resolved ? 'bg-muted/60 text-muted-foreground' : 'bg-red-500/10 text-red-500'
                  : 'bg-primary/10 text-primary'
              }`}>
                {isAnti ? (resolved ? 'resolved anti-pattern' : 'anti-pattern') : 'query pattern'}
              </span>
              {isEnabled && (
                <span className="text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                  enabled
                </span>
              )}
            </div>
            <button
              onClick={onClose}
              className="shrink-0 p-1 rounded-sm opacity-60 hover:opacity-100 hover:bg-muted/60 transition-opacity"
              aria-label="Close"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Title */}
          <p className="text-sm font-medium leading-snug px-5">
            {questionText
              ? highlightQueryInText(questionText, query)
              : <span className="italic text-muted-foreground/50">No question text</span>}
          </p>

          {/* Summary chips */}
          <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 px-5">
            {summaryKeys.map((k) => {
              const v = record[k];
              if (v === null || v === undefined || v === '') return null;
              const display = k === 'confidence_score'
                ? `${(Number(v) * 100).toFixed(0)}% conf`
                : k === 'last_seen'
                  ? `Last seen ${relativeDate(String(v))}`
                  : k === 'occurrence_count'
                    ? `Seen ${v}×`
                    : k === 'success_count' && Number(v) > 0
                      ? `Resolved ${v}×`
                    : k === 'liked_count' || k === 'disliked_count'
                      ? null
                      : `${k.replace(/_/g, ' ')}: ${v}`;
              if (!display) return null;
              return <span key={k} className="text-[11px] text-muted-foreground/60">{highlightQueryInText(display, query)}</span>;
            })}
            {!isAnti && (Number(record.liked_count ?? 0) > 0 || Number(record.disliked_count ?? 0) > 0) && (
              <span className="text-[11px]">
                <span className="text-emerald-600 dark:text-emerald-400">↑{record.liked_count as number}</span>
                {' '}
                <span className="text-red-500">↓{record.disliked_count as number}</span>
              </span>
            )}
          </div>

          {/* Tables */}
          {Array.isArray(record.tables_used) && record.tables_used.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2 px-5">
              {(record.tables_used as string[]).map((t) => (
                <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-muted/50 text-muted-foreground/60 font-mono">
                  {highlightQueryInText(t, query)}
                </span>
              ))}
            </div>
          )}

          {/* Divider */}
          <div className="border-b border-border/50 mt-4 mx-5" />

          {/* Fields */}
          <div className="px-5 py-4 space-y-1">
            {allKeys.map((k) => {
              const v = record[k];
              if (v === null || v === undefined || v === '') return null;
              if (SQL_KEYS.has(k)) {
                const sql = String(v);
                return (
                  <div key={k} className="py-2">
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground/40 font-medium">{k.replace(/_/g, ' ')}</p>
                      <SqlCopyButton sql={sql} />
                    </div>
                    <div className="rounded-md bg-muted/40 border border-border/40 overflow-x-auto">
                      <pre className="p-3 text-[11px] font-mono text-foreground/80 leading-relaxed whitespace-pre">
                        {highlightQueryInText(sql, query)}
                      </pre>
                    </div>
                  </div>
                );
              }
              const rendered = renderValue(k, v);
              if (!rendered) return null;
              return (
                <div key={k} className="flex gap-3 py-1.5 border-b border-border/20 last:border-0">
                  <span className="text-[11px] text-muted-foreground/45 w-32 shrink-0 font-mono pt-px">{k.replace(/_/g, ' ')}</span>
                  <span className="text-[11px] text-foreground/80 flex-1 min-w-0 break-words leading-relaxed">
                    {typeof v === 'string' ? highlightQueryInText(v, query) : rendered}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function PatternCard({ record, variant, selected, onSelect, readOnly = false, query = '', onOpen }: {
  record: PatternRecord;
  variant: 'pattern' | 'antipattern';
  selected: boolean;
  onSelect: (id: string, checked: boolean) => void;
  readOnly?: boolean;
  query?: string;
  onOpen: (record: PatternRecord, variant: 'pattern' | 'antipattern') => void;
}) {
  const questionText = record.question_text as string | null | undefined;
  const patternId = record.id as string | undefined;
  const isAnti = variant === 'antipattern';
  const resolved = isAnti && Number(record.success_count ?? 0) > 0;
  const isEnabled = record.is_enabled === true;

  const summaryKeys = isAnti ? SUMMARY_KEYS_AP : SUMMARY_KEYS_QP;

  const borderClass = selected
    ? 'border-primary/60 bg-primary/5'
    : isAnti
      ? resolved ? 'border-border/30 bg-muted/5' : 'border-red-500/20 bg-red-500/5'
      : 'border-border/50 bg-muted/10';

  return (
    <div className={`rounded-lg border transition-colors ${borderClass}`}>
      <div className="flex items-start gap-3 px-3 pt-3 pb-0">
        {!readOnly && (
          <div className="shrink-0 pt-0.5" onClick={(e) => e.stopPropagation()}>
            <input
              type="checkbox"
              checked={selected}
              onChange={(e) => patternId && onSelect(patternId, e.target.checked)}
              className="w-3.5 h-3.5 rounded border-border accent-primary cursor-pointer"
              aria-label="Select pattern"
            />
          </div>
        )}

        <div
          role="button"
          tabIndex={0}
          onClick={() => onOpen(record, variant)}
          onKeyDown={(e) => e.key === 'Enter' && onOpen(record, variant)}
          className="flex-1 min-w-0 pb-3 outline-none cursor-pointer"
        >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-foreground leading-snug">
                  {questionText
                    ? highlightQueryInText(questionText, query)
                    : <span className="italic text-muted-foreground/50">No question text</span>}
                </p>
                {isEnabled && !readOnly && (
                  <span className="inline-block text-[9px] uppercase tracking-wide font-medium text-emerald-600 dark:text-emerald-400 mt-0.5">Enabled</span>
                )}
              </div>

            </div>

            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5">
              {summaryKeys.map((k) => {
                const v = record[k];
                if (v === null || v === undefined || v === '') return null;
                const display = k === 'confidence_score'
                  ? `${(Number(v) * 100).toFixed(0)}% conf`
                  : k === 'last_seen'
                    ? `Last seen ${relativeDate(String(v))}`
                    : k === 'occurrence_count'
                      ? `Seen ${v}×`
                      : k === 'success_count' && Number(v) > 0
                        ? `Resolved ${v}×`
                      : k === 'liked_count' || k === 'disliked_count'
                        ? null
                        : `${k.replace(/_/g, ' ')}: ${v}`;
                if (!display) return null;
                return <span key={k} className="text-[11px] text-muted-foreground/60">{highlightQueryInText(display, query)}</span>;
              })}
              {!isAnti && (Number(record.liked_count ?? 0) > 0 || Number(record.disliked_count ?? 0) > 0) && (
                <span className="text-[11px]">
                  <span className="text-emerald-600 dark:text-emerald-400">↑{record.liked_count as number}</span>
                  {' '}
                  <span className="text-red-500">↓{record.disliked_count as number}</span>
                </span>
              )}
            </div>

            {Array.isArray(record.tables_used) && record.tables_used.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {(record.tables_used as string[]).map((t) => (
                  <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-muted/50 text-muted-foreground/60 font-mono">
                    {highlightQueryInText(t, query)}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
  );
}

function BulkActionBar({ count, total, onSelectAll, onClearAll, onEnable, onDisable, onDelete, enabling, disabling, variant }: {
  count: number;
  total: number;
  onSelectAll: () => void;
  onClearAll: () => void;
  onEnable: () => void;
  onDisable: () => void;
  onDelete: () => void;
  enabling: boolean;
  disabling: boolean;
  variant: 'pattern' | 'antipattern';
}) {
  const [showEnableInfo, setShowEnableInfo] = useState(false);
  const hasSelection = count > 0;

  return (
    <div className="rounded-lg border bg-background space-y-2 px-4 py-3 border-border/60">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        {/* Left: selection count + select-all / clear */}
        <div className="flex items-center gap-3 text-xs">
          {hasSelection ? (
            <>
              <span className="font-medium text-foreground">{count} selected</span>
              {count < total && (
                <button onClick={onSelectAll} className="text-primary hover:underline">
                  Select all {total}
                </button>
              )}
              <button onClick={onClearAll} className="text-muted-foreground hover:text-foreground transition-colors">
                Clear
              </button>
            </>
          ) : (
            <button onClick={onSelectAll} className="text-muted-foreground hover:text-foreground transition-colors">
              Select all {total}
            </button>
          )}
        </div>

        {/* Right: actions — only active when something is selected */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1.5"
              onClick={onEnable}
              disabled={!hasSelection || enabling}
            >
              {enabling ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              Enable
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1.5"
              onClick={onDisable}
              disabled={!hasSelection || disabling}
            >
              {disabling ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              Disable
            </Button>
            <button
              onClick={() => setShowEnableInfo((v) => !v)}
              className={`transition-colors ${showEnableInfo ? 'text-primary' : 'text-muted-foreground/50 hover:text-primary'}`}
              title="What does enabling do?"
            >
              <Info className="w-3.5 h-3.5" />
            </button>
          </div>

          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs text-destructive border-destructive/40 hover:bg-destructive/10 gap-1.5"
            onClick={onDelete}
            disabled={!hasSelection}
          >
            <Trash2 className="w-3 h-3" />
            Delete
          </Button>
        </div>
      </div>

      {showEnableInfo && (
        <div className="flex items-start gap-2 rounded-md bg-primary/8 border border-primary/20 px-3 py-2">
          <Info className="w-3.5 h-3.5 text-primary shrink-0 mt-0.5" />
          <p className="text-[11px] text-foreground/70 leading-relaxed">
            <span className="font-medium text-foreground">Enable</span> — marks the selected {variant === 'pattern' ? 'query patterns' : 'anti-patterns'} as active so the AI pipeline uses them when generating answers. Enabled patterns are matched against incoming queries to guide SQL generation and improve response accuracy.
            <br />
            <span className="font-medium text-foreground">Disable</span> — deactivates the selected patterns so they are no longer used by the AI pipeline, without deleting them.
          </p>
        </div>
      )}
    </div>
  );
}

function PatternPagination({
  page, totalPages, total, loading, onPrev, onNext,
}: {
  page: number; totalPages: number; total: number;
  loading: boolean; onPrev: () => void; onNext: () => void;
}) {
  if (totalPages <= 1) {
    return (
      <p className="text-[10px] text-muted-foreground/40 text-center pt-1">{total} total</p>
    );
  }
  return (
    <div className="flex items-center justify-between pt-2">
      <button
        onClick={onPrev}
        disabled={page <= 1 || loading}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-default transition-colors"
      >
        <ChevronLeft className="w-3.5 h-3.5" />
        Previous
      </button>
      <span className="text-[11px] text-muted-foreground/60 tabular-nums">
        {page} / {totalPages} · {total} total
      </span>
      <button
        onClick={onNext}
        disabled={page >= totalPages || loading}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-default transition-colors"
      >
        Next
        <ChevronRight className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

const VERSION_QUOTES = [
  '"We are the architects of the future." — CEO',
  '"The numbers don\'t lie. But they do tell stories." — CFO',
  '"Revenue is a team sport." — CRO',
  '"People first, always." — CPO',
  '"Operational excellence is not optional." — COO',
  '"Strategy is nothing without execution." — Chief of Staff',
  '"The cloud is just someone else\'s computer. Ours runs better." — President, DW/Cloud',
  '"Ship it. Then make it beautiful." — President, Apps & DE',
  '"Process is poetry in disguise." — EVP, BPS',
  '"Solutions aren\'t found - they\'re engineered." — EVP, Industry Solutions',
  '"Growth is a mindset." — VP, Corp Dev',
  '"We skipped v1. Too many features, not enough bugs." — QA Team',
];

// ─── Instructions panel ──────────────────────────────────────────────────

const CONTENT_LIMIT = 500;
const BUDGET_LIMIT = 1500;

function InstructionCard({
  instruction,
  onToggle,
  onUpdate,
  onDelete,
}: {
  instruction: UserInstruction;
  onToggle: (enabled: boolean) => void;
  onUpdate: (patch: { title?: string; content?: string }) => void;
  onDelete: () => void;
}) {
  const [title, setTitle] = useState(instruction.title);
  const [content, setContent] = useState(instruction.content);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const contentRef = useRef<HTMLTextAreaElement>(null);

  const savePatch = useCallback(
    (patch: { title?: string; content?: string }) => {
      const trimmed = { title: patch.title?.trim(), content: patch.content?.trim() };
      if ((trimmed.title !== undefined && trimmed.title === instruction.title) &&
          (trimmed.content !== undefined && trimmed.content === instruction.content)) return;
      onUpdate(trimmed);
    },
    [instruction.title, instruction.content, onUpdate],
  );

  return (
    <div
      className={`rounded-xl border transition-colors ${
        instruction.enabled
          ? 'border-border bg-muted/20 hover:border-border/80'
          : 'border-border/40 bg-muted/8 opacity-60'
      }`}
    >
      {/* Card header row: title + toggle */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-2">
        <div className="flex-1 min-w-0">
          <input
            ref={titleRef}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => savePatch({ title })}
            placeholder="Instruction name"
            className="w-full bg-transparent text-sm font-medium text-foreground placeholder:text-muted-foreground/50 outline-none border-b border-transparent focus:border-border/60 pb-0.5 transition-colors"
          />
        </div>
        <Switch
          checked={instruction.enabled}
          onCheckedChange={onToggle}
          aria-label={`${instruction.enabled ? 'Disable' : 'Enable'} instruction`}
          className="shrink-0 mt-0.5"
        />
      </div>

      {/* Content textarea */}
      <div className="px-4 pb-1">
        <textarea
          ref={contentRef}
          value={content}
          maxLength={CONTENT_LIMIT}
          onChange={(e) => {
            setContent(e.target.value);
            e.target.style.height = 'auto';
            e.target.style.height = `${e.target.scrollHeight}px`;
          }}
          onBlur={() => savePatch({ content })}
          placeholder="Describe what MTI Brain should always do…"
          rows={3}
          className="w-full resize-none bg-transparent text-xs text-muted-foreground placeholder:text-muted-foreground/40 outline-none leading-relaxed border-none focus:text-foreground transition-colors"
          style={{ minHeight: '4rem' }}
        />
        <div className="flex justify-end pb-1">
          <span className={`text-[10px] tabular-nums ${content.length >= CONTENT_LIMIT ? 'text-amber-500 font-medium' : 'text-muted-foreground/40'}`}>
            {CONTENT_LIMIT - content.length} remaining
          </span>
        </div>
      </div>

      {/* Card footer: delete */}
      <div className="flex items-center justify-end px-4 pb-3">
        {confirmDelete ? (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Delete this instruction?</span>
            <button
              onClick={onDelete}
              className="text-destructive hover:text-destructive/80 font-medium transition-colors"
            >
              Yes
            </button>
            <button
              onClick={() => setConfirmDelete(false)}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirmDelete(true)}
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground/50 hover:text-destructive transition-colors"
            aria-label="Delete instruction"
          >
            <Trash2 className="w-3 h-3" />
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

function InstructionsPanel() {
  const { instructions, loading, lastFetched, fetchInstructions, addInstruction, updateInstruction, removeInstruction } =
    useInstructionsStore();
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newContent, setNewContent] = useState('');
  const [saving, setSaving] = useState(false);
  const newTitleRef = useRef<HTMLInputElement>(null);

  const handleAddFromPattern = useCallback((title: string, content: string) => {
    setNewTitle(title);
    setNewContent(content);
    setAdding(true);
    setTimeout(() => newTitleRef.current?.focus(), 50);
  }, []);

  useEffect(() => {
    if (!lastFetched) fetchInstructions();
  }, [lastFetched, fetchInstructions]);

  const activeCharCount = instructions
    .filter((i) => i.enabled)
    .reduce((sum, i) => sum + i.content.length, 0);
  const budgetPct = Math.min(100, (activeCharCount / BUDGET_LIMIT) * 100);
  const budgetOver = activeCharCount > BUDGET_LIMIT;
  const budgetBarColor = budgetOver
    ? 'bg-destructive'
    : budgetPct >= 80
      ? 'bg-amber-500'
      : 'bg-primary';

  const handleAdd = async () => {
    const title = newTitle.trim();
    const content = newContent.trim();
    if (!title || !content) return;
    setSaving(true);
    try {
      await addInstruction({ title, content, enabled: true });
      setAdding(false);
      setNewTitle('');
      setNewContent('');
    } catch {
      toast.error('Failed to save instruction');
    } finally {
      setSaving(false);
    }
  };

  const handleStartAdding = () => {
    setAdding(true);
    setTimeout(() => newTitleRef.current?.focus(), 50);
  };

  if (loading && !lastFetched) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-x-10 gap-y-8">
      {/* Left: Instructions list */}
      <div className="space-y-4 min-w-0">
        {/* Info strip */}
        <p className="text-xs text-muted-foreground">
          Sent to the AI on every query. When an instruction conflicts with learned feedback, the instruction wins.
        </p>

        {/* Header row: status + budget bar + add button */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex-1 min-w-0 space-y-1.5">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                {instructions.length === 0
                  ? 'No instructions yet. Add one to get started.'
                  : `${instructions.filter((i) => i.enabled).length} of ${instructions.length} active`}
              </p>
              {budgetOver && (
                <span className="text-[11px] text-destructive font-medium">Too many active instructions</span>
              )}
              {!budgetOver && budgetPct >= 80 && (
                <span className="text-[11px] text-amber-500">Getting heavy — consider disabling some</span>
              )}
            </div>
            {instructions.some((i) => i.enabled) && (
              <div className="h-1 w-full rounded-full bg-muted/50 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${budgetBarColor}`}
                  style={{ width: `${budgetPct}%` }}
                />
              </div>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={handleStartAdding}
            className="shrink-0 flex items-center gap-1.5 h-8 text-xs"
          >
            <Plus className="w-3.5 h-3.5" />
            Add instruction
          </Button>
        </div>

        {/* New instruction form */}
        {adding && (
          <div className="rounded-xl border border-primary/40 bg-primary/5 p-4 space-y-3">
            <input
              ref={newTitleRef}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="Instruction name (e.g. Acronym Glossary)"
              className="w-full bg-transparent text-sm font-medium text-foreground placeholder:text-muted-foreground/50 outline-none border-b border-border/60 pb-0.5"
            />
            <div>
              <textarea
                value={newContent}
                maxLength={CONTENT_LIMIT}
                onChange={(e) => {
                  setNewContent(e.target.value);
                  e.target.style.height = 'auto';
                  e.target.style.height = `${e.target.scrollHeight}px`;
                }}
                placeholder="What should MTI Brain always do? (e.g. List all acronyms as a table at the end of every response)"
                rows={3}
                className="w-full resize-none bg-transparent text-xs text-muted-foreground placeholder:text-muted-foreground/40 outline-none leading-relaxed border-none focus:text-foreground"
                style={{ minHeight: '4rem' }}
              />
              <div className="flex justify-end">
                <span className={`text-[10px] tabular-nums ${newContent.length >= CONTENT_LIMIT ? 'text-amber-500 font-medium' : 'text-muted-foreground/40'}`}>
                  {CONTENT_LIMIT - newContent.length} remaining
                </span>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => { setAdding(false); setNewTitle(''); setNewContent(''); }}
                className="h-7 text-xs"
                disabled={saving}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleAdd}
                className="h-7 text-xs"
                disabled={saving || !newTitle.trim() || !newContent.trim()}
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Save'}
              </Button>
            </div>
          </div>
        )}

        {/* Instruction cards */}
        <div className="space-y-3">
          {instructions.map((instr) => (
            <InstructionCard
              key={instr.id}
              instruction={instr}
              onToggle={(enabled) => {
                updateInstruction(instr.id, { enabled }).catch(() => toast.error('Failed to update'));
              }}
              onUpdate={(patch) => {
                updateInstruction(instr.id, patch).catch(() => toast.error('Failed to save changes'));
              }}
              onDelete={() => {
                removeInstruction(instr.id).catch(() => toast.error('Failed to delete'));
              }}
            />
          ))}
        </div>

        {instructions.length === 0 && !adding && (
          <div className="flex flex-col items-center justify-center py-12 text-center border border-dashed border-border/40 rounded-xl">
            <BookText className="w-8 h-8 text-muted-foreground/30 mb-3" />
            <p className="text-sm text-muted-foreground/60">No instructions yet</p>
            <p className="text-xs text-muted-foreground/40 mt-1">
              Instructions apply to every response across all chats
            </p>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleStartAdding}
              className="mt-4 flex items-center gap-1.5 text-xs"
            >
              <Plus className="w-3.5 h-3.5" />
              Add your first instruction
            </Button>
          </div>
        )}
      </div>

      {/* Right: Feedback & learned patterns */}
      <div className="border-l border-border/40 pl-8 space-y-2 min-w-0">
        <FeedbackHistorySection />
        <FeedbackPatternsSection onAddInstruction={handleAddFromPattern} />
      </div>
    </div>
  );
}

function FeedbackHistorySection() {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-t border-border/40 mt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between py-3 group outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        aria-expanded={open}
      >
        <div className="text-left">
          <p className="text-xs font-medium text-foreground group-hover:text-foreground/80 transition-colors">
            What the AI has learned from you
          </p>
          {!open && (
            <p className="text-[11px] text-muted-foreground/50 mt-0.5">
              Review feedback to spot conflicts with instructions above
            </p>
          )}
        </div>
        <ChevronRight
          className={`w-3.5 h-3.5 text-muted-foreground/50 shrink-0 transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
        />
      </button>

      {open && (
        <div className="pb-4">
          <p className="text-[11px] text-muted-foreground/60 mb-3">
            If anything here conflicts with an instruction above, the instruction wins — disable the instruction to let this feedback apply instead.
          </p>
          <FeedbackHistory />
        </div>
      )}
    </div>
  );
}

const FB_PER_PAGE = 10;

function relativeDate(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days} days ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks} week${weeks > 1 ? 's' : ''} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months > 1 ? 's' : ''} ago`;
  return `${Math.floor(days / 365)} year${Math.floor(days / 365) > 1 ? 's' : ''} ago`;
}

function isStale(item: { created_at: string; last_triggered_at: string | null }): boolean {
  const STALE_DAYS = 60;
  const now = Date.now();
  const createdAge = Math.floor((now - new Date(item.created_at).getTime()) / 86_400_000);
  if (createdAge <= STALE_DAYS) return false;
  if (item.last_triggered_at) {
    const triggeredAge = Math.floor((now - new Date(item.last_triggered_at).getTime()) / 86_400_000);
    return triggeredAge > STALE_DAYS;
  }
  return true;
}

function FeedbackHistory() {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<FeedbackHistoryPage | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const result = await listFeedbackHistory(p, FB_PER_PAGE);
      setData(result);
      setPage(p);
    } catch {
      // non-fatal
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(1); }, [load]);

  if (!data && loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!data || data.total === 0) {
    return (
      <p className="text-xs text-muted-foreground/50 py-4 text-center">
        No feedback given yet — rate responses in any chat to start building a preference profile.
      </p>
    );
  }

  return (
    <div className="space-y-1">
      {/* Entries */}
      <div className="divide-y divide-border/30">
        {data.items.map((item) => {
          const stale = isStale(item);
          return (
            <div key={item.id} className={`flex items-start gap-3 py-3 ${stale ? 'opacity-50' : ''}`}>
              {/* Sentiment icon */}
              <div className={`shrink-0 mt-0.5 rounded-full p-1 ${item.liked ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-red-500/10 text-red-500 dark:text-red-400'}`}>
                {item.liked
                  ? <ThumbsUp className="w-3 h-3" />
                  : <ThumbsDown className="w-3 h-3" />}
              </div>

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-xs text-foreground leading-snug">
                    {item.comment
                      ? `"${item.comment}"`
                      : <span className="text-muted-foreground/60 italic">
                          {item.liked ? 'Marked response helpful' : 'Marked response unhelpful'}
                        </span>
                    }
                  </p>
                  {stale && (
                    <span className="shrink-0 text-[9px] text-muted-foreground/40 italic whitespace-nowrap">
                      Not recently applied
                    </span>
                  )}
                </div>
                {item.question_text && (
                  <p className="text-[11px] text-muted-foreground/55 mt-0.5 leading-snug truncate">
                    {item.question_text}
                  </p>
                )}
                <div className="flex items-center gap-2 mt-0.5">
                  <p className="text-[10px] text-muted-foreground/35">
                    {relativeDate(item.created_at)}
                  </p>
                  {item.feedback_type && item.feedback_type !== 'general' && (
                    <span className="text-[9px] uppercase tracking-wide text-muted-foreground/30 font-medium">
                      {item.feedback_type}
                    </span>
                  )}
                  {item.trigger_count > 0 && (
                    <span className="text-[9px] text-muted-foreground/30">
                      applied {item.trigger_count}×
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Pagination */}
      {data.total_pages > 1 && (
        <div className="flex items-center justify-between pt-3">
          <button
            onClick={() => load(page - 1)}
            disabled={page <= 1 || loading}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-default transition-colors"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
            Previous
          </button>
          <span className="text-[11px] text-muted-foreground/60 tabular-nums">
            {page} / {data.total_pages}
          </span>
          <button
            onClick={() => load(page + 1)}
            disabled={page >= data.total_pages || loading}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-default transition-colors"
          >
            Next
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Total count */}
      <p className="text-[10px] text-muted-foreground/40 text-center pt-1">
        {data.total} feedback {data.total === 1 ? 'entry' : 'entries'} total
      </p>
    </div>
  );
}

const DISMISSED_PATTERNS_KEY = 'mti_brain_dismissed_patterns';

function FeedbackPatternsSection({
  onAddInstruction,
}: {
  onAddInstruction: (title: string, content: string) => void;
}) {
  const [patterns, setPatterns] = useState<FeedbackPattern[] | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem(DISMISSED_PATTERNS_KEY);
      return new Set(stored ? JSON.parse(stored) : []);
    } catch {
      return new Set();
    }
  });

  useEffect(() => {
    getFeedbackPatterns()
      .then(setPatterns)
      .catch(() => setPatterns([]));
  }, []);

  const dismiss = (key: string) => {
    setDismissed((prev) => {
      const next = new Set(prev).add(key);
      try { localStorage.setItem(DISMISSED_PATTERNS_KEY, JSON.stringify([...next])); } catch {}
      return next;
    });
  };

  const visible = (patterns ?? []).filter((p) => !dismissed.has(p.topic_key));
  if (visible.length === 0) return null;

  return (
    <div className="border-t border-border/40 mt-2 pt-3">
      <p className="text-xs font-medium text-foreground mb-1">Patterns we noticed</p>
      <p className="text-[11px] text-muted-foreground/60 mb-3">
        These topics appear repeatedly in your feedback — add them as standing instructions so they apply on every query.
      </p>
      <div className="space-y-2">
        {visible.slice(0, 5).map((p) => (
          <div
            key={p.topic_key}
            className="flex items-start justify-between gap-3 rounded-lg border border-border/50 bg-muted/20 px-3 py-2.5"
          >
            <div className="min-w-0">
              <p className="text-xs font-medium text-foreground truncate">{p.suggested_title}</p>
              <p className="text-[11px] text-muted-foreground/60 mt-0.5 leading-snug line-clamp-1">
                {p.sample_comments[0]}
              </p>
              <p className="text-[10px] text-muted-foreground/40 mt-0.5">
                Mentioned {p.count} time{p.count !== 1 ? 's' : ''}
                {p.liked_count > 0 && p.disliked_count > 0 && ` · ${p.liked_count} liked · ${p.disliked_count} disliked`}
              </p>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => onAddInstruction(p.suggested_title, p.sample_comments[0] ?? p.topic_key)}
              >
                Add as instruction
              </Button>
              <button
                onClick={() => dismiss(p.topic_key)}
                className="text-muted-foreground/30 hover:text-muted-foreground transition-colors"
                aria-label="Dismiss"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AboutBlock() {
  const [hovered, setHovered] = useState(false);
  const [quote] = useState(
    () => VERSION_QUOTES[Math.floor(Math.random() * VERSION_QUOTES.length)],
  );
  const stack = ['LangGraph', 'Neo4j', 'PostgreSQL', 'Next.js', 'AWS Bedrock'];
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-16 gap-y-8">
      {/* Left: Product info */}
      <div className="space-y-6">
        <div>
          <p className="text-base font-semibold text-foreground">MTI Brain</p>
          <p className="text-sm text-muted-foreground mt-1.5 leading-relaxed">
            AI-powered decision intelligence platform. Ask questions about your business data in plain English and get structured insights, SQL, and charts.
          </p>
        </div>
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/50">Powered by</p>
          <div className="flex flex-wrap gap-1.5">
            {stack.map((tag) => (
              <span
                key={tag}
                className="px-2.5 py-1 text-xs rounded-lg bg-muted/60 text-muted-foreground border border-border/50"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Right: Version card */}
      <div className="space-y-4">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/50">Version info</p>
        <div className="rounded-xl border border-border bg-muted/20 p-5 space-y-3">
          <div className="flex items-center justify-between gap-4">
            <span className="text-xs text-muted-foreground">Version</span>
            <span
              onMouseEnter={() => setHovered(true)}
              onMouseLeave={() => setHovered(false)}
              className="text-xs font-mono text-foreground cursor-default"
            >
              {hovered
                ? <span className="italic font-sans text-muted-foreground">{quote}</span>
                : (process.env.NEXT_PUBLIC_APP_VERSION ?? '—')}
            </span>
          </div>
          <div className="h-px bg-border/40" />
          <div className="flex items-center justify-between gap-4">
            <span className="text-xs text-muted-foreground">Platform</span>
            <span className="text-xs text-foreground">Web</span>
          </div>
          {/* <div className="h-px bg-border/40" />
          <div className="flex items-center justify-between gap-4">
            <span className="text-xs text-muted-foreground">Support</span>
            <span className="text-xs text-foreground">mtiinnovation.com</span>
          </div> */}
        </div>
      </div>
    </div>
  );
}

function makeMatcher(query: string): (haystack: string) => boolean {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return () => true;
  return (haystack: string) => {
    const hay = haystack.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  };
}

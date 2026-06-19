'use client';

import {
  useEffect,
  useMemo,
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
  type ThinkingPlacement,
} from '@/lib/store/preferences';
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
    if (SECTIONS.some((s) => s.id === hash)) setActiveSection(hash);
  }, []);

  const matches = useMemo(() => makeMatcher(query), [query]);
  const v = (keywords: string) => matches(keywords);

  const sectionVisible: Record<SectionId, boolean> = {
    'response-style': TONE_OPTIONS.some((o) =>
      matches(`response style tone ${o.label} ${o.description}`),
    ),
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
          {SECTIONS.map((s) => {
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
      <div className="flex-1 overflow-y-auto">
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
  const titles: Record<SectionId, { title: string; description: string }> = {
    'response-style': { title: 'Response style', description: 'How MTI Brain frames its answers.' },
    display: { title: 'Display', description: 'Control what\'s shown alongside responses.' },
    appearance: { title: 'Appearance', description: 'Adjust layout density and accessibility.' },
    notifications: { title: 'Notifications', description: 'Configure how MTI Brain alerts you.' },
    about: { title: 'About', description: '' },
  };

  const { title, description } = titles[activeSection];

  return (
    <div className="px-8 pt-5 pb-8">
      {/* Section heading */}
      <div className="mb-6 pb-4 border-b border-border max-w-3xl">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {description && (
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        )}
      </div>

      {/* Section content */}
      <div className="max-w-3xl">
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
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {options.map((o) => {
        const isSelected = value === o.value && hydrated;
        const isDefault = o.value === defaultValue;
        return (
          <div key={o.value} className="flex flex-col">
            <button
              onClick={() => onChange(o.value)}
              aria-pressed={isSelected}
              className={`flex-1 rounded-xl px-4 py-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background transition-colors ${
                isSelected
                  ? 'ring-2 ring-primary bg-primary/8 text-foreground border border-primary/30'
                  : 'bg-muted/40 hover:bg-muted/70 text-muted-foreground border border-transparent'
              }`}
            >
              <span className="text-sm font-semibold block text-foreground">{o.label}</span>
              <span className="text-xs text-muted-foreground leading-snug block mt-1">
                {o.description}
              </span>
            </button>
            {/* Always render — keeps card heights equal whether or not this is the default */}
            <span
              aria-hidden={!isDefault}
              className="block text-center mt-1 text-[9px] uppercase tracking-wider text-muted-foreground/50 select-none h-4 leading-4"
            >
              {isDefault ? 'default' : ''}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function DisplayContent({
  v,
  showSQL, setShowSQL,
  autoShowCharts, setAutoShowCharts,
  showFollowUps, setShowFollowUps,
  showReasoning, setShowReasoning,
  thinkingPlacement, setThinkingPlacement,
  defaultDataView, setDefaultDataView,
  maxResultRows, setMaxResultRows,
}: {
  v: (k: string) => boolean;
  showSQL: boolean; setShowSQL: (v: boolean) => void;
  autoShowCharts: boolean; setAutoShowCharts: (v: boolean) => void;
  showFollowUps: boolean; setShowFollowUps: (v: boolean) => void;
  showReasoning: boolean; setShowReasoning: (v: boolean) => void;
  thinkingPlacement: ThinkingPlacement; setThinkingPlacement: (v: ThinkingPlacement) => void;
  defaultDataView: DefaultDataView; setDefaultDataView: (v: DefaultDataView) => void;
  maxResultRows: number; setMaxResultRows: (v: number) => void;
}) {
  return (
    <div className="space-y-6">
      <div className="divide-y divide-border/50">
        {v('show sql queries display') && (
          <ToggleRow
            label="Show SQL"
            description="Display the generated SQL alongside results."
            checked={showSQL}
            onCheckedChange={setShowSQL}
            isDefault={showSQL === PREFERENCES_DEFAULTS.showSQL}
          />
        )}
        {v('auto show charts visualizations') && (
          <ToggleRow
            label="Auto-show charts"
            description="Automatically render data visualizations when the result fits a chart."
            checked={autoShowCharts}
            onCheckedChange={setAutoShowCharts}
            isDefault={autoShowCharts === PREFERENCES_DEFAULTS.autoShowCharts}
          />
        )}
        {v('follow-up suggestions follow up') && (
          <ToggleRow
            label="Follow-up suggestions"
            description="Show suggested follow-up questions after responses."
            checked={showFollowUps}
            onCheckedChange={setShowFollowUps}
            isDefault={showFollowUps === PREFERENCES_DEFAULTS.showFollowUps}
          />
        )}
        {v('show reasoning thinking process') && (
          <ToggleRow
            label="Show reasoning"
            description="Display the thinking-process panel for each query."
            checked={showReasoning}
            onCheckedChange={setShowReasoning}
            isDefault={showReasoning === PREFERENCES_DEFAULTS.showReasoning}
          />
        )}
      </div>

      {v('thinking placement inline sidebar position') && (
        <SettingBlock
          label="Thinking panel placement"
          description="Choose where the reasoning/thinking steps are displayed."
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-md mt-3">
            {([
              {
                value: 'inline' as ThinkingPlacement,
                label: 'Inline',
                description: 'Show thinking steps directly in the conversation stream, above the response.',
              },
              {
                value: 'sidebar' as ThinkingPlacement,
                label: 'Side panel',
                description: 'Show thinking steps in a dedicated sidebar panel beside the conversation.',
              },
            ]).map((option) => {
              const isSelected = thinkingPlacement === option.value;
              const isDefault = option.value === PREFERENCES_DEFAULTS.thinkingPlacement;
              return (
                <div key={option.value} className="flex flex-col">
                  <button
                    onClick={() => setThinkingPlacement(option.value)}
                    aria-pressed={isSelected}
                    className={`flex-1 rounded-xl px-4 py-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background transition-colors ${
                      isSelected
                        ? 'ring-2 ring-primary bg-primary/8 text-foreground border border-primary/30'
                        : 'bg-muted/40 hover:bg-muted/70 text-muted-foreground border border-transparent'
                    }`}
                  >
                    <span className="text-sm font-semibold block text-foreground">{option.label}</span>
                    <span className="text-xs text-muted-foreground leading-snug block mt-1">
                      {option.description}
                    </span>
                  </button>
                  <DefaultTag visible={isDefault} />
                </div>
              );
            })}
          </div>
        </SettingBlock>
      )}

      {v('default data view sql table') && (
        <SettingBlock
          label="Default data view"
          description="Which tab opens first when query data arrives."
        >
          <div className="grid grid-cols-2 gap-2 max-w-xs mt-2">
            {(['sql', 'table'] as DefaultDataView[]).map((view) => {
              const isSelected = defaultDataView === view;
              const isDefault = view === PREFERENCES_DEFAULTS.defaultDataView;
              return (
                <div key={view} className="flex flex-col">
                  <button
                    onClick={() => setDefaultDataView(view)}
                    aria-pressed={isSelected}
                    className={`rounded-lg px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors ${
                      isSelected
                        ? 'ring-2 ring-primary bg-primary/8 font-medium text-foreground border border-primary/30'
                        : 'bg-muted/40 hover:bg-muted/70 text-muted-foreground border border-transparent'
                    }`}
                  >
                    {view === 'sql' ? 'SQL' : 'Data'}
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
          <div className="grid grid-cols-4 gap-2 max-w-xs mt-2">
            {ROW_OPTIONS.map((rows) => {
              const isSelected = maxResultRows === rows;
              const isDefault = rows === PREFERENCES_DEFAULTS.maxResultRows;
              return (
                <div key={rows} className="flex flex-col">
                  <button
                    onClick={() => setMaxResultRows(rows)}
                    aria-pressed={isSelected}
                    className={`rounded-lg px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors tabular-nums ${
                      isSelected
                        ? 'ring-2 ring-primary bg-primary/8 font-medium text-foreground border border-primary/30'
                        : 'bg-muted/40 hover:bg-muted/70 text-muted-foreground border border-transparent'
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
    <div className="divide-y divide-border/50">
      {v('density compact comfortable spacing rows') && (
        <div className="pb-5">
          <SettingBlock
            label="Density"
            description="Compact tightens row padding for more on screen at once."
          >
            <div className="grid grid-cols-2 gap-2 max-w-xs mt-2">
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
                          ? 'ring-2 ring-primary bg-primary/8 font-medium text-foreground border border-primary/30'
                          : 'bg-muted/40 hover:bg-muted/70 text-muted-foreground border border-transparent'
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
        </div>
      )}
      {v('high contrast accessibility bold text sharp') && (
        <ToggleRow
          label="High contrast"
          description="Makes text darker and borders sharper — easier to read in bright environments."
          checked={highContrast}
          onCheckedChange={setHighContrast}
        />
      )}
    </div>
  );
}

// ─── Primitive components ────────────────────────────────────────────────

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

function ToggleRow({
  label, description, checked, onCheckedChange, disabled, isDefault,
}: {
  label: string; description: string;
  checked: boolean; onCheckedChange: (v: boolean) => void;
  disabled?: boolean; isDefault?: boolean;
}) {
  return (
    <div className={`flex items-center justify-between gap-4 py-3.5 ${disabled ? 'opacity-50' : ''}`}>
      <div className="min-w-0">
        <p className="text-sm text-foreground flex items-center gap-2">
          {label}
          {isDefault === false && (
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground/60 font-medium">
              changed
            </span>
          )}
        </p>
        <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
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
    <div className="divide-y divide-border/50">
      {v('notify when answers finish notifications stream completion') && (
        <ToggleRow
          label="Notify when answers finish"
          description="Pings you when a stream completes and you're not on that chat."
          checked={enabled}
          onCheckedChange={(val) => setNotifyOnComplete(val ? 'when-hidden' : 'off')}
          isDefault={(enabled ? 'when-hidden' : 'off') === PREFERENCES_DEFAULTS.notifyOnComplete}
        />
      )}
      {v('play sound ping audio notifications') && (
        <ToggleRow
          label="Play a sound"
          description={enabled ? 'Soft ping alongside notifications.' : 'Enable notifications above to use this.'}
          checked={enabled && notifySound}
          onCheckedChange={setNotifySound}
          disabled={!enabled}
          isDefault={notifySound === PREFERENCES_DEFAULTS.notifySound}
        />
      )}
      {v('browser permission notifications') && (
        <div className="flex items-center justify-between gap-4 py-3.5">
          <div className="min-w-0">
            <p className="text-sm text-foreground">Browser permission</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {permission === 'granted' && 'Allowed — to revoke, click the lock icon in the address bar.'}
              {permission === 'default' && 'Not yet granted — click Enable to allow.'}
              {permission === 'denied' && "Blocked — change in your browser's site settings to re-enable."}
              {permission === 'unsupported' && "Your browser doesn't support desktop notifications."}
            </p>
          </div>
          {permission === 'default' && (
            <Button size="sm" onClick={handleEnable} className="shrink-0">Enable</Button>
          )}
          {permission === 'granted' && (
            <span className="shrink-0 inline-flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" aria-hidden />
              Allowed
            </span>
          )}
          {permission === 'denied' && (
            <span className="shrink-0 text-xs text-muted-foreground">Blocked</span>
          )}
        </div>
      )}
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

function AboutBlock() {
  const [hovered, setHovered] = useState(false);
  const [quote] = useState(
    () => VERSION_QUOTES[Math.floor(Math.random() * VERSION_QUOTES.length)],
  );
  return (
    <div className="space-y-3 text-sm text-muted-foreground">
      <p className="text-foreground font-medium">MTI Brain</p>
      <p>AI-powered decision intelligence platform.</p>
      <p
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="text-xs text-muted-foreground/60 hover:text-muted-foreground transition-colors cursor-default"
      >
        {hovered ? <span className="italic">{quote}</span> : `Version ${process.env.NEXT_PUBLIC_APP_VERSION}`}
      </p>
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

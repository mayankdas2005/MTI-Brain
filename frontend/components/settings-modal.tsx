'use client';

import { useEffect, useState } from 'react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  usePreferencesStore,
  type ResponseTone,
  type DefaultDataView,
} from '@/lib/store/preferences';
import {
  getPermission,
  notificationsSupported,
  requestPermission,
  type NotificationPermissionState,
} from '@/lib/utils/notifications';

interface SettingsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const TONE_OPTIONS: { value: ResponseTone; label: string; description: string }[] = [
  { value: 'consultant', label: 'Executive', description: 'Analytical, insight-driven summaries' },
  { value: 'operator', label: 'Operator', description: 'Direct, actionable instructions' },
  { value: 'brief', label: 'Brief', description: 'Key facts only, minimal narrative' },
];

const ROW_OPTIONS = [50, 100, 200, 500];

export function SettingsModal({ open, onOpenChange }: SettingsModalProps) {
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
  const hydrated = usePreferencesStore((s) => s.hydrated);

  // Only show selection styling after user prefs have loaded - prevents flash
  const selected = (isSelected: boolean) =>
    isSelected && hydrated
      ? 'ring-2 ring-primary bg-primary/10 text-foreground'
      : 'bg-muted/50 hover:bg-accent text-muted-foreground';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-full max-w-md max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Customize how MTI Brain responds and displays data
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-2">
          {/* Response Tone */}
          <div className="space-y-3">
            <Label className="text-sm font-medium">Response style</Label>
            <div className="grid grid-cols-3 gap-2">
              {TONE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setResponseTone(opt.value)}
                  className={`rounded-lg px-3 py-2.5 text-left outline-none ${selected(responseTone === opt.value)}`}
                >
                  <span className="text-sm font-medium block">{opt.label}</span>
                  <span className="text-[11px] text-muted-foreground leading-tight block mt-0.5">
                    {opt.description}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Display Toggles */}
          <div className="space-y-4">
            <Label className="text-sm font-medium">Display</Label>

            <SettingRow
              label="Show SQL queries"
              description="Display the generated SQL alongside results"
              checked={showSQL}
              onCheckedChange={setShowSQL}
            />
            <SettingRow
              label="Auto-show charts"
              description="Automatically render data visualizations"
              checked={autoShowCharts}
              onCheckedChange={setAutoShowCharts}
            />
            <SettingRow
              label="Follow-up suggestions"
              description="Show suggested follow-up questions after responses"
              checked={showFollowUps}
              onCheckedChange={setShowFollowUps}
            />
            <SettingRow
              label="Show reasoning"
              description="Display the thinking process for each query"
              checked={showReasoning}
              onCheckedChange={setShowReasoning}
            />
          </div>

          <div className="border-t border-border" />

          {/* Default Data View */}
          <div className="space-y-3">
            <div>
              <Label className="text-sm font-medium">Show results as</Label>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Which tab opens first when query results arrive
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {(['sql', 'table'] as DefaultDataView[]).map((view) => (
                <button
                  key={view}
                  onClick={() => setDefaultDataView(view)}
                  className={`rounded-lg px-3 py-2 text-sm outline-none ${
                    defaultDataView === view
                      ? 'ring-2 ring-primary bg-primary/10 font-medium text-foreground'
                      : 'bg-muted/50 hover:bg-accent text-muted-foreground'
                  }`}
                >
                  {view === 'sql' ? 'SQL Query' : 'Data Table'}
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Max Result Rows */}
          <div className="space-y-3">
            <div>
              <Label className="text-sm font-medium">Max results per query</Label>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Higher values return more data but take longer
              </p>
            </div>
            <div className="grid grid-cols-4 gap-2">
              {ROW_OPTIONS.map((rows) => (
                <button
                  key={rows}
                  onClick={() => setMaxResultRows(rows)}
                  className={`rounded-lg px-3 py-2 text-sm outline-none ${
                    maxResultRows === rows
                      ? 'ring-2 ring-primary bg-primary/10 font-medium text-foreground'
                      : 'bg-muted/50 hover:bg-accent text-muted-foreground'
                  }`}
                >
                  {rows}
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Notifications */}
          <NotificationsSection
            notifyOnComplete={notifyOnComplete}
            setNotifyOnComplete={setNotifyOnComplete}
            notifySound={notifySound}
            setNotifySound={setNotifySound}
            modalOpen={open}
          />

          <div className="border-t border-border" />

          {/* About */}
          <VersionEasterEgg />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function NotificationsSection({
  notifyOnComplete,
  setNotifyOnComplete,
  notifySound,
  setNotifySound,
  modalOpen,
}: {
  notifyOnComplete: 'when-hidden' | 'off';
  setNotifyOnComplete: (val: 'when-hidden' | 'off') => void;
  notifySound: boolean;
  setNotifySound: (val: boolean) => void;
  modalOpen: boolean;
}) {
  const [permission, setPermission] = useState<NotificationPermissionState>(
    notificationsSupported() ? 'default' : 'unsupported',
  );

  // Re-read permission when the modal opens — covers the case where the user
  // changed it in another tab or via browser site settings.
  useEffect(() => {
    if (!modalOpen) return;
    setPermission(getPermission());
  }, [modalOpen]);

  const handleEnable = async () => {
    const next = await requestPermission();
    setPermission(next);
  };

  const enabled = notifyOnComplete === 'when-hidden';

  return (
    <div className="space-y-4">
      <Label className="text-sm font-medium">Notifications</Label>

      <SettingRow
        label="Notify when answers finish"
        description="Pings you when a stream completes and you're not on that chat"
        checked={enabled}
        onCheckedChange={(v) => setNotifyOnComplete(v ? 'when-hidden' : 'off')}
      />
      <SettingRow
        label="Play a sound"
        description={
          enabled
            ? 'Soft ping alongside notifications'
            : 'Enable notifications above to use this'
        }
        checked={enabled && notifySound}
        onCheckedChange={setNotifySound}
        disabled={!enabled}
      />

      {/* Permission status pill */}
      <div className="flex items-center justify-between gap-4 pt-1">
        <div className="min-w-0">
          <p className="text-sm text-foreground">Browser permission</p>
          <p className="text-[11px] text-muted-foreground">
            {permission === 'granted' &&
              'Allowed — to revoke, click the lock/site-info icon in the address bar.'}
            {permission === 'default' && 'Not yet granted — click Enable to allow'}
            {permission === 'denied' &&
              'Blocked — change in your browser\'s site settings to re-enable'}
            {permission === 'unsupported' &&
              'Your browser doesn\'t support desktop notifications'}
          </p>
        </div>
        {permission === 'default' && (
          <button
            type="button"
            onClick={handleEnable}
            className="shrink-0 rounded-md bg-foreground text-background text-xs px-3 py-1.5 font-medium hover:opacity-85 transition-opacity"
          >
            Enable
          </button>
        )}
        {permission === 'granted' && (
          <span className="shrink-0 inline-flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" aria-hidden />
            Allowed
          </span>
        )}
        {permission === 'denied' && (
          <span className="shrink-0 text-[11px] text-muted-foreground">Blocked</span>
        )}
      </div>
    </div>
  );
}

function SettingRow({
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
}: {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className={`flex items-center justify-between gap-4 ${disabled ? 'opacity-50' : ''}`}>
      <div className="min-w-0">
        <p className="text-sm text-foreground">{label}</p>
        <p className="text-[11px] text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} />
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

function VersionEasterEgg() {
  const [hovered, setHovered] = useState(false);
  // Pick a random quote once per mount (each time settings opens)
  const [quote] = useState(() => VERSION_QUOTES[Math.floor(Math.random() * VERSION_QUOTES.length)]);

  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">
        MTI Brain - AI-powered decision intelligence
      </p>
      <p
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="text-xs text-muted-foreground/60 hover:text-muted-foreground transition-colors cursor-default"
      >
        {hovered
          ? <span className="italic">{quote}</span>
          : 'Version 2.0.0'}
      </p>
    </div>
  );
}

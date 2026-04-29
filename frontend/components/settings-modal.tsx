'use client';

import { useState } from 'react';

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
  const hydrated = usePreferencesStore((s) => s.hydrated);

  // Only show selection styling after user prefs have loaded — prevents flash
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
            Customize how Quest responds and displays data
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

          {/* About */}
          <VersionEasterEgg />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SettingRow({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <p className="text-sm text-foreground">{label}</p>
        <p className="text-[11px] text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

const VERSION_QUOTES = [
  '"We are the architects of the future." — Sameer Kishore, CEO',
  '"The numbers don\'t lie. But they do tell stories." — Mayank Agrawal, CFO',
  '"Revenue is a team sport." — Puneet Kumar, CRO',
  '"People first, always." — Arlene LaBorde, CPO',
  '"Operational excellence is not optional." — Mike Riep, COO',
  '"Strategy is nothing without execution." — Chitra Vivek, Chief of Staff',
  '"The cloud is just someone else\'s computer. Ours runs better." — Olivier Crene, President DW/Cloud',
  '"Ship it. Then make it beautiful." — Bala Ramakrishna, President Apps & DE',
  '"Process is poetry in disguise." — Natalie Heroux, EVP BPS',
  '"Solutions aren\'t found — they\'re engineered." — Abhilash Kaduthanum, EVP Industry Solutions',
  '"Growth is a mindset." — Eric Wong, VP Corp Dev',
  '"We skipped v1. Too many features, not enough bugs." — QA Team',
];

function VersionEasterEgg() {
  const [hovered, setHovered] = useState(false);
  // Pick a random quote once per mount (each time settings opens)
  const [quote] = useState(() => VERSION_QUOTES[Math.floor(Math.random() * VERSION_QUOTES.length)]);

  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">
        MTI Brain – AI-powered decision intelligence
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

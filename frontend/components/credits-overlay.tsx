'use client';

import { useEffect } from 'react';
import { X } from 'lucide-react';

interface CreditsOverlayProps {
  open: boolean;
  onClose: () => void;
}

const CREW = [
  // Executive Team
  // { num: '01', name: 'CEO', role: 'Chief Executive Officer', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  // { num: '02', name: 'CFO', role: 'Chief Financial Officer', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  // { num: '03', name: 'CRO', role: 'Chief Revenue Officer', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  // { num: '04', name: 'CPO', role: 'Chief People Officer', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  // { num: '05', name: 'COO', role: 'Chief Operating Officer', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  // { num: '06', name: 'Chief of Staff', role: 'Office of the CEO', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  // { num: '07', name: 'President, DW/Cloud/Infra', role: 'Data & Cloud Infrastructure', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  // MTI Brain Core Team - ordered by corporate ladder
  { num: '01', name: 'President, Apps & DE', role: 'Applications & Data Engineering', status: 'VIP', statusClass: 'bg-amber-500/15 text-amber-400' },
  { num: '02', name: 'VP, Data & AI Practice', role: 'Data & AI Strategy', status: 'LEAD', statusClass: 'bg-sky-500/15 text-sky-400' },
  
  { num: '03', name: 'Director, Enterprise Data', role: 'Enterprise Data Solutions', status: 'LEAD', statusClass: 'bg-sky-500/15 text-sky-400' },
  { num: '04', name: 'Director, AI CoE', role: 'AI Center of Excellence', status: 'LEAD', statusClass: 'bg-sky-500/15 text-sky-400' },
  
  { num: '05', name: 'Sr. Director, Engineering', role: 'Engineering Leadership', status: 'LEAD', statusClass: 'bg-sky-500/15 text-sky-400' },
  { num: '06', name: 'Program Manager', role: 'Data and AI', status: 'ACTIVE', statusClass: 'bg-emerald-500/15 text-emerald-400' },

  { num: '07', name: 'Agile Program Manager', role: 'Delivery & Sprints', status: 'ACTIVE', statusClass: 'bg-emerald-500/15 text-emerald-400' },
  { num: '08', name: 'Developers', role: 'The ones who shipped it', status: 'SHIPPED', statusClass: 'bg-emerald-500/15 text-emerald-400' },
  // Teams
  { num: '09', name: 'QA & Testing', role: 'Quality guardians', status: 'PASSED', statusClass: 'bg-emerald-500/15 text-emerald-400' },
  { num: '10', name: 'Design', role: 'Pixel perfectionists', status: 'SHIPPED', statusClass: 'bg-emerald-500/15 text-emerald-400' },
  { num: '11', name: 'DevOps & Infra', role: 'Keeping the lights on', status: 'ONLINE', statusClass: 'bg-emerald-500/15 text-emerald-400' },
  { num: '12', name: 'You', role: 'Found the easter egg', status: 'INSIDER', statusClass: 'bg-amber-500/15 text-amber-400' },
];

const TICKER_MESSAGES = [
  'MILESTONE TECHNOLOGIES \u00B7 POWERING SMARTER IT SINCE 1997',
  'HEADQUARTERED IN FREMONT, CA \u00B7 OPERATING IN 36 COUNTRIES',
  '3500+ EMPLOYEES \u00B7 200+ CLIENTS \u00B7 INFINITE POSSIBILITIES',
  'GREAT PLACE TO WORK CERTIFIED \u00B7 USA \u00B7 INDIA \u00B7 IRELAND \u00B7 PHILIPPINES \u00B7 UK \u00B7 MEXICO',
  'EMPLOYEE FIRST \u00B7 PERFORMANCE DRIVEN \u00B7 ALWAYS SHIPPING',
];

const TICKER_TEXT = TICKER_MESSAGES.join('  \u00B7  ') + '  \u00B7  ';

export function CreditsOverlay({ open, onClose }: CreditsOverlayProps) {
  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center animate-fade-in">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />

      {/* Credits card */}
      <div className="relative w-full max-w-lg mx-4 bg-[#0d0d0d] rounded-xl overflow-hidden border border-white/10 shadow-2xl">
        {/* Scanline effect */}
        <div className="absolute inset-0 pointer-events-none overflow-hidden">
          <div className="absolute w-full h-0.5 bg-amber-400/5 animate-[scan_4s_linear_infinite]" />
        </div>

        {/* Grid background */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: 'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)',
            backgroundSize: '40px 40px',
          }}
        />

        {/* Header */}
        <div className="relative flex items-center justify-between px-5 py-3 border-b border-white/8 bg-[#111]">
          <span className="font-bold text-amber-400 text-sm tracking-[0.2em]">
            THE PEOPLE BEHIND MTI BRAIN
          </span>
          <button onClick={onClose} className="text-white/30 hover:text-white/70 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="relative px-5 py-5 space-y-5 max-h-[60vh] overflow-y-auto">
          {/* Hero card */}
          <div className="flex items-center gap-4 bg-amber-400/5 border border-amber-400/20 rounded-lg p-4 animate-fade-up">
            <div className="w-12 h-12 rounded-full bg-amber-400 flex items-center justify-center shrink-0">
              <span className="text-[#0d0d0d] font-bold text-lg">MT</span>
            </div>
            <div>
              <p className="font-bold text-amber-400 text-lg tracking-wide leading-none mb-1">Leadership</p>
              <p className="text-[11px] text-white/40 tracking-widest font-mono mb-2">CEO &middot; MILESTONE TECHNOLOGIES</p>
              <p className="text-[13px] text-white/60 italic leading-relaxed">
                &ldquo;We are the architects of the future for enterprise companies.&rdquo;
              </p>
            </div>
          </div>

          {/* Section label */}
          <p className="text-[10px] text-white/20 tracking-[0.2em] uppercase font-mono">
            The team behind this build
          </p>

          {/* Crew rows */}
          <div className="space-y-0">
            {CREW.map((member, i) => (
              <div
                key={member.num}
                className="flex items-center gap-3 py-2.5 border-b border-white/5 animate-fade-up"
                style={{ animationDelay: `${(i + 1) * 150}ms` }}
              >
                <span className="text-white/20 font-mono text-xs w-6 shrink-0">{member.num}</span>
                <span className="text-white/85 text-sm font-medium flex-1">{member.name}</span>
                <span className="text-white/30 font-mono text-[11px] hidden sm:block">{member.role}</span>
                <span className={`text-[10px] font-mono px-2 py-0.5 rounded-sm shrink-0 ${member.statusClass}`}>
                  {member.status}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Footer with ticker */}
        <div className="relative flex items-center justify-between px-5 py-2.5 border-t border-white/6">
          <div className="overflow-hidden whitespace-nowrap flex-1 mr-4">
            <div className="inline-block animate-[marquee_30s_linear_infinite] text-[11px] text-white/20 font-mono tracking-wide">
              {TICKER_TEXT}{TICKER_TEXT}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-[11px] text-white/30 font-mono border border-white/10 px-3 py-1 rounded-sm hover:border-white/30 hover:text-white/60 transition-colors tracking-wide shrink-0"
          >
            CLOSE
          </button>
        </div>
      </div>
    </div>
  );
}

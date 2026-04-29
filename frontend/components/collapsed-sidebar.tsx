'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import {
  Plus,
  Search,
  MessageSquare,
  FolderOpen,
  Sun,
  Moon,
  Settings,
  LogOut,
} from 'lucide-react';
import { useTheme } from 'next-themes';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { useUIStore } from '@/lib/store/ui';
import { useSearchStore } from '@/lib/store/search';
import { getStoredUser, getStoredToken, userFromToken, logout } from '@/lib/auth';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { SettingsModal } from './settings-modal';

function IconButton({
  tooltip,
  onClick,
  children,
  variant = 'default',
}: {
  tooltip: string;
  onClick: () => void;
  children: React.ReactNode;
  variant?: 'default' | 'onNavy';
}) {
  const classes =
    variant === 'onNavy'
      ? 'h-9 w-9 border border-[var(--header-control-border)] bg-[var(--header-control-bg)] text-[var(--header-foreground)] hover:bg-[var(--header-control-bg-hover)]'
      : 'h-9 w-9 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent';
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={classes}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right" sideOffset={6}>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

export function CollapsedSidebar() {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const openSearch = useSearchStore((s) => s.openModal);
  const [initials, setInitials] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    const user = getStoredUser() || (() => {
      const token = getStoredToken();
      return token ? userFromToken(token) : null;
    })();
    const name = user?.name || user?.email || '';
    setInitials(name.charAt(0).toUpperCase());
  }, []);

  const handleLogout = () => {
    logout();
  };

  return (
    <div className="flex flex-col items-center h-full w-12 bg-sidebar border-sidebar-border">
      {/*
        Row heights mirror the expanded sidebar so icons stay aligned:
        - Header  : h-12  (48px) — "Q Quest" + toggle
        - New Chat: pt-3 (12) + h-9 button (36) + pb-2 (8) = 56px
        - Search  : h-8 input (32) + pb-2 (8) = 40px
      */}

      {/* Header — matches expanded sidebar h-12 */}
      <div
        className="h-12 flex items-center justify-center w-full shrink-0 border-b border-sidebar-border"
        style={{ backgroundColor: 'var(--header)' }}
      >
        <button
          onClick={toggleSidebar}
          aria-label="Open sidebar"
          className="flex items-center justify-center h-9 w-9 text-[var(--header-foreground)]"
        >
          <Image
            src="/Milestone%20Icon.png"
            alt="Milestone"
            width={0}
            height={0}
            sizes="26px"
            style={{ width: '26px', height: 'auto', objectFit: 'contain' }}
            className="select-none"
          />
        </button>
      </div>

      {/* New Chat (matches expanded pt-3 + button + pb-2 = 56px) */}
      <div className="h-14 flex items-center justify-center w-full shrink-0">
        <IconButton tooltip="New chat" onClick={() => router.push('/new')}>
          <Plus className="w-[18px] h-[18px]" />
        </IconButton>
      </div>

      {/* Search (matches expanded h-8 + pb-2 = 40px) */}
      <div className="h-10 flex items-center justify-center w-full shrink-0">
        <IconButton tooltip="Search" onClick={openSearch}>
          <Search className="w-[18px] h-[18px]" />
        </IconButton>
      </div>

      {/* Projects + Chats (inside scroll area in expanded — just flow here) */}
      <div className="flex flex-col items-center w-full shrink-0">
        <div className="h-9 flex items-center justify-center w-full">
          <IconButton tooltip="Projects" onClick={() => router.push('/projects')}>
            <FolderOpen className="w-[18px] h-[18px]" />
          </IconButton>
        </div>
        <div className="h-9 flex items-center justify-center w-full">
          <IconButton tooltip="Chats" onClick={() => router.push('/chats')}>
            <MessageSquare className="w-[18px] h-[18px]" />
          </IconButton>
        </div>
      </div>

      <div className="flex-1" />

      {/* User avatar (matches expanded footer) */}
      <div className="py-2">
        {initials && (
          <DropdownMenu>
            <Tooltip>
              <TooltipTrigger asChild>
                <DropdownMenuTrigger asChild>
                  <button className="hover:opacity-80 transition-opacity">
                    <Avatar className="h-8 w-8">
                      <AvatarFallback className="bg-primary/15 text-primary text-xs font-semibold">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                  </button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent side="right" sideOffset={6}>Account</TooltipContent>
            </Tooltip>
            <DropdownMenuContent side="right" align="end" className="w-56 mb-1">
              {/* Theme options */}
              <div className="px-2 py-1.5">
                <p className="text-xs font-medium text-muted-foreground mb-1.5">Theme</p>
                <div className="flex gap-1">
                  <button
                    onClick={() => setTheme('light')}
                    className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition-colors ${
                      theme === 'light'
                        ? 'bg-accent text-accent-foreground font-medium'
                        : 'hover:bg-accent text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <Sun className="w-3.5 h-3.5" />
                    Light
                  </button>
                  <button
                    onClick={() => setTheme('dark')}
                    className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition-colors ${
                      theme === 'dark'
                        ? 'bg-accent text-accent-foreground font-medium'
                        : 'hover:bg-accent text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <Moon className="w-3.5 h-3.5" />
                    Dark
                  </button>
                </div>
              </div>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setSettingsOpen(true)} className="gap-2">
                <Settings className="w-4 h-4" />
                Settings
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout} className="gap-2">
                <LogOut className="w-4 h-4" />
                Log out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* Settings Modal */}
      <SettingsModal open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}

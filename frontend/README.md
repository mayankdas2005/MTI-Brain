# MTI Brain Frontend

Production **AI-powered conversational data analytics** interface for MTI Brain. Users ask natural-language questions and receive streamed answers with data tables, charts, and follow-up suggestions in real time via Server-Sent Events.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 16 (App Router, standalone output) |
| Language | TypeScript (strict mode) |
| UI | React 19 with React Compiler (auto-memoization), shadcn/ui (Radix UI), Lucide icons |
| Styling | Tailwind CSS 4 (PostCSS plugin — no `tailwind.config.*`; tokens live in `app/globals.css` with `@custom-variant dark`) |
| State | Zustand (11 stores — see [State Management](#state-management)) |
| Streaming | POST-based SSE via fetch + ReadableStream |
| Auth | Username/password → JWT in localStorage. **Okta OIDC planned** (callback page stub at `app/auth/callback/page.tsx`) |
| Markdown | react-markdown + remark-gfm + rehype-highlight |
| Charts | recharts |
| Forms | react-hook-form + zod |
| Notifications | Sonner |
| Theme | next-themes |
| Analytics | PostHog (gracefully no-ops when key is unset) + Vercel Analytics |
| Local persistence | Dexie (IndexedDB) for composer drafts |
| Animations | Framer Motion |
| Virtualization | @tanstack/react-virtual |
| Keyboard | react-hotkeys-hook (wrapped by `hooks/use-keyboard-shortcuts.ts`) |
| PWA | Service worker (`public/sw.js`), install prompt with 3-day re-show logic |
| Containerization | Docker (multi-stage, non-root, standalone) |

## Project Structure

```
frontend/
├── app/
│   ├── page.tsx                         # Login page (username/password form)
│   ├── layout.tsx                       # Root layout with providers + Vercel Analytics
│   ├── globals.css                      # Global styles + Tailwind v4 theme tokens (@custom-variant dark)
│   ├── not-found.tsx                    # 404 page
│   ├── auth/
│   │   └── callback/
│   │       └── page.tsx                 # Stub for upcoming Okta OIDC callback
│   ├── (authenticated)/                 # Route group - all pages require JWT
│   │   ├── layout.tsx                   # Auth guard + sidebar + topbar shell
│   │   ├── new/                         # Welcome + new chat composer
│   │   ├── chat/
│   │   │   ├── page.tsx                 # Redirects to /new
│   │   │   ├── [chatId]/page.tsx        # Chat detail with message stream
│   │   │   └── layout.tsx
│   │   ├── chats/page.tsx               # All chats list with search
│   │   ├── starred/page.tsx             # Starred threads view
│   │   ├── settings/page.tsx            # User preferences settings
│   │   └── projects/
│   │       ├── page.tsx                 # Projects grid
│   │       └── [projectId]/page.tsx     # Project detail with threads
│   └── api/                             # Legacy mock routes (unused, kept for reference; backend is FastAPI)
│       ├── chat/route.ts
│       ├── completions/route.ts
│       └── thinking/route.ts
├── components/
│   ├── ui/                              # shadcn/ui primitives (button, dialog, tabs, …)
│   ├── charts/                          # Chart helpers (theme module)
│   ├── messages/
│   │   ├── about-panel.tsx              # Per-message metadata panel
│   │   └── trust-strip.tsx              # Trust strip: source tables / freshness (backend-owned data)
│   ├── sidebar.tsx                      # Thread list, project switcher, user menu
│   ├── collapsed-sidebar.tsx            # Minimized sidebar (icons only)
│   ├── topbar.tsx                       # Thread title, star, search trigger
│   ├── chat-composer.tsx                # Message input with auto-grow textarea
│   ├── new-chat-composer.tsx            # Centered composer for /new page
│   ├── message-list.tsx                 # Grouped messages with version branching
│   ├── message-bubble.tsx               # Single message with actions (copy, edit, retry)
│   ├── message-visualization.tsx        # Data viz container (chart + table + SQL)
│   ├── message-skeleton.tsx             # Loading placeholder
│   ├── markdown-renderer.tsx            # Markdown → JSX with code highlighting + copy
│   ├── data-table.tsx                   # Query results table with pagination
│   ├── follow-up-chips.tsx              # Suggested follow-up question buttons
│   ├── thinking-words.tsx               # Reasoning / chain-of-thought display
│   ├── feedback-widget.tsx              # Thumbs up/down + comment
│   ├── welcome-state.tsx                # Welcome screen with suggestion chips
│   ├── search-modal.tsx                 # Global search (Cmd+K, search-only — no Commands group)
│   ├── slash-command-popover.tsx        # In-composer slash commands (/clear, /retry, …)
│   ├── shortcuts-dialog.tsx             # Keyboard shortcuts help
│   ├── create-project-dialog.tsx        # New project form
│   ├── edit-project-dialog.tsx          # Edit project name/description
│   ├── rename-dialog.tsx                # Rename thread dialog
│   ├── move-to-project-dialog.tsx       # Move threads to project
│   ├── bulk-action-bar.tsx              # Multi-select toolbar (delete, move)
│   ├── project-context-menu.tsx         # Project right-click menu
│   ├── thread-context-menu.tsx          # Thread right-click menu
│   ├── agent-selector.tsx               # AI model selector
│   ├── analytics-bridge.tsx             # PostHog identify + pageviews + service-worker registration
│   ├── feature-pulse.tsx                # Dismissible "new feature" indicator
│   ├── install-prompt.tsx               # PWA install prompt with 3-day re-show window
│   ├── live-announcer.tsx               # ARIA live-region announcer (accessibility)
│   ├── onboarding-tour.tsx              # First-run UI walkthrough
│   ├── error-boundary.tsx               # Error handling wrapper
│   ├── providers.tsx                    # Theme + Tooltip + AnalyticsBridge providers
│   ├── theme-provider.tsx               # next-themes wrapper
│   └── credits-overlay.tsx              # Easter egg (Konami code)
├── hooks/
│   ├── use-keyboard-shortcuts.ts        # Global keyboard shortcut handler (wraps react-hotkeys-hook)
│   └── use-mobile.ts                    # Mobile viewport detection
├── lib/
│   ├── auth.ts                          # Login (username/password → JWT), logout, token helpers
│   ├── utils.ts                         # Utility functions (cn for classnames)
│   ├── toast.ts                         # Toast wrapper (sonner)
│   ├── analytics.ts                     # PostHog identify / pageview / event helpers
│   ├── api/
│   │   ├── client.ts                    # Base fetch wrapper with auth headers + 401 redirect
│   │   ├── sse.ts                       # POST-based SSE stream parser
│   │   ├── threads.ts                   # Thread/chat API functions
│   │   ├── projects.ts                  # Project API functions
│   │   └── index.ts                     # API exports
│   ├── store/                           # 11 Zustand stores (see State Management section)
│   │   ├── threads.ts
│   │   ├── projects.ts
│   │   ├── auth.ts
│   │   ├── ui.ts
│   │   ├── preferences.ts
│   │   ├── search.ts
│   │   ├── agents.ts
│   │   ├── thinking.ts
│   │   ├── activity.ts                  # User activity tracking
│   │   ├── drafts.ts                    # Composer drafts (Dexie / IndexedDB)
│   │   └── install.ts                   # PWA install prompt state
│   └── types/
│       └── api.ts                       # TypeScript types for API responses
├── public/
│   ├── sw.js                            # Service worker for PWA install support
│   └── …                                # Static assets (favicon, logos, icons)
├── package.json
├── tsconfig.json
├── next.config.mjs                      # React Compiler + standalone output config
├── postcss.config.mjs                   # @tailwindcss/postcss plugin
├── components.json                      # shadcn/ui config (new-york style)
├── Dockerfile
├── .dockerignore
├── .env.example
└── .gitignore
```

> **Note:** there is no `tailwind.config.*` file — Tailwind v4 with the PostCSS plugin reads tokens directly from `app/globals.css`. Likewise there's no `styles/` directory.

## Routes

### Public

| Route | Purpose |
|-------|---------|
| `/` | Login page - username/password form, calls `POST /api/v1/auth/login` |

### Authenticated (require JWT)

| Route | Purpose |
|-------|---------|
| `/new` | Welcome screen with suggestion chips and centered composer |
| `/chat/[chatId]` | Chat detail - message stream, data tables, charts, follow-ups |
| `/chats` | All chats list with search and pagination |
| `/starred` | Starred threads view (filtered list) |
| `/projects` | Projects grid with search |
| `/projects/[projectId]` | Project detail with its threads |
| `/settings` | User preferences (response tone, visibility toggles, max rows) |

### Auth callback (placeholder)

| Route | Purpose |
|-------|---------|
| `/auth/callback` | Stub awaiting Okta OIDC wire-up — present so the redirect URL can be registered with the IdP early |

## Authentication

> **Okta OIDC migration is planned.** The `app/auth/callback/page.tsx` route is a stub today; the backend `MTIBrainUser` model already carries `okta_id`. Until that flow lands, the live path is the username/password flow described below.

Direct username/password flow:

1. User submits the login form on `/`
2. Frontend calls `POST /api/v1/auth/login` with `{ username, password }`
3. Backend validates credentials and returns a signed JWT + user object
4. Frontend stores both in localStorage under `mti_brain_token` and `mti_brain_user`
5. All subsequent API calls include `Authorization: Bearer <token>`
6. On 401 response, token is cleared and user is redirected to `/`
7. Logout clears localStorage and redirects to `/`

## State Management

Eleven Zustand stores under `lib/store/`, each with a single responsibility:

| Store | Key State | Purpose |
|-------|-----------|---------|
| `useThreadStore` | threads, currentMessages, isStreaming, selectedThreadIds | Thread/message CRUD, SSE streaming, version branching, bulk operations |
| `useProjectStore` | projects, currentProject | Project list + CRUD |
| `useAuthStore` | user, token | Auth state, login/logout |
| `useUIStore` | sidebarOpen | Sidebar open/close toggle |
| `usePreferencesStore` | responseTone, showSQL, autoShowCharts, showFollowUps, showReasoning, maxResultRows | Per-user preferences, persisted to localStorage under `mti-brain-prefs:{userId}` |
| `useSearchStore` | query, chatResults, projectResults | Global search with 200ms debounce |
| `useAgentStore` | agents, currentAgentId | AI model selection |
| `useThinkingStore` | enableDeepThinking, isThinking | Extended thinking toggle |
| `useActivityStore` | activity events | Per-user activity tracking (Activity framing — not gamification/streaks) |
| `useDraftsStore` | composer drafts | Per-thread composer drafts persisted to IndexedDB via Dexie |
| `useInstallStore` | install prompt visibility, last-shown timestamp | PWA install prompt state with 3-day re-show window |

## SSE Streaming

The frontend uses a custom POST-based SSE parser (since `EventSource` only supports GET). When a user asks a question, the thread store opens a streaming connection and dispatches events to update the UI in real time:

| SSE Event | Handler | UI Update |
|-----------|---------|-----------|
| `timing.sync` | `onTimingSync` | Elapsed time sync during stream |
| `title.generated` | `onTitleGenerated` | Set thread title in sidebar |
| `node.start` | `onNodeStart` | Show pipeline step progress |
| `reasoning.pending` | `onReasoningPending` | Render the "thinking" placeholder before tokens arrive |
| `reasoning.delta` | `onReasoningDelta` | Append to reasoning accordion |
| `answer.delta` | `onAnswerDelta` | Stream answer text into message bubble |
| `validation` | `onValidation` | Show SQL validation status |
| `execute.done` | `onExecuteDone` | Populate data table with query results |
| `chart` | `onChart` | Render chart visualization |
| `follow_ups` | `onFollowUps` | Show follow-up question chips |
| `done` | `onDone` | Finalize message, stop loading indicators |
| `stopped` | `onStopped` | Mark message as user-cancelled |
| `error` | `onError` | Show error toast |

## User Preferences

Persisted per-user in localStorage. Configurable via the settings modal:

| Preference | Default | Options |
|-----------|---------|---------|
| Response tone | `consultant` | `consultant`, `operator`, `brief` |
| Show SQL | `true` | Toggle |
| Auto-show charts | `true` | Toggle |
| Show follow-ups | `true` | Toggle |
| Show reasoning | `true` | Toggle |
| Default data view | `table` | `sql`, `table` |
| Max result rows | `100` | `10`-`500` |

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl+L` | New chat |
| `Cmd/Ctrl+K` | Search conversations |
| `Cmd/Ctrl+S` | Star / unstar thread |
| `Cmd/Ctrl+Shift+C` | Copy last response |
| `Cmd/Ctrl+Shift+P` | Open projects |
| `Cmd/Ctrl+Shift+H` | Chat history |
| `Cmd/Ctrl+/` | Show shortcuts menu |
| `Enter` | Send message |
| `Shift+Enter` | New line |
| `Escape` | Close dialog |

## Getting Started

### Prerequisites

- Node.js 20+ and npm (or pnpm)

### Setup

1. **Install dependencies:**
   ```bash
   cd quest/frontend
   npm install
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env - at minimum set NEXT_PUBLIC_API_URL to point at the backend
   ```

3. **Start development server:**
   ```bash
   npm run dev
   # Open http://localhost:3000
   ```

### Docker

```bash
docker build -t mti-brain-frontend .
docker run -p 3000:3000 mti-brain-frontend
```

The container runs as a non-root user (`nextjs`), includes a health check (`wget` to port 3000 every 30s), uses the Next.js standalone output, and limits Node.js memory to 256 MB.

## Environment Variables

Variables prefixed `NEXT_PUBLIC_` are embedded at build time and exposed to the browser.

### Required

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | FastAPI backend base URL (e.g., `http://localhost:8000`) |

### Optional

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_POSTHOG_KEY` | PostHog project key. When unset, `lib/analytics.ts` no-ops and `AnalyticsBridge` skips identify/pageview calls — analytics are entirely optional. |
| `NEXT_PUBLIC_POSTHOG_HOST` | PostHog ingest host. Defaults to PostHog Cloud when unset. |

### Dev-only

| Variable | Description |
|----------|-------------|
| `NEXT_DEV_ORIGINS` | Comma-separated extra origins for cross-machine HMR. Only needed when running `next dev` inside Docker or behind an SSH tunnel. |

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server with hot reload |
| `npm run build` | Build for production (standalone output) |
| `npm run start` | Start production server |
| `npm run lint` | Run ESLint |

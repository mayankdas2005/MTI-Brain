# MTI Brain Frontend

Production **AI-powered conversational data analytics** interface for MTI Brain. Users ask natural-language questions and receive streamed answers with data tables, charts, and follow-up suggestions in real time via Server-Sent Events.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 16 (App Router, standalone output) |
| Language | TypeScript (strict mode) |
| UI | React 19, shadcn/ui (Radix UI), Lucide icons |
| Styling | Tailwind CSS 4 with OKLCH CSS variables, dark/light theme |
| State | Zustand (8 stores) |
| Streaming | POST-based SSE via fetch + ReadableStream |
| Auth | Username/password → JWT (stored in localStorage) |
| Markdown | react-markdown + remark-gfm + rehype-highlight |
| Charts | recharts |
| Forms | react-hook-form + zod |
| Notifications | Sonner |
| Theme | next-themes |
| Containerization | Docker (multi-stage, non-root, standalone) |

## Project Structure

```
frontend/
├── app/
│   ├── page.tsx                         # Login page (username/password form)
│   ├── layout.tsx                       # Root layout with providers
│   ├── globals.css                      # Global styles + Tailwind theme tokens
│   ├── not-found.tsx                    # 404 page
│   ├── auth/
│   │   └── callback/
│   │       └── page.tsx                 # Auth callback handler (planned: Okta OIDC)
│   ├── (authenticated)/                 # Route group — all pages require JWT
│   │   ├── layout.tsx                   # Auth guard + sidebar + topbar shell
│   │   ├── new/
│   │   │   ├── page.tsx                 # Welcome state + new chat composer
│   │   │   └── layout.tsx
│   │   ├── chat/
│   │   │   ├── page.tsx                 # Redirects to /new
│   │   │   ├── [chatId]/
│   │   │   │   └── page.tsx             # Chat detail with message stream
│   │   │   └── layout.tsx
│   │   ├── chats/
│   │   │   └── page.tsx                 # All chats list with search
│   │   └── projects/
│   │       ├── page.tsx                 # Projects grid
│   │       └── [projectId]/
│   │           └── page.tsx             # Project detail with threads
│   └── api/                             # Legacy mock routes (unused, backend is FastAPI)
│       ├── chat/route.ts
│       ├── completions/route.ts
│       └── thinking/route.ts
├── components/
│   ├── ui/                              # shadcn/ui primitives (button, dialog, tabs, …)
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
│   ├── search-modal.tsx                 # Global search (Cmd+K)
│   ├── settings-modal.tsx               # User preferences (tone, SQL, charts, …)
│   ├── shortcuts-dialog.tsx             # Keyboard shortcuts help
│   ├── create-project-dialog.tsx        # New project form
│   ├── edit-project-dialog.tsx          # Edit project name/description
│   ├── rename-dialog.tsx                # Rename thread dialog
│   ├── move-to-project-dialog.tsx       # Move threads to project
│   ├── bulk-action-bar.tsx              # Multi-select toolbar (delete, move)
│   ├── project-context-menu.tsx         # Project right-click menu
│   ├── thread-context-menu.tsx          # Thread right-click menu
│   ├── agent-selector.tsx               # AI model selector
│   ├── error-boundary.tsx               # Error handling wrapper
│   ├── providers.tsx                    # Theme + Tooltip providers
│   ├── theme-provider.tsx               # next-themes wrapper
│   ├── credits-overlay.tsx              # Easter egg (Konami code)
│   └── sonner.tsx                       # Toast notification config
├── hooks/
│   ├── use-keyboard-shortcuts.ts        # Global keyboard shortcut handler
│   ├── use-mobile.ts                    # Mobile viewport detection
│   └── use-toast.ts                     # Toast notification hook
├── lib/
│   ├── auth.ts                          # Login (username/password → JWT), logout, token helpers
│   ├── utils.ts                         # Utility functions (cn for classnames)
│   ├── toast.ts                         # Toast wrapper (sonner)
│   ├── api/
│   │   ├── client.ts                    # Base fetch wrapper with auth headers + 401 redirect
│   │   ├── sse.ts                       # POST-based SSE stream parser
│   │   ├── threads.ts                   # Thread/chat API functions
│   │   ├── projects.ts                  # Project API functions
│   │   └── index.ts                     # API exports
│   ├── store/
│   │   ├── threads.ts                   # Thread/message CRUD, SSE streaming, version branching
│   │   ├── projects.ts                  # Project list + CRUD
│   │   ├── auth.ts                      # Auth state (user, token)
│   │   ├── ui.ts                        # UI state (sidebar open/close)
│   │   ├── preferences.ts              # Per-user preferences (persisted to localStorage)
│   │   ├── search.ts                    # Global search with 200ms debounce
│   │   ├── agents.ts                    # AI model selection
│   │   └── thinking.ts                  # Extended thinking toggle
│   └── types/
│       └── api.ts                       # TypeScript types for API responses
├── styles/
│   └── globals.css                      # Tailwind imports + CSS variables
├── public/                              # Static assets (favicon, logos, icons)
├── package.json
├── tsconfig.json
├── next.config.mjs
├── postcss.config.mjs
├── components.json                      # shadcn/ui config (new-york style)
├── Dockerfile
├── .dockerignore
├── .env.example
└── .gitignore
```

## Routes

### Public

| Route | Purpose |
|-------|---------|
| `/` | Login page — username/password form, calls `POST /api/v1/auth/login` |
| `/auth/callback` | Auth callback page (prepared for Okta OIDC — not yet active) |

### Authenticated (require JWT)

| Route | Purpose |
|-------|---------|
| `/new` | Welcome screen with suggestion chips and centered composer |
| `/chat/[chatId]` | Chat detail — message stream, data tables, charts, follow-ups |
| `/chats` | All chats list with search and pagination |
| `/projects` | Projects grid with search |
| `/projects/[projectId]` | Project detail with its threads |

## Authentication

Direct username/password flow:

1. User submits the login form on `/`
2. Frontend calls `POST /api/v1/auth/login` with `{ username, password }`
3. Backend validates credentials and returns a signed JWT + user object
4. Frontend stores both in localStorage under `quest_token` and `quest_user`
5. All subsequent API calls include `Authorization: Bearer <token>`
6. On 401 response, token is cleared and user is redirected to `/`
7. Logout clears localStorage and redirects to `/`

> **Planned:** Okta OAuth2/OIDC login. The `/auth/callback` route and Okta env vars are scaffolded for this future integration.

## State Management

Eight Zustand stores, each with a single responsibility:

| Store | Key State | Purpose |
|-------|-----------|---------|
| `useThreadStore` | threads, currentMessages, isStreaming, selectedThreadIds | Thread/message CRUD, SSE streaming, version branching, bulk operations |
| `useProjectStore` | projects, currentProject | Project list + CRUD |
| `useAuthStore` | user, token | Auth state, login/logout |
| `useUIStore` | sidebarOpen | Sidebar open/close toggle |
| `usePreferencesStore` | responseTone, showSQL, autoShowCharts, showFollowUps, showReasoning, maxResultRows | Per-user preferences, persisted to localStorage under `quest-prefs:{userId}` |
| `useSearchStore` | query, chatResults, projectResults | Global search with 200ms debounce |
| `useAgentStore` | agents, currentAgentId | AI model selection |
| `useThinkingStore` | enableDeepThinking, isThinking | Extended thinking toggle |

## SSE Streaming

The frontend uses a custom POST-based SSE parser (since `EventSource` only supports GET). When a user asks a question, the thread store opens a streaming connection and dispatches events to update the UI in real time:

| SSE Event | Handler | UI Update |
|-----------|---------|-----------|
| `title.generated` | `onTitleGenerated` | Set thread title in sidebar |
| `node.start` | `onNodeStart` | Show pipeline step progress |
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
| Max result rows | `100` | `10`–`500` |

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
   # Edit .env — at minimum set NEXT_PUBLIC_API_URL to point at the backend
   ```

3. **Start development server:**
   ```bash
   npm run dev
   # Open http://localhost:3000
   ```

### Docker

```bash
docker build --build-arg NEXT_PUBLIC_API_URL=https://your-api.example.com -t mti-brain-frontend .
docker run -p 3000:3000 mti-brain-frontend
```

The container runs as a non-root user (`nextjs`), includes a health check (`wget` to port 3000 every 30s), uses the Next.js standalone output, and limits Node.js memory to 256 MB.

## Environment Variables

Variables prefixed `NEXT_PUBLIC_` are embedded at build time and exposed to the browser.

### Required

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | FastAPI backend base URL (e.g., `http://localhost:8000`) |
| `NEXT_PUBLIC_APP_URL` | Frontend public URL (e.g., `http://localhost:3000`) |

### Planned / future (Okta OIDC)

These variables are present in `.env.example` for the future Okta authentication integration. They have no effect on the current username/password login flow:

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_OKTA_DOMAIN` | Okta tenant domain (e.g., `your-org.okta.com`) |
| `NEXT_PUBLIC_OKTA_CLIENT_ID` | Okta OIDC application client ID |
| `NEXT_PUBLIC_OKTA_REDIRECT_URI` | OAuth2 callback URI (e.g., `http://localhost:3000/auth/callback`) |

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

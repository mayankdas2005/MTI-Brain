# MTI Brain Frontend

Production **AI-powered conversational data analytics** interface for MTI Brain. Users ask natural-language questions and receive streamed answers with data tables, charts, and follow-up suggestions in real time via Server-Sent Events.Fully responsive across desktop, tablet (iPad portrait/landscape), and mobile (iPhone SE through iPhone 16 Pro Max). Installable as a PWA.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 16 (App Router, standalone output) |
| Language | TypeScript (strict mode) |
| UI | React 19 with React Compiler (auto-memoization), shadcn/ui (Radix UI), Lucide icons |
| Styling | Tailwind CSS 4 (PostCSS plugin — no `tailwind.config.*`; tokens live in `app/globals.css`) |
| State | Zustand stores (see [State Management](#state-management)) |
| Streaming | POST-based SSE via fetch + ReadableStream |
| Auth | Username/password → JWT in localStorage. **Okta OIDC planned** |
| Markdown | react-markdown + remark-gfm + rehype-highlight |
| Charts | recharts |
| Mobile drawers | vaul (bottom-sheet animations) |
| Notifications | Sonner |
| Theme | next-themes |
| Analytics | PostHog (gracefully no-ops when key is unset) + Vercel Analytics |
| Local persistence | Dexie (IndexedDB) for composer drafts |
| Animations | Framer Motion |
| Virtualization | @tanstack/react-virtual |
| Keyboard | react-hotkeys-hook (wrapped by `hooks/use-keyboard-shortcuts.ts`) |
| PWA | Service worker (`public/sw.js`), install prompt, `display: standalone` manifest |
| Containerization | Docker (multi-stage, non-root, standalone) |

## Project Structure

```
frontend/
├── app/
│   ├── page.tsx                         # Login page (username/password + no-flash auth check)
│   ├── layout.tsx                       # Root layout — viewport meta, providers, PWA capture script
│   ├── globals.css                      # Tailwind v4 theme tokens + mobile hardening
│   │                                    #   (-webkit-tap-highlight-color, overscroll-behavior,
│   │                                    #    .tap-44, .scroll-shadow-x, --vv-bottom-inset)
│   ├── not-found.tsx                    # 404 page
│   ├── auth/callback/page.tsx           # Stub for upcoming Okta OIDC callback
│   └── (authenticated)/                 # Route group — all pages require JWT
│       ├── layout.tsx                   # Auth guard + responsive sidebar shell
│       │                                #   Mobile: off-canvas Sheet via useIsMobile()
│       │                                #   Tablet: CollapsedSidebar + overlay Sheet via useIsTablet()
│       │                                #   Desktop: inline sidebar (280px or 48px collapsed)
│       ├── new/                         # Welcome screen + NewChatComposer (centered)
│       ├── chat/[chatId]/page.tsx       # Chat detail — messages, stream, visualViewport listener
│       ├── chats/page.tsx               # All chats with search
│       ├── starred/page.tsx             # Starred threads
│       ├── settings/page.tsx            # User preferences (two-column desktop, stacked mobile)
│       └── projects/
│           ├── page.tsx                 # Projects grid
│           └── [projectId]/page.tsx     # Project detail
├── components/
│   ├── ui/                              # shadcn/ui primitives
│   │   ├── responsive-dialog.tsx        # ResponsiveDialog — Dialog on desktop, vaul Drawer on mobile
│   │   ├── sheet.tsx                    # Off-canvas panel (used for mobile/tablet sidebar)
│   │   ├── drawer.tsx                   # vaul-based bottom drawer
│   │   └── …                            # button, dialog, tabs, tooltip, etc.
│   ├── messages/
│   │   ├── about-panel.tsx              # Per-message metadata side panel
│   │   └── trust-strip.tsx              # Source tables / data freshness (backend-owned data)
│   ├── sidebar.tsx                      # Thread list, project nav, user menu
│   │                                    #   w-full md:w-[280px]; auto-closes on mobile nav
│   ├── collapsed-sidebar.tsx            # Icon-only sidebar (48px); tablet expand → overlay sheet
│   ├── topbar.tsx                       # Thread title, star, search trigger, hamburger (mobile/tablet)
│   ├── chat-composer.tsx                # Message input — Deep Analysis toggle, safe-area pb,
│   │                                    #   visualViewport keyboard avoidance, .tap-44 buttons
│   ├── new-chat-composer.tsx            # Centered composer for /new — same Deep Analysis toggle,
│   │                                    #   passes deepAnalysis to store via setPendingQuestion
│   ├── message-list.tsx                 # Grouped messages with version branching
│   ├── message-bubble.tsx               # Single message — max-w-[92%] md:max-w-[80%] on mobile
│   ├── message-visualization.tsx        # Chart + table + SQL — h-[260px] md:h-[340px] charts
│   ├── data-table.tsx                   # Query results — max-h-[60vh], sticky first column,
│   │                                    #   scroll-shadow-x, overscroll-x-contain, flex-wrap footer
│   ├── follow-up-chips.tsx              # Suggested follow-up question buttons
│   ├── thinking-words.tsx               # Reasoning / chain-of-thought display
│   ├── feedback-widget.tsx              # Thumbs up/down + comment (DialogDescription for a11y)
│   ├── welcome-state.tsx                # Welcome screen with suggestion chips
│   ├── search-modal.tsx                 # Global search (Cmd+K) — ResponsiveDialog, max-h-[60vh] mobile
│   ├── slash-command-popover.tsx        # In-composer slash commands — max-h-[40vh] mobile
│   ├── shortcuts-dialog.tsx             # Keyboard shortcuts — ResponsiveDialog with sr-only description
│   ├── create-project-dialog.tsx        # New project — ResponsiveDialog (Drawer on mobile)
│   ├── edit-project-dialog.tsx          # Edit project — ResponsiveDialog
│   ├── rename-dialog.tsx                # Rename thread — ResponsiveDialog
│   ├── move-to-project-dialog.tsx       # Move threads — ResponsiveDialog, max-h-[40vh] list
│   ├── bulk-action-bar.tsx              # Multi-select toolbar — flex-wrap for narrow screens
│   ├── onboarding-tour.tsx              # First-run walkthrough — mobile-sized popover, skipIfMissing
│   │                                    #   for sidebar steps (sidebar is in a closed Sheet on mobile)
│   ├── install-prompt.tsx               # PWA install — full-width on phones, keyboard-aware bottom
│   ├── credits-overlay.tsx              # Easter egg (Konami code) — max-h-[75vh] mobile
│   ├── live-announcer.tsx               # ARIA live-region (sr-only, a11y)
│   ├── error-boundary.tsx               # Error handling wrapper
│   ├── providers.tsx                    # Theme + Tooltip + AnalyticsBridge + DensitySync
│   └── analytics-bridge.tsx             # PostHog identify + pageviews + service-worker registration
├── hooks/
│   ├── use-keyboard-shortcuts.ts        # Global keyboard shortcut handler
│   └── use-mobile.ts                    # useIsMobile() (<768px) + useIsTablet() (768–1023px)
├── lib/
│   ├── auth.ts                          # Login (username/password → JWT), logout, token helpers
│   ├── utils.ts                         # cn() for classnames
│   ├── toast.ts                         # Toast wrapper (sonner)
│   ├── analytics.ts                     # PostHog helpers
│   ├── api/
│   │   ├── client.ts                    # Base fetch wrapper with auth headers + 401 redirect
│   │   │                                #   API_BASE = NEXT_PUBLIC_API_URL/api/v1
│   │   ├── sse.ts                       # POST-based SSE stream parser
│   │   ├── threads.ts                   # Thread/chat API functions
│   │   └── projects.ts                  # Project API functions
│   └── store/                           # Zustand stores
│       ├── threads.ts                   # Thread/message CRUD, streaming, pendingDeepAnalysis
│       ├── ui.ts                        # mobileSidebarOpen, tabletSidebarOverlayOpen, sidebarOpen
│       ├── preferences.ts               # responseTone, showSQL, autoShowCharts, etc.
│       └── …                            # search, activity, drafts, install, projects, auth
└── public/
    ├── manifest.json                    # PWA manifest — display: standalone (no orientation lock)
    ├── sw.js                            # Service worker
    └── …                               # Logos, icons, MSFT sign-in button
```

## Responsive Layout

Three breakpoints, each with a distinct sidebar behavior:

| Viewport | Width | Sidebar pattern |
|----------|-------|-----------------|
| **Mobile** | < 768px | Off-canvas Sheet (88% width). Hamburger in topbar opens it. Tap a row → navigates + sheet closes. |
| **Tablet** | 768–1023px | `CollapsedSidebar` inline (48px icon bar). Tapping the expand button OR hamburger opens a 320px overlay Sheet — content never gets squeezed. |
| **Desktop** | ≥ 1024px | Inline sidebar (280px expanded or 48px collapsed). Toggle with `Cmd+.`. |

### Key responsive utilities

- **`.tap-44`** — Tailwind utility class (globals.css); sets `min-height: 44px; min-width: 44px` on phones only. Applied to topbar hamburger, composer send/stop buttons, and other interactive controls.
- **`--vv-bottom-inset`** — CSS variable set by the chat page via the Visual Viewport API. Used by the composer to lift above the iOS soft keyboard.
- **`scroll-shadow-x`** — Gradient utility for horizontally-scrolling containers (data tables, SQL code blocks) to signal more content off-screen.
- **`ResponsiveDialog`** — `components/ui/responsive-dialog.tsx`. Renders as a Radix `Dialog` (centered modal) on desktop and as a `vaul` bottom-sheet `Drawer` on mobile. Used by all form dialogs.

## Deep Analysis Toggle

A per-question `BrainCircuit` toggle button sits in the bottom-left of both composers (`chat-composer.tsx` and `new-chat-composer.tsx`).

- **On:** blue pill with border; sends `deep_analysis: true` with the request
- **Off:** muted ghost button (default)
- **Persistence:** stays on until the user manually clicks it off (does not reset between questions)
- **Navigation:** when toggled on the `/new` page, the value is stored in `pendingDeepAnalysis` (thread store) and picked up by `ChatComposer` when it fires the first `askQuestion` — the toggle is also initialized to `pendingDeepAnalysis` on mount so it visually reflects the correct state
- **Backend field:** `AskRequest.deep_analysis: bool` — default `false`

## State Management

Zustand stores under `lib/store/`:

| Store | Key State | Purpose |
|-------|-----------|---------|
| `useThreadStore` | `threads`, `currentMessages`, `isStreaming`, `pendingQuestion`, `pendingDeepAnalysis`, `selectedThreadIds` | Thread/message CRUD, SSE streaming, version branching, Deep Analysis flag, bulk operations |
| `useProjectStore` | `projects`, `currentProject` | Project list + CRUD |
| `useUIStore` | `sidebarOpen`, `mobileSidebarOpen`, `tabletSidebarOverlayOpen`, `shortcutsOpen`, `createProjectOpen` | Sidebar state for all three breakpoints, dialog visibility |
| `usePreferencesStore` | `responseTone`, `showSQL`, `autoShowCharts`, `showFollowUps`, `showReasoning`, `maxResultRows`, `density` | Per-user preferences, persisted to localStorage under `mti-brain-prefs:{userId}` |
| `useSearchStore` | `query`, `chatResults`, `projectResults` | Global search with 200 ms debounce |
| `useActivityStore` | activity events | Per-user activity tracking (Activity framing — not gamification/streaks) |
| `useDraftsStore` | composer drafts | Per-thread composer drafts persisted to IndexedDB via Dexie |
| `useInstallStore` | install prompt visibility | PWA install prompt state with 3-day re-show window |

## User Preferences

Configurable via `/settings`, persisted per-user in localStorage:

| Preference | Default | Options |
|-----------|---------|---------|
| Response tone | `analyst` | `analyst`, `manager`, `director`, `executive` |
| Show SQL | `true` | Toggle |
| Auto-show charts | `true` | Toggle |
| Show follow-ups | `true` | Toggle |
| Show reasoning | `true` | Toggle |
| Default data view | `table` | `sql`, `table` |
| Max result rows | `100` | `50`, `100`, `200`, `500` |
| Density | `comfortable` | `comfortable`, `compact` |

**Response tone definitions:**

| Value | Label | Description |
|-------|-------|-------------|
| `analyst` | Analyst | Data-driven, detailed breakdowns |
| `manager` | Manager | Actionable insights with context |
| `director` | Director | Strategic summaries with key metrics |
| `executive` | Executive | High-level, decision-ready answers |

## SSE Streaming

POST-based SSE (`lib/api/sse.ts`). `EventSource` only supports GET, so the app uses `fetch + ReadableStream`.

| Event | UI update |
|-------|-----------|
| `timing.sync` | Elapsed time sync |
| `title.generated` | Thread title in sidebar |
| `node.start` | Pipeline step progress ring |
| `reasoning.pending` | Thinking placeholder |
| `reasoning.delta` | Append to reasoning accordion |
| `answer.delta` | Stream answer text |
| `validation` | SQL validation status |
| `execute.done` | Populate data table |
| `chart` | Render chart |
| `follow_ups` | Follow-up chips |
| `done` | Finalize message |
| `stopped` | Mark as cancelled |
| `error` | Error toast |

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl+K` or `/` | Search conversations |
| `Cmd/Ctrl+Shift+O` | New chat |
| `Cmd/Ctrl+Shift+P` | Open projects |
| `Cmd/Ctrl+Shift+H` | Chat history |
| `Cmd/Ctrl+.` | Toggle sidebar |
| `Cmd/Ctrl+/` or `?` | Show shortcuts menu |
| `Cmd/Ctrl+S` | Star / unstar thread |
| `Cmd/Ctrl+Shift+C` | Copy last response |
| `Enter` | Send message |
| `Shift+Enter` | New line in message |
| `Esc` | Stop active stream |
| `Cmd+1–9` | Jump to Nth recent thread |

## Authentication

1. User submits login form at `/`
2. `POST /api/v1/auth/login` → JWT + user object
3. Both stored in localStorage (`mti_brain_token`, `mti_brain_user`)
4. All API calls include `Authorization: Bearer <token>`
5. On 401, token is cleared and a `mti-brain:unauthenticated` custom event redirects to `/` via the router (no full-page reload)
6. Login page returns `null` until client-side auth check completes — prevents one-frame flash of the form for already-authenticated users

## Known Dev Issues

**VS Code port forwarding (Windows):**
VS Code and VS Code Insiders can auto-detect and forward port 8000 when uvicorn starts. This intercepts all `localhost:8000` connections before they reach the backend. Symptoms: `/docs` doesn't load, no uvicorn access logs.

Fix options:
1. `View → Ports` in VS Code → right-click port 8000 → **Stop Forwarding Port**
2. Run uvicorn on a different port: `uvicorn app.main:app --port 8001 --reload` and set `NEXT_PUBLIC_API_URL=http://127.0.0.1:8001`

**npm install after cloning:**
The `package-lock.json` may have been generated with a wrong `next` version constraint. Delete it and run:
```bash
npm install --legacy-peer-deps
```
`--legacy-peer-deps` is needed because some Radix UI packages have peer dependency declarations for older React versions that don't match React 19 (they work fine at runtime).

## Getting Started

### Prerequisites

- Node.js 20+ and npm

### Setup

```bash
cd quest/frontend

# Install dependencies
npm install --legacy-peer-deps

# Configure environment
cp .env.example .env
# .env.example already sets NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
# If VS Code intercepts port 8000, run the backend on --port 8001 and change the value accordingly

# Start development server
npm run dev
# Open http://localhost:3000
```

### Docker

```bash
docker build -t mti-brain-frontend .
docker run -p 3000:3000 mti-brain-frontend
```

The container: runs as non-root user (`nextjs`), health-checks port 3000 every 30 s, uses Next.js standalone output, limits Node.js to 256 MB.

## Environment Variables

`NEXT_PUBLIC_*` variables are embedded at **build time** and exposed to the browser.

### Required

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | FastAPI backend base URL. Default: `http://127.0.0.1:8000`. On Windows use `127.0.0.1` not `localhost` (see Known Dev Issues). |

### Optional

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_POSTHOG_KEY` | PostHog project key. When unset, analytics no-ops entirely. |
| `NEXT_PUBLIC_POSTHOG_HOST` | PostHog ingest host. Defaults to PostHog Cloud. |
| `NEXT_DEV_ORIGINS` | Comma-separated extra origins for cross-machine HMR (SSH tunnels, Docker). |

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server with hot reload |
| `npm run build` | Build for production (standalone output) |
| `npm run start` | Start production server |
| `npm run lint` | Run ESLint |

---

## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Backend (FastAPI) | [../backend/README.md](../backend/README.md) |
| Database (PostgreSQL + PgBouncer) | [../database/README.md](../database/README.md) |
| Deployment (AWS CodeDeploy) | [../deploy/README.md](../deploy/README.md) |
Docker or behind an SSH tunnel. |


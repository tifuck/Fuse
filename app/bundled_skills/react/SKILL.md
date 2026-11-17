---
name: react
description: React and Next.js practices for components, state, effects, data fetching and server vs client boundaries. Use when writing, reviewing or refactoring React or Next.js code, or fixing re-render, hydration or stale-state bugs.
metadata:
  category: Build
  inspired-by: vercel-labs/agent-skills
---
# React

Match the project first: check `package.json` for React/Next versions, the router (`app/` vs `pages/`), the state library, styling and test setup. Follow existing patterns over these defaults.

## Components
- One job per component. Split when a file mixes data loading, layout and interaction, or passes the same props through three levels.
- Props in, events out. Name handlers `onX` in props and `handleX` inside.
- Keys: stable ids from data, never array index for lists that reorder, insert or delete.
- Avoid defining components inside other components; it remounts them on every render and loses their state.

## State placement
- Keep state as close as possible to where it is used; lift only to the nearest common parent that needs it.
- Derive, don't store: if a value can be computed from props or other state, compute it during render.
- URL state (filters, tabs, pagination) belongs in the URL/search params so it survives reload and sharing.
- Server data belongs in the data layer (server components, the framework loader, or a query cache like the one the project uses), not copied into `useState`.
- Reach for context for low-frequency values (theme, user, locale). High-frequency values in context re-render every consumer.

## Effects discipline
Use `useEffect` only to sync with something outside React (subscriptions, timers, DOM APIs, non-React widgets). Before writing one, check:
- Transforming data for render? Compute it in render.
- Reacting to a user event? Do it in the event handler.
- Resetting state when a prop changes? Use a `key` on the component.
- Fetching? Prefer the framework or query library; if raw, handle cancellation (`AbortController` or an ignore flag) and races.
Every effect lists all its dependencies and returns a cleanup when it subscribes or schedules anything. Do not silence the exhaustive-deps lint rule; restructure instead.

## Memoization
Add `useMemo`, `useCallback` or `memo` only with evidence: a profiler trace, a measurably slow computation, or a memoized child that needs stable props. Remove memoization that guards nothing. If the project uses the React Compiler, skip manual memoization.

## Next.js (App Router)
- Components are server components by default. Add `"use client"` only at the leaf that needs state, effects, browser APIs or event handlers; keep the boundary small.
- Fetch on the server, close to where data is used; run independent fetches in parallel (`Promise.all`) instead of a waterfall.
- Never import server-only code (secrets, DB clients) into client components; use the `server-only` package if the project does.
- Use `loading.tsx`, `error.tsx` and `Suspense` for streaming and failure states.
- Mutations: server actions or route handlers; validate input on the server and revalidate the affected paths or tags.
- Hydration errors usually mean render output differs between server and client (dates, `Math.random`, `window` checks). Move that logic into an effect or a client-only component.
- Use `next/image`, `next/link` and `next/font` where the project already does.

## Accessibility
Use real elements (`button`, `a`, `label`, `form`) before ARIA. Every input has a label, every icon button an accessible name, and focus is managed when dialogs open and close.

## Verify
Run the project's typecheck, lint and tests (read the scripts in `package.json`). For UI changes, load the page and exercise the changed interaction, including loading, empty and error states. Report what you ran.

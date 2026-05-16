# CartoSky — Feedback System Implementation Plan
**Beta Release — The Weather Forums**

| | |
|---|---|
| **Status** | Draft — Pending Review |
| **Version** | 1.0 |
| **Date** | May 16, 2026 |
| **Author** | Principal Engineering |
| **Audience** | Internal Engineering |

---

## 1. Overview

This document describes the design and implementation plan for a lightweight user feedback system to support the CartoSky public beta on The Weather Forums. The system is designed to be low-friction for beta testers, consistent with CartoSky's existing operational patterns, and ready to surface actionable signal in the admin dashboard from day one.

### 1.1 Goals

- Allow authenticated Weather Forums beta testers to submit categorized feedback from any page in the app.
- Capture passive context (active model, forecast hour, current route) automatically — no manual effort from the user.
- Store submissions natively in a SQLite database, consistent with existing telemetry infrastructure.
- Notify the team via email on each submission so issues surface immediately during the beta window.
- Surface all feedback in the existing admin dashboard for triage and pattern analysis.

### 1.2 Non-Goals for Beta

- No screenshot or file attachment support — text-only to reduce complexity and scope creep.
- No third-party routing (GitHub Issues, Linear, Notion) at launch — that is a post-beta workflow decision.
- No public-facing feedback status or response mechanism.

### 1.3 Decision Rationale: Native Storage

The team evaluated two primary storage strategies: native SQLite (extending the existing telemetry pattern) versus routing submissions directly to a third-party issue tracker. Native storage was selected for the beta for the following reasons:

| Reason | Detail |
|---|---|
| **Consistency** | CartoSky already maintains SQLite-backed telemetry and status databases. A `feedback_service.py` following the same service pattern requires no new infrastructure, no new credentials, and no new deployment dependencies. |
| **Ownership** | All beta feedback data remains in-house. No PII is transmitted to external services without a deliberate post-beta decision to do so. |
| **Speed** | The backend endpoint, service layer, and admin UI can be built and deployed in a single sprint alongside the beta launch, with no external service onboarding. |
| **Post-Beta Path** | A post-beta export or push-to-tracker workflow (GitHub Issues, Linear) can be added as a thin layer on top of the existing store once feedback volume and category patterns are understood. |

---

## 2. System Design

### 2.1 Architecture Overview

The feedback system spans three layers: a new backend service and API route, a globally-mounted frontend widget, and a new admin dashboard tab. All three are additive changes with no modification to existing routes or components.

| Layer | Component |
|---|---|
| Backend service | `backend/app/services/feedback_service.py` (new) |
| API routes | `POST /api/v4/feedback` &nbsp;&nbsp; `GET /api/v4/admin/feedback` |
| Database | `feedback.db` (SQLite, configured via `CARTOSKY_FEEDBACK_DB_PATH` and deployed alongside existing telemetry DBs) |
| Email notification | Async background task via FastAPI `BackgroundTasks` |
| Frontend widget | `frontend/src/components/FeedbackWidget.tsx` (new) |
| Admin surface | New tab in `frontend/src/pages/admin/` (new) |

### 2.2 Database Schema

A new `feedback.db` SQLite database is created and managed by `feedback_service.py`. It follows the same initialization-on-startup pattern used by existing telemetry services.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PK` | Auto-increment primary key |
| `submitted_at` | `DATETIME` | UTC timestamp, set server-side on insert |
| `category` | `TEXT` | One of the five defined categories (enum-validated in Pydantic model) |
| `message` | `TEXT` | User-supplied free text, max 1000 characters |
| `member_id` | `INTEGER` | Weather Forums member id extracted from `TwfSession.member_id`; used for durable rate limiting and admin correlation |
| `forums_display_name` | `TEXT` | Extracted from `TwfSession.display_name` server-side — never accepted from the client payload |
| `page_context` | `TEXT` | The current route/page at time of widget open (e.g. `/`, `/forecast`, `/models`) |
| `model_context` | `TEXT NULLABLE` | Active model slug if the viewer is open, null otherwise |
| `fhr_context` | `INTEGER NULLABLE` | Active forecast hour if the viewer is open, null otherwise |
| `user_agent` | `TEXT` | Browser user-agent string, auto-captured from the request |
| `app_version` | `TEXT` | Frontend build version from `VITE_APP_VERSION` env var |

### 2.3 API Contract

#### `POST /api/v4/feedback`

Accepts a JSON body. Returns `201` on success. Fires the notification email as a background task after responding — email latency does not affect the user's submission response time.

**Request body (Pydantic model `FeedbackSubmission`):**

```
category        string   required  One of: bug, performance, feature, data_accuracy, ui_ux
message         string   required  1–1000 characters
page_context    string   required  Current route
model_context   string   optional  Active model slug
fhr_context     integer  optional  Active forecast hour
app_version     string   optional  Build version string
```

**Rate limiting:** 10 submissions per Weather Forums user per rolling 60-minute window, enforced in the service layer by querying `submitted_at` for the authenticated `member_id`. `forums_display_name` is stored for readability but is not used as the identity key because display names can change or collide. Returns HTTP `429` with a human-readable message on breach.

**Auth:** Requires a valid Weather Forums session. The `display_name` is extracted server-side from `TwfSession.display_name` — it is never accepted from the client payload. Falls back to `member-{id}` if the upstream forum profile returns no name, matching the existing OAuth callback behavior.

#### `GET /api/v4/admin/feedback`

Returns paginated feedback records. Protected by `_require_admin_session(request)`, the local helper in `main.py` used by all existing admin routes. The route is added directly to `main.py` to avoid circular import issues — `feedback_service.py` handles DB logic only.

Supported query parameters:

- `page` / `page_size` for pagination (default `page_size` 50)
- `category` filter (optional, one of the five categories)
- `since` / `until` ISO datetime range filters
- `display_name` partial-match filter

---

## 3. Frontend Widget

### 3.1 Placement and Mount Point

`FeedbackWidget.tsx` is mounted once at the router/root-app level, making it globally available on marketing pages, the viewer, and admin pages without per-page wiring. Because the viewer route is rendered through `frontend/src/pages/viewer.tsx` -> `App.tsx` while marketing/admin pages live in separate route trees, viewer-specific context must be shared through a small `FeedbackContext` provider rather than by passing props only inside `App.tsx`.

The provider owns optional `model_context` and `fhr_context` values. `FeedbackWidget` reads from this provider. `App.tsx` updates the provider when the viewer is active; non-viewer pages naturally submit `null` model/hour context. The trigger button is positioned in the bottom-right corner of the viewport, above the z-index of page content but below map controls on the viewer.

> **Note — Map Controls:** The bottom-right corner is already used by MapLibre's default attribution. The feedback button will be positioned above this zone and styled to be visually distinct. Final pixel positioning should be confirmed against the viewer at all viewport breakpoints before launch.

### 3.2 Widget Behavior

The widget consists of a persistent circular button that opens a compact modal overlay. The full interaction flow is:

1. User taps the feedback button. The modal opens. `page_context`, `model_context`, and `fhr_context` are captured from app state at this moment.
2. User selects a category from five pill/chip buttons (one selection required).
3. User types their message in a text area (required, 1000 char max with live counter).
4. The submitting-as display shows their Weather Forums display name (pre-filled from `/auth/twf/status` response, read-only).
5. User submits. The widget shows a loading state, then a brief success confirmation before closing. On `429`, a friendly rate-limit message is shown in place of the success state.

### 3.3 Context Capture

The widget reads the following state silently on modal open. Users never see or interact with this data — it is attached transparently to every submission to maximize diagnostic value.

- **`page_context`** — read from React Router's `useLocation()` hook.
- **`model_context`** — read from `model` useState in `App.tsx` and published into the shared `FeedbackContext` while the viewer route is active. Because this state is local to `App.tsx`, direct prop-passing from `App.tsx` alone would only cover `/viewer`; the context bridge keeps the globally mounted widget available on every route.
- **`fhr_context`** — read from `forecastHour` useState in `App.tsx` and published into `FeedbackContext` alongside `model_context`. `Number.POSITIVE_INFINITY` is the only sentinel value for the unset state (confirmed). The context publisher normalizes this with `Number.isFinite(forecastHour) ? forecastHour : null` before storing it for the widget.
- **`app_version`** — read from `import.meta.env.VITE_APP_VERSION` at build time.

### 3.4 Categories

Users select exactly one category before submitting. Categories map directly to the `feedback.category` column:

| Display Label | Stored Value | Intended Use |
|---|---|---|
| Bug / Something Broken | `bug` | Functional defects, errors, crashes |
| Performance Issue | `performance` | Slowness, timeouts, rendering lag |
| Feature Request | `feature` | Missing capabilities or workflow gaps |
| Data / Model Accuracy | `data_accuracy` | Incorrect or suspicious forecast values |
| UI / UX Feedback | `ui_ux` | Layout, usability, and design issues |

---

## 4. Email Notifications

### 4.1 Delivery Mechanism

Notifications are sent using Python's stdlib `smtplib` and `email` modules — no additional pip dependency is required. The send is dispatched as a FastAPI `BackgroundTask` immediately after the submission is committed to the database, so email delivery latency does not block or affect the API response time seen by the user.

If the email send fails (SMTP timeout, misconfiguration, etc.), the failure is logged but does not trigger a `5xx` response. The submission is already persisted; the notification is best-effort.

### 4.2 Environment Variables

The following variables are added to `api.env.example` and must be set in the production environment:

```
CARTOSKY_FEEDBACK_DB_PATH Feedback SQLite database path, e.g. /var/lib/cartosky/feedback.sqlite3
FEEDBACK_NOTIFY_EMAIL   Destination address for all notification emails
SMTP_HOST               SMTP server hostname
SMTP_PORT               SMTP server port (typically 587 for STARTTLS)
SMTP_USER               SMTP authentication username
SMTP_PASSWORD           SMTP authentication password
SMTP_FROM               From address for outbound notification emails
```

### 4.3 Email Format

Subject line format:

```
[CartoSky Beta Feedback] [CATEGORY] from display_name
```

Body fields:

- Category, `submitted_at` (UTC), `forums_display_name`
- Message (full text)
- `page_context`, `model_context` (if set), `fhr_context` (if set)
- `app_version`, `user_agent`
- Direct link to the admin feedback tab (if `CARTOSKY_ADMIN_BASE_URL` is set)

---

## 5. Admin Dashboard

### 5.1 New Feedback Tab

A new Feedback tab is added to the existing admin shell in `frontend/src/pages/admin/`. It follows the layout and component patterns already established in the admin surface.

### 5.2 Dashboard Components

- **Summary row** — total submissions, submissions in the last 24h / 7d, and a breakdown by category.
- **Submission volume chart** — a time-series bar chart (daily granularity) showing feedback volume over the beta window. No charting library is currently installed in the frontend (confirmed via `package.json`). Recharts is the recommended addition — it is React-native, well-maintained, and aligns with the Tailwind/Radix stack already in use. Install as a targeted production dependency before Phase 3 because the chart ships in the frontend bundle.
- **Filterable table** — all submissions with columns for `submitted_at`, `category`, `forums_display_name`, `page_context`, `model_context`, `fhr_context`, and a truncated message preview. Full message shown on row expand.
- **Filters** — category dropdown, date range picker, `display_name` search. Applied client-side against the paginated fetch or as server-side query params depending on volume.

### 5.3 Admin Aggregation Contract

The admin UI must not compute global summary stats or daily chart volume from only the current paginated table page. `GET /api/v4/admin/feedback` should return the requested page plus aggregate metadata for the active filters, or a separate summary endpoint should be added before Phase 3. The preferred Phase 1 response shape is:

```
items[]          paginated feedback rows
page            current page
page_size       current page size
total           total rows matching filters
summary         total, last_24h, last_7d, by_category for matching filters
daily_volume[]  date/count rows for the selected date range
```

This keeps the admin dashboard truthful even after feedback volume exceeds one page.

---

## 6. Implementation Phases

The implementation is divided into three sequential phases. Each phase is independently deployable and testable.

| Phase | Focus | Key Deliverables | Est. Effort |
|---|---|---|---|
| **Phase 1** | Backend | `feedback_service.py`, `POST /api/v4/feedback`, `GET /api/v4/admin/feedback` with aggregate metadata, member-id rate limiting, email notification, env var updates, backend tests | 2–3 hrs |
| **Phase 2** | Frontend Widget | `FeedbackContext`, `FeedbackWidget.tsx`, global mount, Weather Forums auth pre-fill, viewer context publishing, category chips, success/error states | 2–3 hrs |
| **Phase 3** | Admin UI | Feedback tab in admin shell, summary stats, volume chart, filterable/paginated table | 1–2 hrs |

### Phase 1 — Backend (Start Here)

- Create `backend/app/services/feedback_service.py` with DB initialization on startup, `insert()`, `get_paginated()`, `get_summary()`, and `check_rate_limit()` methods.
- Configure the SQLite path with `CARTOSKY_FEEDBACK_DB_PATH`, defaulting locally to `./data/feedback.sqlite3` and using `/var/lib/cartosky/feedback.sqlite3` in production.
- Add `FeedbackSubmission` Pydantic request model with category enum validation and message length constraint (1–1000 chars).
- Register `POST /api/v4/feedback` in `main.py`. Extract `member_id` and `display_name` from `TwfSession` server-side via `_require_twf_session(request)`. Validate rate limit by `member_id` before insert. Fire email notification as `BackgroundTask` after successful commit.
- Register `GET /api/v4/admin/feedback` in `main.py` (not in a separate router). Call `_require_admin_session(request)` as the first line, matching the existing admin route pattern. Support pagination, category, datetime range, and `display_name` filters. Include aggregate metadata (`summary`, `daily_volume`, `total`) for the active filters so the admin UI is not dependent on a single page of rows.
- Add `CARTOSKY_FEEDBACK_DB_PATH` and SMTP env vars to `deployment/systemd/api.env.example` with comments.
- Add backend tests for: successful submission, rate limit enforcement by `member_id` (429 on 11th submission within the window), category validation, missing required fields, and admin aggregate metadata accuracy.

### Phase 2 — Frontend Widget

- Create `frontend/src/components/FeedbackWidget.tsx`.
- Create a small `FeedbackContext` provider near the root router/app level so the globally mounted widget can read optional viewer context on every route.
- Mount `FeedbackWidget` once near the root router/app level (not per-page and not only inside `App.tsx`). Confirm z-index and bottom-right positioning against the map viewer at mobile and desktop breakpoints.
- Publish `model` and `forecastHour` from `App.tsx` into `FeedbackContext` while the viewer is active. Treat `forecastHour === Number.POSITIVE_INFINITY` as `null` before submission.
- Read `display_name` from the `/auth/twf/status` response (`status.display_name`) for pre-fill. Display as read-only in the widget UI.
- Implement category chip group (single-select, required), textarea (required, 1000 char max, live counter), submit button with loading state.
- Handle `201` (success dismiss), `429` (rate limit message), and `5xx` (generic error message) response states.

### Phase 3 — Admin UI

- Add `recharts` to production `dependencies` in `package.json` (`npm install recharts`). No other charting library is currently in the frontend.
- Add Feedback tab to the admin shell nav.
- Build summary stats row (total, last 24h, last 7d, by category) using existing card/table components consistent with the current admin surface style.
- Add a Recharts `BarChart` showing daily submission volume over the beta window using backend aggregate data, not only the current table page.
- Build paginated, filterable feedback table with row-expand for full message.

---

## 7. Resolved Pre-Implementation Questions

All open questions identified during planning have been resolved via codebase review. No blockers remain before Phase 1 implementation.

| Question | Resolution |
|---|---|
| **Admin auth pattern** | `_require_admin_session(request)` is a local helper in `main.py`. Both feedback routes are added directly to `main.py` and call this helper as the first line. No FastAPI `Depends()` import required. |
| **Weather Forums session key** | `TwfSession.display_name` is the correct field. Backend extracts via `_require_twf_session(request)`. Frontend reads from `/auth/twf/status` JSON as `status.display_name`. Fallback is `member-{id}`. |
| **App.tsx model/fhr state shape** | Local `useState` only — no existing Context or global store. Add a small `FeedbackContext` bridge so the globally mounted widget can read viewer context when `/viewer` is active. Infinity sentinel is normalized via `Number.isFinite(forecastHour) ? forecastHour : null`. |
| **Global widget mount** | `/viewer` renders `App.tsx`, while marketing and admin pages are separate route trees. Mounting only inside `App.tsx` would not be global. The widget should be mounted near `RouterApp`/root app level and read optional viewer context from `FeedbackContext`. |
| **Charting library** | No charting library is currently installed. Recharts will be added in Phase 3 as a production dependency (`npm install recharts`). Aligns with the existing React/Tailwind/Radix stack. |

---

## 8. Post-Beta Considerations

The following items are intentionally out of scope for the beta but are noted here to inform how the system is structured so that they remain easy to add.

- **Issue tracker routing** — A post-beta workflow can push categorized feedback rows to GitHub Issues or Linear via their APIs. The `feedback_service.py` insert path is the right hook point for this.
- **Screenshot capture** — If screenshots prove high-value, `html2canvas` or a similar client-side capture can be added to the widget's submission payload. The backend schema accepts an optional `attachments` column with no migration risk given SQLite's flexible `ALTER TABLE` support.
- **Triage workflow** — An admin-side `status` field (`open` / `in progress` / `resolved`) and `notes` column can be added to enable lightweight triage directly in the admin dashboard without routing to an external tracker.
- **Feedback volume alerting** — If submission rate spikes significantly (e.g., a flood of bug reports after a bad deploy), a simple Prometheus counter on the `/api/v4/feedback` endpoint can trigger an existing alert rule without any new code in the feedback service.

---

## Appendix: Related Documents

- `docs/TELEMETRY_OVERHAUL.md` — Telemetry ownership and rollout direction (feedback DB follows same service pattern)
- `deployment/systemd/api.env.example` — Env var reference updated in Phase 1
- `backend/app/services/` — Existing service layer for pattern reference
- `frontend/src/pages/admin/` — Admin shell for Phase 3 integration

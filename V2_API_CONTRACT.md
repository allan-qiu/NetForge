# NetForge V2 API and data contract

This contract extends the existing `/api/*` routes. Existing login, registration,
practice history, favorites and active-session routes remain compatible.

## Response shape

- Success: `{ "ok": true, "data": ..., "requestId": "..." }`
- Failure: `{ "ok": false, "error": { "code": "...", "message": "..." }, "requestId": "..." }`
- Lists: `data.items`, `data.page`, `data.pageSize`, `data.total`.
- All timestamps are Unix seconds. Calendar aggregation uses `Asia/Shanghai`.

## Learner routes

- `GET /api/v2/home`
- `GET /api/v2/questions?page&pageSize&q&topic&type&difficulty&source&state`
- `GET /api/v2/questions/{id}`
- `POST /api/v2/practice-sessions`
- `GET /api/v2/practice-sessions/{id}`
- `POST /api/v2/practice-sessions/{id}/answers` with mandatory `submissionId`
- `POST /api/v2/question-feedback`; `GET /api/v2/me/feedback`
- `GET /api/v2/me/learning`; `GET /api/v2/me/heatmap`
- `GET|PATCH /api/v2/me/profile`; `POST /api/v2/me/password`; `POST /api/v2/me/logout-all`
- `GET /api/v2/leaderboard?period=day|week|all`; all active users participate
- `GET /api/v2/users/{id}/profile`; authenticated users receive only public learner summary fields
- `POST /api/v2/me/avatar`; multipart PNG/JPEG/WebP upload, maximum 2 MB
- `GET /api/v2/exam-papers`; `POST /api/v2/exam-attempts`
- `GET /api/v2/exam-attempts/{id}`; `PUT /api/v2/exam-attempts/{id}/answers`
- `POST /api/v2/exam-attempts/{id}/submit`; `GET /api/v2/exam-attempts/{id}/report`
- `GET /api/v2/content?type=article|announcement|changelog`

Answers and explanations are absent from question-list/detail responses. Practice
answers return them only after an idempotent submission. Exam answers are disclosed
only after the attempt is submitted.

## Administrator routes

- `GET /api/v2/admin/dashboard`
- `GET|POST /api/v2/admin/users`; actions `disable`, `restore`, `soft-delete`, `reset-token`
- `GET|POST /api/v2/admin/registration-codes`; actions `revoke`
- `GET|POST /api/v2/admin/questions`; `PATCH /api/v2/admin/questions/{id}`
- `POST /api/v2/admin/question-imports/preview`; `POST .../{batchId}/commit`
- `GET|POST /api/v2/admin/exam-papers`; publish/unpublish actions
- `GET|PATCH /api/v2/admin/feedback`
- `GET|POST /api/v2/admin/content`
- `GET /api/v2/admin/audit-logs`
- `POST /api/v2/admin/backups`; restore is intentionally not exposed without a separate approval workflow.

Roles are `super_admin`, `question_admin`, and `support_admin`. Every sensitive API
checks its permission server-side and writes an audit record. High-risk mutations
require `confirm: true` and are idempotent.

## V2.3 learning extensions

- `GET|PATCH /api/v2/me/appearance`; `GET|PATCH /api/v2/admin/me/appearance` save separate account preferences.
- `GET /api/v2/me/motivation` returns a non-repeated network quote or explicitly labeled offline fallback.
- `POST /api/v2/ai/explain`; `POST /api/v2/reference-document` require owned submitted `sessionId` or submitted `attemptId`, plus `questionId`. References also accept `index`.
- `GET /api/v2/admin/users/{id}?page=1`; authorized business details, without credentials.
- `POST /api/v2/admin/users/{id}/ai-portrait`, `confirm: true`; super-admin AI learning analysis.
- User actions add `hard-delete` with `confirm: true` and exact `confirmUsername`.
- Registration-code and paper actions add `delete`, with explicit confirmation.
- Practice creation accepts `allWrong: true` to include all currently wrong published questions.
- Leaderboard returns `practiceScore`, `examScore`, independent ranks/percentiles and documented formulas.
- Unsupported DELETE returns HTTP405; destructive business actions use authenticated, confirmed POST actions.

## Compatibility and migrations

### V2.4.0 learner study and analysis

- `POST /api/v2/practice-sessions`: `mode: "study"` (or `"memorize"`)
  supports `count: 20 | 60 | 80 | "all"`, default 20. `"all"` selects every
  currently published matching question once; no 100-question truncation. Fewer
  available questions return a smaller set. Topic/type/difficulty/source/state
  and search filters apply. Empty selection returns 400; explicit question IDs
  still support opening a single question. Study reveals create no scored answers.
- AI explanation and reference-document APIs also accept a question contained in
  the authenticated learner's memorize session, if it remains published. This
  does not relax practice-answer or submitted-exam authorization requirements.
- `GET /api/v2/me/ai-summary` returns only this learner's cached analysis:
  `ready`, `text`, `generatedAt`, `stale`, `model`. It does not call the provider.
- `POST /api/v2/me/ai-summary`, body `{confirm: true}`, generates from the
  authenticated learner's actual learning aggregates. No target user ID or raw
  client prompt is accepted. The prompt omits usernames, passwords and tokens.
  Cache keys include aggregate data, and pending/rate limits use the existing AI
  document guard. New activity marks old analysis stale; failed requests can retry.
- Leaderboard `practiceScore = round(correct / (wrong + 1) * 10, 2)`;
  no attempts/all wrong = 0, all correct = correct * 10, no 100-point ceiling.
  `correct`/`wrong` count real valid submissions, including legacy attempts,
  excluding study, skips and administrator corrections. `examScore` stays separate.
  Beijing calendar day starts at 00:00; calendar week starts Monday 00:00;
  all-time includes every historical attempt. Returned formulas describe these
  rules and independent rank/percentile semantics.
- No new database migration is required; analyses reuse account-owned AI documents.

### V2.3.1 administrator user editing

- `PATCH /api/v2/admin/users/{id}` requires `users.edit` (super administrator).
  Body: `{profile, metrics, reason, version, confirm: true}`. `version` is the
  `editVersion` returned by the user-detail GET. Profile fields are `username`,
  `display_name`, `avatar_url`, `updated_at`, `deleted_at`, `delete_reason`.
  User ID, registration time, last login and account status are immutable here.
- Metrics: `answered`, `correct`, `wrong`, `exams`, `submittedExams`, `favorites`,
  `feedback`; nonnegative integers up to 1 billion. Correct cannot exceed answered;
  submitted exams cannot exceed all exams. Values are stored as audited offsets
  from real records; subsequent learning continues to update totals. No synthetic
  answer/exam/favorite/feedback records are created. Rankings use real records.
  Detail GET exposes `actualMetrics`, `metricAdjustments` and `adjustmentReason`.
- `POST /api/v2/admin/users/{id}/password` requires `users.password` (super
  administrator), body `{password, reason, version, confirm: true}`; password length
  8–128. Stores a password hash, revokes all learner sessions and reset tokens.
  Neither the password nor its hash is returned or written to the audit log.
  Legacy administrator password reset also enforces `users.password`.
- Username changes revoke learner sessions. Stale detail versions or duplicate
  usernames return 409, invalid edits 400, missing users 404, unauthorized roles
  403 and unauthenticated requests 401. Both changes require an audit reason.
- Migration `004_user_admin_edits.sql` is additive and leaves existing records
  intact. Users without correction rows retain their original totals.

The legacy tables and routes remain readable. V2 creates additive tables and nullable
columns only. Existing question IDs (`net-0001` etc.) are stable. Existing attempts,
favorites, wrong-state rows, active sessions and users are never deleted by migration.

<wizard-report>
# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics into MindForge AI's FastAPI backend. The existing `app/analytics.py` module was enhanced with `enable_exception_autocapture=True` and `atexit` shutdown registration. Six new server-side events were instrumented across four router/service files, covering the full user engagement lifecycle — messaging, goal management, morning brief delivery, and nightly batch runs. All events are scoped by `user_id` (Supabase UUID) as the PostHog `distinct_id`, so cross-session identity is maintained automatically through the existing auth layer. An `identify()` helper was also added to `analytics.py` for future use when user properties become available.

| Event | Description | File |
|---|---|---|
| `conversation_created` | User starts a new conversation | `app/routers/conversations.py` *(pre-existing)* |
| `dashboard_viewed` | User opens the dashboard | `app/routers/dashboard.py` *(pre-existing)* |
| `message_sent` | User sends a message; properties: `message_length` | `app/routers/messages.py` |
| `goal_created` | User creates a new active goal | `app/routers/goals.py` |
| `goal_fulfilled` | User marks a goal as fulfilled | `app/routers/goals.py` |
| `goal_removed` | User removes/abandons a goal | `app/routers/goals.py` |
| `morning_brief_posted` | Morning brief delivered; properties: `is_sparse`, `has_pattern_data`, `active_goals_count` | `app/morning_brief.py` |
| `nightly_batch_completed` | Nightly pipeline completed; properties: `users_processed`, `parse_succeeded`, `morning_briefs_posted` | `app/routers/admin.py` |

## Next steps

We've built insights and a dashboard to keep an eye on user behavior:

- [Analytics basics (wizard) Dashboard](https://us.posthog.com/project/468367/dashboard/1707649)
- [Messages sent per day](https://us.posthog.com/project/468367/insights/Ka8lf15R)
- [New conversations created](https://us.posthog.com/project/468367/insights/O0U5F8JB)
- [Goal lifecycle events](https://us.posthog.com/project/468367/insights/JzYnq3sX)
- [Morning briefs posted](https://us.posthog.com/project/468367/insights/fY4ftHVm)
- [Daily active users (DAU)](https://us.posthog.com/project/468367/insights/9rDcXCM4)

### Agent skill

We've left an agent skill folder in your project at `.claude/skills/integration-fastapi/`. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.

</wizard-report>

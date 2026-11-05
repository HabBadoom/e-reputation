# Browser fleet — design (Milestone 6, not implemented)

Playwright + Chromium workers, separate deployment from the API.

- **Mission lifecycle**: `CREATED → VALIDATING → QUEUED → ASSIGNED → RUNNING → (PAUSED | WAITING_FOR_HUMAN) → COMPLETED | FAILED | CANCELLED | EXPIRED`. Transitions are enforced server-side in a state machine with an audit event per transition. `VALIDATING` calls the connector policy engine; missions without an authorisation record never reach `QUEUED`.
- **Scheduling**: RabbitMQ queue per priority; workers advertise capacity (CPU, RAM, contexts) and heartbeat; missions with missed heartbeats are re-queued per retry policy.
- **Isolation**: one browser context per mission; authenticated profiles are stored encrypted per `(tenant_id, source_id)` and never mounted for another tenant. Contexts are destroyed after each mission (cleanup manager verifies).
- **Network**: workers sit in their own segment with an egress allowlist; RFC1918, link-local and cloud metadata endpoints blocked at network level, not just in code.
- **Resource limits**: per-worker max contexts, per-mission timeouts, per-tenant concurrency from entitlements.

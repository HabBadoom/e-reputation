# AI agents (Milestone 7)

Code: `backend/app/agents/`. API: `/api/v1/ai/*`. UI: **AI agents** page.

## Principles

1. **Agents call services, not systems.** An agent gets an `AgentContext`, never a database
   session, shell or HTTP client. `ctx.call(tool)` checks the tool is in the agent's allowlist,
   that the member has the tool's permission, and the step deadline. Tools are thin wrappers
   over existing services and run in the tenant's RLS context.
2. **Rights = member ∩ ceiling.** A task runs with the requesting member's *current* role
   permissions, capped by `AGENT_PERMISSION_CEILING` (read data, run monitoring, check/enable
   sources, alert rules, reports, browser). Never members, invitations, billing, organization
   settings, approvals or platform administration.
3. **Computed, not invented.** Analysis agents are deterministic: counts and the documented
   formulas of `REPUTATION_METHODOLOGY.md`. Each finding is labelled *fact* (counted),
   *inference* (rule or model) or *limitation* (what the data cannot say), with its values;
   the UI renders them in the reader's language. Unvalidated NLP tasks are flagged.
4. **Humans decide what is sensitive.** Every proposed action goes through the compliance
   policy (`policy.py`). Low-risk actions within the member's rights run immediately; enabling
   sources, alert rules and any public reply create an approval request. Approval is a human
   API call by an owner/admin; the action is re-checked and executed with the approver's
   rights. Database triggers refuse executing an approval-required action without an approved
   request decided by a member holding `ai:approve`. **Nothing is ever published** — approved
   replies are kept for the team to copy.

## Agents

| Agent | Responsibility | Tools | Model |
|---|---|---|---|
| Orchestrator | Runs a fixed playbook; routes free-text requests | — | only for `ask` (enum-restricted) |
| Monitoring | Projects scheduled and running; proposes running stale ones | monitoring.projects | no |
| Collection | Coverage, restrictions, anti-bot checks met; re-checks failing sources | sources.coverage, challenges.recent | no |
| Discovery | Discovered feeds, frequent unchecked sites | sources.discovery | no |
| Language | Languages, scripts, Arabizi, code-switching; validation status | analytics.languages, nlp.validation | no |
| Entity | Brand / products / competitors; homonyms excluded | analytics.entities, analytics.about_target | no |
| Sentiment | Distribution, uncertainty, validation | analytics.window, nlp.validation | no |
| Reputation | Reputation index vs previous period | analytics.window | no |
| Trend | Emerging topics, volume change | trends.recent, analytics.window | no |
| Crisis | Active / recent potential crises | crises.list | no |
| Competitor | Share of voice and reputation vs competitors | analytics.entities | no |
| Response | Reply drafts in the post's language and the brand's tone | mentions.top, mentions.get, brands.get | yes |
| Reporting | Requests the downloadable report | — | no |
| Compliance | Connector review status; its policy engine checks every action | compliance.connectors, nlp.validation | no |

Each agent has a version, a timeout (cooperative deadline checked around every tool and model
call), a typed output model (re-validated after the run), and a recorded `agent_runs` row
(input, output, tools used, model calls, model id, prompt version, duration, status
`completed / failed / timeout / skipped`). A failing agent leaves no partial writes (savepoint)
and the task continues with the others. Missing AI provider → the model-based step is
`skipped` with the reason, never faked.

## Playbooks

| Task | Steps |
|---|---|
| `brand_health` | compliance, monitoring, collection, language, entity, sentiment, reputation, trend, crisis, competitor, reporting |
| `crisis_response` | crisis, sentiment, trend, response, reporting |
| `competitor_review` | competitor, reputation, trend, reporting |
| `source_discovery` | compliance, collection, discovery |
| `respond_to_mention` | language, response |
| `report` | reporting |
| `ask` | the model picks one of the above (except `respond_to_mention`) or `none` |

## Actions

| Action | Permission | Risk | Approval |
|---|---|---|---|
| run_monitoring | monitoring:write | low | no |
| check_source | sources:write | low | no |
| browser_check_source | browser:run | low | no |
| create_report | reports:create | low | no |
| enable_source | sources:write | medium | **yes** |
| create_default_alert_rules | alerts:manage | medium | **yes** |
| use_response | mentions:read | high | **yes** (editable by the approver) |

Max 12 actions per task; approvals expire after `REPINTEL_AI_APPROVAL_EXPIRY_HOURS` (72).

## Prompt injection

- **Capability separation.** The only agent that reads collected Internet text (Response)
  can only output a draft — strict JSON schema, then re-validated (length, language; links,
  e-mails, phone numbers not present in the brand's own guidelines are refused; tag or prompt
  leakage refused). Its drafts always wait for a human. The only model that chooses something
  (the planner) sees nothing but the member's own request and answers from an enum.
- **Data framing.** Untrusted text is JSON-encoded inside `<data>` with `<`/`>` escaped: it
  cannot close the block or forge tags. Rules live in the system message.
- **Action kinds come from code**, never from model output; parameters are schema-validated
  with extra fields forbidden and ownership-checked.
- **Evaluation suite** (`tests/test_agents.py`, in CI): injected posts in French, Darija/Arabizi,
  Arabic and mixed text; a simulated *compromised* model that obeys the injection (links,
  phone, e-mail, prompt leakage, extra "approve"/"action" fields) — no draft passes, no other
  action appears; planner forced to off-list tasks or brands → refused; out-of-scope requests →
  nothing done.

## Deviation from the master prompt

LangGraph is not used: the playbooks are linear and typed, so a small in-house orchestrator
keeps the control flow explicit and testable without another dependency. Agents are
self-contained classes and can be moved to a graph runtime later if branching is needed.

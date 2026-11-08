# Reputation methodology

Every figure shown in the Analytics, Alerts and Reports screens is computed by code from stored
mentions and their analyses (`backend/app/analytics/`). No AI model writes a number, a date, a
score or a recommendation. When the data is too thin, the product says so instead of estimating.

## Which mentions count

| Rule | Why |
|---|---|
| Reposts (`duplicate_of` set) are excluded | the same content published twice counts once |
| Mentions judged **not about the target** (`about_target = no`, homonyms) are excluded | "Djezzy" the brand vs a person's name |
| The **human correction** wins over the AI label | `coalesce(corrected_*, ai_*)` everywhere |
| `UNCERTAIN` and not-yet-analysed mentions are counted in volume, **not** in sentiment ratios | no invented polarity |
| Days are calendar days in `REPINTEL_ANALYTICS_TIMEZONE` (default `Africa/Algiers`), by **publication date**, collection date when unknown | readable for the client |
| **Spikes, crises and emerging topics use dated mentions only** | the first collection back-fills old articles; dating them "today" would raise false alarms |

## Reputation index

`index = 50 + 50 × (positive − negative) / (positive + negative + neutral + mixed)`

- 50 = balanced, 100 = all positive, 0 = all negative. Rounded to one decimal.
- Shown only when at least `REPINTEL_REPUTATION_MIN_SAMPLE` (default 10) mentions are analysed;
  otherwise the UI and reports print "not enough data" with the sample size.
- The previous period (same length, just before) is shown next to it.

## Share of voice

Mentions of each brand or competitor divided by the total mentions of the brand and its
competitors in the period. Products are reported separately.

## Spikes and emerging topics

Expected level = mean of the previous `REPINTEL_TREND_BASELINE_DAYS` days (default 28).
`z = (observed − expected) / √max(expected, 1)` (Poisson approximation).

| Signal | Condition (defaults, editable per rule) | Severity |
|---|---|---|
| Negative spike | today ≥ 3 negative mentions and z ≥ 3 | Medium; High when z ≥ 6 |
| Emerging topic | topic in ≥ 3 mentions today and z ≥ 3 | Low; Medium when z ≥ 6 |
| Competitor activity | competitor mentions ≥ 3 and z ≥ 3 | Low; Medium when z ≥ 6 (never above Medium) |

Topics come from the AI analysis (`topics` field) and are compared case-insensitively.

## Potential crisis

Assessed per brand at each evaluation:

- open when, today: ≥ 5 negative mentions, z ≥ 3 and negative share ≥ 40 % of the day's (dated) mentions;
- severity **Medium**; **High** if z ≥ 6 or ≥ 3 distinct source domains; **Critical** if (z ≥ 9 and ≥ 5 domains)
  or (High and ≥ 30 % of today's negative mentions express boycott intent or escalation threats);
- one active crisis per brand; severity only escalates while active; peak and metrics are kept;
- **resolved** after `REPINTEL_CRISIS_QUIET_HOURS` (default 48) without detection; its open alerts are resolved.

The word used everywhere is "potential crisis": the system detects an unusual negative signal; a
human decides whether it is a crisis.

## Alerts

- Evaluated every `REPINTEL_ALERT_EVAL_EVERY_MINUTES` (15) for organizations with rules, and right
  after each analysis batch.
- Each fact has a de-duplication key (mention, brand + day, crisis + severity…): it alerts once.
- Filtered by the rule's minimum severity, capped at `REPINTEL_ALERTS_PER_RULE_PER_RUN` (20) per run.
- Channels: in-app; one e-mail digest per recipient (verified members whose role is selected);
  signed webhooks (see API documentation).

## Reports

Sections: summary KPIs with the previous period, rule-based recommendations, daily chart, crises,
top negative and positive mentions (with source, date and repost count), emerging topics, share of
voice and competitors, products, **data limitations** (coverage not exhaustive, undated and
unanalysed counts, failing sources and collections, AI labelling) and this methodology.

Recommendations are fixed sentences triggered by measured conditions: active crisis, escalation
intents among top negatives, negative share up ≥ 10 points, emerging topic, competitor ahead in
share of voice, many uncertain/unanalysed mentions, degraded collection, > 30 % undated, sample
below minimum; otherwise "no warning signal".

## Known limits

- Statistics assume roughly stable collection; adding many sources at once raises volume.
- With little history (new brand), the baseline is near zero and the minimum counts are what
  prevents noise; thresholds are editable per rule.
- Sentiment quality depends on the language benchmark (see `ALGERIAN_NLP.md`); unvalidated tasks
  are labelled as such in the UI.

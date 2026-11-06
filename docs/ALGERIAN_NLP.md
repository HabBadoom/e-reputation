# Algerian language intelligence

Status: Milestone 4. **No accuracy is claimed for any task until it has been measured on `ALGERIAN_NLP_BENCHMARK`.** The UI labels every unvalidated result as such.

## Architecture

```
mention excerpt ──► deterministic layer (free, instant)
                     • script: arabic / latin / arabizi / tifinagh / mixed
                     • heuristic language: ar-DZ / ar / fr / en / UNCERTAIN, code-switching
                     • normaliser: bzaf/bzzaf/bezaf/bezzaf/بزاف → bzaf, elongations, diacritics
                       (original kept; substitutions traced)
                     • lexicon hints (seed Darija expressions)
               └──► AI ensemble (if configured and allowed)
                     • N annotators (default 3) = configured models × prompt variants A/B/C
                     • strict JSON schema output, re-validated server-side
                     • majority vote per label; disputes on sentiment / about_target / sarcasm
                       → arbiter; still unclear → UNCERTAIN
                     • every annotator's output kept for audit
               └──► mention_analysis (+ human corrections stored beside, never over)
               └──► corpus (only if the organization opted in)
```

**Labels:** `sentiment` (positive, negative, neutral, mixed, UNCERTAIN), `sarcasm`, `about_target` (homonym filter), `language`, `dialect`, `intents` (complaint, praise, recommendation, purchase_intent, boycott_intent, support_dissatisfaction, escalation_threat, question, information, comparison), `emotions`, `topics`, French translation.

**Confidence:** the system reports the **annotator agreement** (the share of annotators who gave the final sentiment) and whether arbitration was needed. The annotators' self-reported "confidence" is stored but not presented as a probability. Calibrated probabilities need the benchmark: calibrate on `gold_dev`, report on `gold_test`.

## Automatic annotation and gold set (founder decisions, 2026-11-05)

1. The SaaS annotates automatically: the ensemble above.
2. **Corpus:** opted-in organizations contribute their analysed excerpts. The setting lives in Settings, is off by default, and opting out deletes all of that organization's contributions. Only platform admins can read the corpus.
3. **Gold set:** a platform admin (the founder) runs **"Add 50 texts to the gold set"**. The sample is balanced across scripts so Darija in Arabic script, Latin script and Arabizi are all represented. The admin then **confirms or corrects** each text on the verification screen, where the labels are pre-filled. Target: 300–500 texts, split between `gold_test` (reporting) and `gold_dev` (tuning only).
4. **Tenant corrections:** corrections made by customer teams feed the `train` split, **never** `gold_test`.

Platform admins are designated by an operator in the database. The app cannot grant this flag (protected by a database trigger):

```sql
UPDATE users SET is_platform_admin = true WHERE email = 'founder@example.dz';
```

## Benchmark (`ALGERIAN_NLP_BENCHMARK`)

The benchmark runs the current ensemble configuration on every human-verified item of a split and compares the result with the human labels:

| Task | Metrics |
|---|---|
| sentiment, sarcasm, about_target, language | macro-F1 (over answered items), macro-F1 strict (abstentions counted as errors), precision / recall / F1 per class, confusion matrix, coverage (share not UNCERTAIN), accuracy |
| intents (multi-label) | per-label P/R/F1, macro-F1 over labels |
| language (heuristic baseline) | same metrics for the deterministic detector, for comparison |

Each run records the models, prompt version and analysis version. A task is shown as **validated** only when the latest completed run with the current prompt and analysis versions meets all three thresholds:

- macro-F1 ≥ `REPINTEL_NLP_MIN_MACRO_F1` (0.70 by default)
- coverage ≥ `REPINTEL_NLP_MIN_COVERAGE` (0.60)
- at least `REPINTEL_NLP_MIN_GOLD_ITEMS` gold items (100)

Changing a prompt changes `PROMPT_VERSION`, which invalidates earlier results until the benchmark is run again.

**Cost:** 3 to 4 AI calls per mention or per gold item. Set the models with `REPINTEL_NLP_MODELS` (a JSON list, cycled over the annotators); no model is hard-coded. Using two different models gives more independent annotators than prompt variants of a single model.

## Security

- Third-party text is passed as an escaped JSON value inside `<data>…</data>`, and the system prompt states that it contains no instructions. The model has no tools. Output must match a strict schema and is re-validated against the allowed labels, so anything outside them is rejected (tested).
- **Compliance gate:** sending excerpts to OpenAI is gated by the `openai` compliance record, blocked in production until approved. Personal data of users and customers is never sent; only the excerpt, title and brand name are. This transfer outside Algeria needs review under Law 18-07.

## Expression dictionary

Thirty common Algerian expressions are seeded with their Arabic form, Latin variants, French meaning and usual polarity. They are marked `seed_unvalidated`. They help the normaliser and the AI, and never decide a label on their own: negation (*ma 3jebnich*), irony and context come first.

## Known limits

- The heuristic detector is a baseline. Arabic-script Darija without marker words is often labelled MSA or UNCERTAIN.
- Tamazight (Kabyle, Chaoui) is not reliably supported. Detection covers the Tifinagh script only; Latin-script Kabyle is not distinguished from French.
- Analysis runs once per mention. Re-analysis after a prompt change is manual (Mentions → "Analyse pending mentions" covers only pending, failed or blocked items).

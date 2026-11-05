# Algerian language intelligence — design (Milestone 4, not implemented)

No accuracy is claimed for any language or dialect until it has been measured on `ALGERIAN_NLP_BENCHMARK`.

## What exists today

`app/modules/brands/scripts.py`: a writing-system heuristic (Arabic / Latin / Arabizi / Tifinagh) used only to pre-fill alias metadata. It is not language identification.

## Plan

1. **Annotation workflow first**: schema per master prompt §10.5, two annotators per item, adjudication for disagreement, inter-annotator agreement (Krippendorff's α) reported per label. Only public or licensed text; no private conversations.
2. **Benchmark**: stratified by script (Arabic/Latin/Arabizi), code-switching, region, platform. Separate held-out test set never used for prompt tuning. Metrics: macro-F1, per-class P/R, confusion matrices, error analysis.
3. **Pipeline**: language/script detector → Arabizi processor → normalizer (stores normalized form *beside* the original, keeps a mapping, never merges meaningful distinctions) → LLM-backed sentiment/intent/sarcasm with structured output → confidence calibration (temperature scaling on the dev set). Below threshold ⇒ `UNCERTAIN`.
4. **Expression dictionary**: curated `dialect_expressions` with variants (bzaf/bzzaf/bezaf/bezzaf/بزاف), region, register, sentiment polarity where unambiguous.
5. Support claims in the UI are generated from the latest `language_evaluation_runs`, not hand-written.

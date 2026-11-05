# Algerian language intelligence — design (Milestone 4, not implemented)

No accuracy is claimed for any language or dialect until it has been measured on `ALGERIAN_NLP_BENCHMARK`.

## What exists today

`app/modules/brands/scripts.py`: a writing-system heuristic (Arabic / Latin / Arabizi / Tifinagh) used only to pre-fill alias metadata. It is not language identification.

## Plan

1. **Automatic annotation pipeline**: each public/licensed text is labelled by ≥3 independent LLM annotators (different providers, models or prompts) against the §10.5 schema. Full agreement → label accepted (`auto_agreed`); partial → arbitration by a stronger model with all rationales (`auto_arbitrated`); no consensus → `UNCERTAIN`, excluded from training data. Every label records models, prompt versions and agreement score. User corrections from the Mentions screen are stored as `human_corrected` and take precedence.
2. **Benchmark**: built on a small **human-verified gold set** (auto-labels pre-filled, the founder confirms or fixes them in a one-click review screen — the only manual step; target 300–500 texts). Without it, accuracy would be measured by the same models being evaluated, which proves nothing. Stratified by script (Arabic/Latin/Arabizi), code-switching, region, platform. Separate held-out test set never used for prompt tuning. Metrics: macro-F1, per-class P/R, confusion matrices, error analysis.
3. **Pipeline**: language/script detector → Arabizi processor → normalizer (stores normalized form *beside* the original, keeps a mapping, never merges meaningful distinctions) → LLM-backed sentiment/intent/sarcasm with structured output → confidence calibration (temperature scaling on the dev set). Below threshold ⇒ `UNCERTAIN`.
4. **Expression dictionary**: curated `dialect_expressions` with variants (bzaf/bzzaf/bezaf/bezzaf/بزاف), region, register, sentiment polarity where unambiguous.
5. Support claims in the UI are generated from the latest `language_evaluation_runs`, not hand-written.

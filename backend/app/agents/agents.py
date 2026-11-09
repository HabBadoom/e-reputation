"""The specialised agents. Each: one responsibility, typed output, restricted tools, a timeout
and a version. Analysis agents are deterministic (counts and documented formulas from
app.analytics); only the Response agent calls a model. Findings say whether they are facts
(counted), inferences (rules or model) or limitations."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.agents.base import Agent, AgentContext, AgentOutput, Finding
from app.agents.prompts import (
    RESPONSE_PROMPT_VERSION,
    RESPONSE_SCHEMA,
    llm_json,
    response_system,
    response_user,
    validate_reply,
)

DAYS = 30
ESCALATION = {"boycott_intent", "escalation_threat"}


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


# ------------------------------------------------------------------------ operations
class MonitoringOutput(AgentOutput):
    projects: int = 0
    active: int = 0
    failed_runs_7d: int = 0


class MonitoringAgent(Agent):
    name = "monitoring"
    responsibility = "Checks monitoring projects are scheduled and running; runs stale ones."
    tools = frozenset({"monitoring.projects"})
    Output = MonitoringOutput

    def run(self, ctx: AgentContext) -> MonitoringOutput:
        projects = ctx.call("monitoring.projects", brand_id=ctx.brand_id)
        out = MonitoringOutput(projects=len(projects),
                               active=sum(p["status"] == "active" for p in projects),
                               failed_runs_7d=sum(p["failed_7d"] for p in projects))
        if not projects:
            out.findings.append(Finding(code="monitoring.none", kind="fact", level="warning"))
            return out
        if out.active == 0:
            out.findings.append(Finding(code="monitoring.all_paused", level="warning"))
        if out.failed_runs_7d:
            out.findings.append(Finding(code="monitoring.failures", level="warning",
                                        values={"n": out.failed_runs_7d}))
        now = datetime.now(UTC)
        for p in projects:
            last = p["last_run_at"]
            stale = last is None or last < now - timedelta(minutes=2 * p["frequency_minutes"])
            if p["status"] == "active" and stale and p["last_run_status"] not in ("queued",
                                                                                 "running"):
                ctx.propose("run_monitoring", {"project_id": str(p["id"])},
                            reason="monitoring.stale", project=p["name"])
        return out


class CollectionOutput(AgentOutput):
    by_coverage: dict[str, int] = {}
    challenges_7d: dict[str, int] = {}


class CollectionAgent(Agent):
    name = "collection"
    responsibility = "Reviews source coverage and access problems; re-checks failing sources."
    tools = frozenset({"sources.coverage", "challenges.recent"})
    Output = CollectionOutput

    def run(self, ctx: AgentContext) -> CollectionOutput:
        cov = ctx.call("sources.coverage")
        out = CollectionOutput(by_coverage=cov["by_coverage"])
        if "browser:read" in ctx.permissions:
            out.challenges_7d = ctx.call("challenges.recent", days=7)
        restricted = cov["by_coverage"].get("RESTRICTED", 0)
        if restricted:
            out.findings.append(Finding(code="collection.restricted", level="warning",
                                        values={"n": restricted}))
        if out.challenges_7d:
            out.findings.append(Finding(code="collection.challenges", level="info",
                                        values={"n": sum(out.challenges_7d.values()),
                                                "kinds": sorted(out.challenges_7d)}))
        out.findings.append(Finding(code="collection.not_exhaustive", kind="limitation"))
        for s in cov["failing"][:3]:
            if s["coverage"] in ("UNAVAILABLE", "CONNECTOR_ERROR") and not s["last_challenge"]:
                ctx.propose("check_source", {"source_id": str(s["id"])},
                            reason="collection.failing_source", domain=s["domain"])
        return out


class DiscoveryOutput(AgentOutput):
    candidates: int = 0


class DiscoveryAgent(Agent):
    name = "discovery"
    responsibility = "Finds sources worth adding: discovered feeds, frequent unchecked sites."
    tools = frozenset({"sources.discovery"})
    Output = DiscoveryOutput

    def run(self, ctx: AgentContext) -> DiscoveryOutput:
        d = ctx.call("sources.discovery")
        for f in d["disabled_feeds"]:
            ctx.propose("enable_source", {"source_id": str(f["id"])},
                        reason="discovery.feed_found", url=f["url"])
        for w in d["unchecked_websites"]:
            ctx.propose("check_source", {"source_id": str(w["id"])},
                        reason="discovery.frequent_site", domain=w["domain"],
                        mentions=w["mentions"])
        n = len(d["disabled_feeds"]) + len(d["unchecked_websites"])
        out = DiscoveryOutput(candidates=n)
        out.findings.append(Finding(code="discovery.candidates" if n else "discovery.none",
                                    values={"feeds": len(d["disabled_feeds"]),
                                            "sites": len(d["unchecked_websites"])}))
        return out


# -------------------------------------------------------------------------- analysis
class LanguageOutput(AgentOutput):
    total: int = 0
    languages: dict[str, int] = {}
    scripts: dict[str, int] = {}


class LanguageAgent(Agent):
    name = "language"
    responsibility = "Describes languages, scripts (Arabic, Latin, Arabizi) and code-switching."
    tools = frozenset({"analytics.languages", "nlp.validation"})
    Output = LanguageOutput

    def run(self, ctx: AgentContext) -> LanguageOutput:
        d = ctx.call("analytics.languages", brand_id=ctx.brand_id, days=DAYS)
        out = LanguageOutput(total=d["total"], languages=d["languages"], scripts=d["scripts"])
        if d["total"]:
            top = max(d["languages"].items(), key=lambda kv: kv[1])
            out.findings.append(Finding(code="language.mix", values={
                "top": top[0], "share": _pct(top[1], d["total"]),
                "darija": _pct(d["languages"].get("ar-DZ", 0), d["total"]),
                "arabizi": _pct(d["scripts"].get("arabizi", 0), d["total"]),
                "switching": _pct(d["code_switching"], d["total"])}))
        if not ctx.call("nlp.validation")["tasks"].get("language"):
            out.findings.append(Finding(code="language.not_validated", kind="limitation"))
        return out


class EntityOutput(AgentOutput):
    entities: list[dict[str, Any]] = []
    homonyms_excluded: int = 0


class EntityAgent(Agent):
    name = "entity"
    responsibility = "Separates the brand, its products and competitors; counts homonyms."
    tools = frozenset({"analytics.entities", "analytics.about_target"})
    Output = EntityOutput

    def run(self, ctx: AgentContext) -> EntityOutput:
        ents = ctx.call("analytics.entities", brand_id=ctx.brand_id, days=DAYS)
        about = ctx.call("analytics.about_target", brand_id=ctx.brand_id, days=DAYS)
        out = EntityOutput(entities=[{k: e[k] for k in ("entity_type", "entity_name", "total")}
                                     for e in ents], homonyms_excluded=about.get("no", 0))
        if out.homonyms_excluded:
            out.findings.append(Finding(code="entity.homonyms", kind="inference",
                                        values={"n": out.homonyms_excluded}))
        if about.get("UNCERTAIN"):
            out.findings.append(Finding(code="entity.uncertain", kind="limitation",
                                        values={"n": about["UNCERTAIN"]}))
        return out


class SentimentOutput(AgentOutput):
    totals: dict[str, int] = {}
    negative_share: float | None = None
    validated: bool = False


class SentimentAgent(Agent):
    name = "sentiment"
    responsibility = "Summarises opinions: sentiment distribution, uncertainty, validation."
    tools = frozenset({"analytics.window", "nlp.validation"})
    Output = SentimentOutput

    def run(self, ctx: AgentContext) -> SentimentOutput:
        w = ctx.call("analytics.window", brand_id=ctx.brand_id, days=DAYS)
        c, p = w["current"], w["previous"]
        analysed = c["positive"] + c["negative"] + c["neutral"] + c["mixed"]
        prev = p["positive"] + p["negative"] + p["neutral"] + p["mixed"]
        val = ctx.call("nlp.validation")["tasks"].get("sentiment", False)
        out = SentimentOutput(totals=c, negative_share=_pct(c["negative"], analysed),
                              validated=val)
        out.findings.append(Finding(code="sentiment.distribution", values={
            "total": c["total"], "positive": c["positive"], "negative": c["negative"],
            "neutral": c["neutral"] + c["mixed"], "negative_share": out.negative_share,
            "previous_negative_share": _pct(p["negative"], prev), "days": DAYS}))
        if c["uncertain"] + c["unanalysed"]:
            out.findings.append(Finding(code="sentiment.uncertain", kind="limitation",
                                        values={"n": c["uncertain"] + c["unanalysed"]}))
        if not val:
            out.findings.append(Finding(code="sentiment.not_validated", kind="limitation"))
        return out


class ReputationOutput(AgentOutput):
    value: float | None = None
    previous: float | None = None


class ReputationAgent(Agent):
    name = "reputation"
    responsibility = "Reputation index over time (documented formula, minimum sample)."
    tools = frozenset({"analytics.window"})
    Output = ReputationOutput

    def run(self, ctx: AgentContext) -> ReputationOutput:
        w = ctx.call("analytics.window", brand_id=ctx.brand_id, days=DAYS)
        r, pr = w["reputation"], w["previous_reputation"]
        out = ReputationOutput(value=r["value"], previous=pr["value"])
        if r["value"] is None:
            out.findings.append(Finding(code="reputation.insufficient", kind="limitation",
                                        values={"n": r["sample"], "min": w["min_sample"]}))
            return out
        delta = round(r["value"] - pr["value"], 1) if pr["value"] is not None else None
        level = "warning" if delta is not None and delta <= -10 else "info"
        out.findings.append(Finding(code="reputation.index", level=level, values={
            "value": r["value"], "previous": pr["value"], "delta": delta, "days": DAYS}))
        return out


class TrendOutput(AgentOutput):
    topics: list[dict[str, Any]] = []
    volume_change: float | None = None


class TrendAgent(Agent):
    name = "trend"
    responsibility = "Emerging topics and changes in discussion volume."
    tools = frozenset({"trends.recent", "analytics.window"})
    Output = TrendOutput

    def run(self, ctx: AgentContext) -> TrendOutput:
        topics = ctx.call("trends.recent", brand_id=ctx.brand_id, days=7)
        w = ctx.call("analytics.window", brand_id=ctx.brand_id, days=7)
        cur, prev = w["current"]["total"], w["previous"]["total"]
        out = TrendOutput(topics=[{k: t[k] for k in ("topic", "count", "z", "brand")}
                                  for t in topics[:5]],
                          volume_change=_pct(cur - prev, prev))
        out.findings.append(Finding(code="trend.volume", values={
            "current": cur, "previous": prev, "change": out.volume_change}))
        for t in topics[:3]:
            out.findings.append(Finding(code="trend.topic", kind="inference", values={
                "topic": t["topic"], "count": t["count"], "expected": t["expected"],
                "brand": t["brand"]}))
        return out


class CrisisOutput(AgentOutput):
    active: int = 0


class CrisisAgent(Agent):
    name = "crisis"
    responsibility = "Tracks potential crises (lifecycle from the alert engine)."
    tools = frozenset({"crises.list"})
    Output = CrisisOutput

    def run(self, ctx: AgentContext) -> CrisisOutput:
        crises = ctx.call("crises.list", brand_id=ctx.brand_id, days=14)
        active = [c for c in crises if c["status"] == "active"]
        out = CrisisOutput(active=len(active))
        for c in active:
            m = c["metrics"] or {}
            out.findings.append(Finding(code="crisis.active", kind="inference",
                                        level="critical" if c["severity"] == "CRITICAL"
                                        else "warning",
                                        values={"brand": c["brand"], "severity": c["severity"],
                                                "peak": c["peak_negative"],
                                                "domains": m.get("domains"),
                                                "since": c["started_at"].isoformat()}))
        if not active:
            out.findings.append(Finding(code="crisis.none", values={
                "resolved": sum(c["status"] == "resolved" for c in crises)}))
        return out


class CompetitorOutput(AgentOutput):
    share: dict[str, float | None] = {}


class CompetitorAgent(Agent):
    name = "competitor"
    responsibility = "Share of voice and reputation compared with competitors."
    tools = frozenset({"analytics.entities"})
    Output = CompetitorOutput

    def run(self, ctx: AgentContext) -> CompetitorOutput:
        ents = ctx.call("analytics.entities", brand_id=ctx.brand_id, days=DAYS)
        rel = [e for e in ents if e["entity_type"] in ("brand", "competitor")]
        out = CompetitorOutput(share={e["entity_name"]: e["share"] for e in rel})
        comps = [e for e in rel if e["entity_type"] == "competitor"]
        if not comps:
            out.findings.append(Finding(code="competitor.none", kind="limitation"))
            return out
        mine = max((e for e in rel if e["entity_type"] == "brand"),
                   key=lambda e: e["total"], default=None)
        for c in sorted(comps, key=lambda e: -e["total"])[:3]:
            out.findings.append(Finding(code="competitor.compare", values={
                "competitor": c["entity_name"], "share": c["share"],
                "reputation": c["reputation"],
                "brand_share": mine["share"] if mine else None,
                "brand_reputation": mine["reputation"] if mine else None}))
            if mine and (c["share"] or 0) > (mine["share"] or 0):
                out.findings.append(Finding(code="competitor.ahead", kind="inference",
                                            level="warning",
                                            values={"competitor": c["entity_name"]}))
        return out


# --------------------------------------------------------------------------- response
class ResponseOutput(AgentOutput):
    drafts: int = 0
    rejected: dict[str, int] = {}


class ResponseAgent(Agent):
    name = "response"
    version = "1.0"
    responsibility = ("Drafts replies to negative posts in the post's language and the brand's "
                      "tone. Drafts always wait for a human; nothing is published.")
    timeout_seconds = 120
    tools = frozenset({"mentions.top", "mentions.get", "brands.get"})
    Output = ResponseOutput

    def run(self, ctx: AgentContext) -> ResponseOutput:
        from app.core.config import get_settings
        from app.nlp.detect import detect

        out = ResponseOutput()
        if ctx.params.get("mention_id"):
            targets = [ctx.call("mentions.get", mention_id=ctx.params["mention_id"])]
        else:
            top = ctx.call("mentions.top", brand_id=ctx.brand_id, sentiment="negative", days=7,
                           limit=get_settings().agent_max_response_drafts)
            targets = [ctx.call("mentions.get", mention_id=m["id"]) for m in top]
        if not targets:
            out.findings.append(Finding(code="response.nothing"))
            return out
        for m in targets:
            brand = ctx.call("brands.get", brand_id=m["brand_id"])
            lang = detect(f"{m['title'] or ''} {m['text'] or ''}")
            hint = "darija_latin_script" if lang.script == "arabizi" else lang.language
            data = llm_json(ctx, system=response_system(brand["name"], brand["tone"],
                                                        brand["guidelines"]),
                            user=response_user(m, hint), schema=RESPONSE_SCHEMA,
                            name="response_draft", prompt_version=RESPONSE_PROMPT_VERSION)
            reply, reason = validate_reply(data, brand["guidelines"])
            if reply is None:
                out.rejected[reason or "?"] = out.rejected.get(reason or "?", 0) + 1
                continue
            ctx.propose("use_response", {"mention_id": str(m["id"]), "reply": reply,
                                         "language": data["language"],
                                         "needs_facts": bool(data["needs_facts"])},
                        reason="response.draft", mention_title=(m["title"] or "")[:200],
                        mention_url=m["url"], intents=m["intents"])
            out.drafts += 1
        out.findings.append(Finding(code="response.drafted", kind="inference",
                                    values={"n": out.drafts, "rejected": out.rejected}))
        return out


# ------------------------------------------------------------------- reporting & checks
class ReportingOutput(AgentOutput):
    report_requested: bool = False


class ReportingAgent(Agent):
    name = "reporting"
    responsibility = "Requests the downloadable report when the task asks for one."
    tools = frozenset()
    Output = ReportingOutput

    def run(self, ctx: AgentContext) -> ReportingOutput:
        out = ReportingOutput()
        if ctx.params.get("report_format"):
            ctx.propose("create_report", {
                "kind": ctx.params.get("report_kind", "weekly"),
                "format": ctx.params["report_format"], "locale": ctx.locale,
                "brand_id": str(ctx.brand_id) if ctx.brand_id else None},
                reason="reporting.requested")
            out.report_requested = True
        return out


class ComplianceOutput(AgentOutput):
    connectors: dict[str, str] = {}


class ComplianceAgent(Agent):
    """Its policy engine (app/agents/policy.py) checks every action; as a step it reports
    which collection channels are still under legal review."""
    name = "compliance"
    responsibility = "Checks permissions, source policies and actions."
    tools = frozenset({"compliance.connectors", "nlp.validation"})
    Output = ComplianceOutput

    def run(self, ctx: AgentContext) -> ComplianceOutput:
        from app.core.config import get_settings
        conns = ctx.call("compliance.connectors")
        out = ComplianceOutput(connectors=conns)
        pending = sorted(k for k, v in conns.items() if v != "approved")
        if pending:
            out.findings.append(Finding(
                code="compliance.pending_review", kind="limitation",
                level="warning" if get_settings().env == "production" else "info",
                values={"connectors": pending}))
        return out


AGENTS: dict[str, Agent] = {a.name: a for a in (
    MonitoringAgent(), CollectionAgent(), DiscoveryAgent(), LanguageAgent(), EntityAgent(),
    SentimentAgent(), ReputationAgent(), TrendAgent(), CrisisAgent(), CompetitorAgent(),
    ResponseAgent(), ReportingAgent(), ComplianceAgent())}

"""Alert titles and bodies in French, Arabic and English, built only from measured facts
(counts, baselines, sources). Brand / topic names come from the tenant's own data and are
escaped by every renderer (email HTML, web UI)."""

from typing import Any

_T: dict[str, dict[str, tuple[str, str]]] = {
    "negative_mention": {
        "fr": ("Mention négative : {brand}",
               "Une mention négative de {brand} a été publiée sur {domain}."),
        "ar": ("إشارة سلبية: {brand}", "نُشرت إشارة سلبية إلى {brand} على {domain}."),
        "en": ("Negative mention: {brand}", "A negative mention of {brand} was published on {domain}."),
    },
    "negative_spike": {
        "fr": ("Hausse des mentions négatives : {brand}",
               "{count} mentions négatives aujourd'hui contre {expected} par jour en moyenne "
               "sur les {days} jours précédents (écart z = {z})."),
        "ar": ("ارتفاع الإشارات السلبية: {brand}",
               "{count} إشارات سلبية اليوم مقابل متوسط {expected} يوميًا خلال الأيام الـ{days} "
               "السابقة (الانحراف z = {z})."),
        "en": ("Negative mentions rising: {brand}",
               "{count} negative mentions today versus {expected} per day on average over the "
               "previous {days} days (z = {z})."),
    },
    "crisis": {
        "fr": ("Crise potentielle ({severity}) : {brand}",
               "{count} mentions négatives aujourd'hui (moyenne habituelle {expected}), soit "
               "{share} % des mentions du jour, sur {domains} sources distinctes. "
               "Intentions de boycott ou d'escalade : {escalation} %."),
        "ar": ("أزمة محتملة ({severity}): {brand}",
               "{count} إشارات سلبية اليوم (المتوسط المعتاد {expected})، أي {share}٪ من إشارات "
               "اليوم، عبر {domains} مصادر مختلفة. نوايا المقاطعة أو التصعيد: {escalation}٪."),
        "en": ("Potential crisis ({severity}): {brand}",
               "{count} negative mentions today (usual average {expected}), {share}% of today's "
               "mentions, across {domains} distinct sources. Boycott or escalation intent: "
               "{escalation}%."),
    },
    "emerging_topic": {
        "fr": ("Sujet émergent : « {topic} » ({brand})",
               "Le sujet « {topic} » apparaît dans {count} mentions aujourd'hui contre {expected} "
               "par jour en moyenne (écart z = {z})."),
        "ar": ("موضوع ناشئ: «{topic}» ({brand})",
               "يظهر موضوع «{topic}» في {count} إشارات اليوم مقابل متوسط {expected} يوميًا "
               "(الانحراف z = {z})."),
        "en": ("Emerging topic: “{topic}” ({brand})",
               "The topic “{topic}” appears in {count} mentions today versus {expected} per day "
               "on average (z = {z})."),
    },
    "competitor_spike": {
        "fr": ("Pic d'activité du concurrent {competitor}",
               "{count} mentions de {competitor} aujourd'hui contre {expected} par jour en "
               "moyenne, dont {negative} négatives (écart z = {z})."),
        "ar": ("ذروة نشاط المنافس {competitor}",
               "{count} إشارات إلى {competitor} اليوم مقابل متوسط {expected} يوميًا، منها "
               "{negative} سلبية (الانحراف z = {z})."),
        "en": ("Activity spike for competitor {competitor}",
               "{count} mentions of {competitor} today versus {expected} per day on average, "
               "{negative} of them negative (z = {z})."),
    },
    "run_failed": {
        "fr": ("Échec d'une collecte : {project}",
               "La collecte du projet « {project} » a échoué. La couverture de cette période est "
               "incomplète."),
        "ar": ("فشل عملية جمع: {project}",
               "فشلت عملية الجمع للمشروع «{project}». التغطية لهذه الفترة غير مكتملة."),
        "en": ("Collection failed: {project}",
               "The collection for project “{project}” failed. Coverage for this period is "
               "incomplete."),
    },
    "source_failing": {
        "fr": ("Source en erreur : {domain}",
               "La source {domain} a échoué {errors} fois de suite ({error})."),
        "ar": ("مصدر به أخطاء: {domain}", "فشل المصدر {domain} {errors} مرات متتالية ({error})."),
        "en": ("Source failing: {domain}", "The source {domain} failed {errors} times in a row "
               "({error})."),
    },
}


def describe(event: str, data: dict[str, Any], locale: str) -> tuple[str, str]:
    lang = locale if locale in ("fr", "ar", "en") else "fr"
    title, body = _T[event][lang]
    fmt = {k: ("" if v is None else v) for k, v in data.items()}

    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return "?"
    return title.format_map(_Safe(fmt))[:300], body.format_map(_Safe(fmt))

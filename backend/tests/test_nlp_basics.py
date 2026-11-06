from app.nlp.detect import detect
from app.nlp.metrics import multi_label, single_label
from app.nlp.normalize import normalize


def test_script_and_language_detection():
    d = detect("Le réseau de Djezzy est vraiment nul depuis ce matin")
    assert d.script == "latin" and d.language == "fr" and not d.code_switching
    d = detect("wesh khoya rak tchouf, réseau ta3 djezzy khayeb bzaf lyoum")
    assert d.script == "arabizi" and d.language == "ar-DZ"
    assert {"wesh", "khayeb", "bzaf"} <= set(d.darija_markers)
    d = detect("واش راك خويا، الريزو خايب بزاف")
    assert d.script == "arabic" and d.language == "ar-DZ"
    d = detect("أعلنت الشركة أن الخدمة ستعود خلال ساعات وفق بيان رسمي")
    assert d.script == "arabic" and d.language == "ar"
    d = detect("franchement le service client c'est walou, rani 3ayi men had la situation")
    assert d.code_switching and set(d.shares) >= {"fr", "ar-DZ"}
    assert detect("ok").language == "UNCERTAIN"
    assert detect("").script == "none"
    assert detect("ⴰⵣⵓⵍ ⴼⵍⴰⵡⴻⵏ ⴰⴽⴽ").script == "tifinagh"


def test_normalization_keeps_traceability():
    norm, changes = normalize("Djezzy KHAYB bezzzzaf, ma3jebnich le service")
    assert norm == "djezzy khayeb bzaf, ma 3jebnich le service"
    assert ("bezzzzaf", "bzaf") in changes and ("khayb", "khayeb") in changes
    norm, _ = normalize("Bzaf bzzaf bezaf bezzaf")
    assert norm == "bzaf bzaf bzaf bzaf"
    norm, changes = normalize("مليــــح بَزَّاف")
    assert norm == "مليح بزاف" and changes
    norm, _ = normalize("ya3tik sa7a khoya")
    assert norm == "ya3tik saha khoya"


def test_single_label_metrics():
    gold = ["positive", "negative", "negative", "neutral", "negative"]
    pred = ["positive", "negative", "positive", "UNCERTAIN", "negative"]
    m = single_label(gold, pred)
    assert m["n"] == 5 and m["coverage"] == 0.8
    assert m["per_class"]["negative"] == {"precision": 1.0, "recall": 0.6667, "f1": 0.8,
                                          "support": 3}
    assert m["per_class"]["positive"]["precision"] == 0.5
    assert m["confusion"]["negative"] == {"negative": 2, "positive": 1}
    assert m["macro_f1_strict"] <= m["macro_f1"]
    perfect = single_label(["a", "b"], ["a", "b"])
    assert perfect["macro_f1"] == 1.0 and perfect["coverage"] == 1.0


def test_multi_label_metrics():
    gold = [{"complaint"}, {"complaint", "boycott_intent"}, set()]
    pred = [{"complaint"}, {"complaint"}, {"praise"}]
    m = multi_label(gold, pred)
    assert m["per_label"]["complaint"]["f1"] == 1.0
    assert m["per_label"]["boycott_intent"]["recall"] == 0.0
    assert m["macro_f1"] == 0.5        # mean over labels present in gold

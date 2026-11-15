"""Question answering without any AI language model.
The question's words are matched against the words in the uploaded files:
table names, field names, the values inside fields, and document text.
The matches are turned into one SQL query. Every answer carries a full
'How I got this' trail so it can be checked."""
import re, math, difflib
from . import store, builder, ml
from .store import q, rows, scalar, fmt

GLOSSARY = {}    # business word -> field or value name, from SECOND_BRAIN.md and the in-app list
BUILT_IN = {"revenue": "sales", "turnover": "sales", "income": "sales", "qty": "quantity", "units": "quantity",
            "client": "customer", "clients": "customer", "item": "product", "items": "product", "area": "region",
            "territory": "region", "type": "category", "spend": "amount", "earnings": "sales", "purchases": "orders"}

STOP = set("""a an the of in on at for to from with and or is are was were be been do does did what which who whom whose when where
how me my our we i you your it its this that these those there their them they as about can could would should will shall have has had
please tell give show find get see much many s any all into than then so if not no by per vs versus compare compared each every
across between during within us look up out also just only over under above below more less least most number amount""".split())
AGG = {"sum": ["total", "sum", "overall", "altogether", "combined"],
       "avg": ["average", "avg", "mean", "typical", "typically"],
       "count": ["count", "many"],
       "max": ["highest", "max", "maximum", "largest", "biggest", "most", "top", "best", "greatest", "strongest"],
       "min": ["lowest", "min", "minimum", "smallest", "least", "bottom", "worst", "fewest", "weakest"],
       "distinct": ["unique", "distinct", "different"]}
GROUP_WORDS = {"by", "per", "each", "every", "across"}
GRAINS = {"month": "month", "monthly": "month", "quarter": "quarter", "quarterly": "quarter", "year": "year", "yearly": "year",
          "annual": "year", "annually": "year", "day": "day", "daily": "day"}
TIME_WORDS = {"trend", "time", "history", "historical"}
LIST_WORDS = {"list", "rows", "records", "details", "show", "which", "display"}
ML_FORECAST = {"forecast", "predict", "prediction", "projection", "projected", "expect", "expected", "future", "next", "upcoming", "outlook"}
ML_DRIVER = {"why", "drive", "drives", "driver", "drivers", "influence", "influences", "affect", "affects", "factor", "factors", "explain", "explains", "cause", "causes"}
MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())}); MONTHS["sept"] = 9
OPS = [(("more", "than"), ">"), (("greater", "than"), ">"), (("higher", "than"), ">"), (("at", "least"), ">="), (("less", "than"), "<"),
       (("fewer", "than"), "<"), (("lower", "than"), "<"), (("at", "most"), "<="), (("over",), ">"), (("above",), ">"), (("exceeding",), ">"),
       (("under",), "<"), (("below",), "<")]
AVERAGE_NAMES = re.compile(r"price|rate|score|percent|%|\bage\b|ratio|rating|tenure|discount|margin|cost|per ", re.I)


DOC_WORDS = {"document", "documents", "say", "says", "said", "mention", "mentions", "mentioned", "slide", "slides", "report",
             "presentation", "deck", "written", "policy", "wording", "text"}


def stem(w):
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies"):
        w = w[:-3] + "y"
    for suf in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not (suf == "s" and w.endswith("ss")):
            return w[:-len(suf)]
    return w


def words(text):
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+(?:\.\d+)?", str(text).lower().replace(",", ""))


def set_glossary(entries):
    GLOSSARY.clear()
    for e in entries or []:
        if e.get("term") and e.get("means"):
            GLOSSARY[" ".join(words(e["term"]))] = e["means"]


# ------------------------------------------------------------------ lexicon
def _lexicon():
    """phrase (tuple of word stems) -> list of things it could mean."""
    lex = {}

    def put(phrase, target):
        if phrase:
            lex.setdefault(phrase, []).append(target)
    for ds in store.DATASETS.values():
        nm = tuple(stem(w) for w in words(ds["name"]))
        put(nm, {"kind": "dataset", "ds": ds["id"], "weight": 2.0, "label": f"the table “{ds['name']}”"})
        if len(nm) > 1:
            for w in nm:
                if w not in STOP and not w.isdigit() and len(w) > 2:
                    put((w,), {"kind": "dataset", "ds": ds["id"], "weight": 1.0 / len(nm), "label": f"the table “{ds['name']}”"})
        for c in ds["columns"]:
            toks = tuple(stem(w) for w in words(c["name"]))
            n = len(toks)
            for i in range(n):
                for j in range(i + 1, n + 1):
                    sub = toks[i:j]
                    if all(w in STOP or w.isdigit() or len(w) < 2 for w in sub):
                        continue
                    full = (j - i) == n
                    if not full and all(w in ("id", "no", "code", "date", "name") for w in sub):
                        continue
                    put(sub, {"kind": "column", "ds": ds["id"], "column": c["name"], "full": full,
                              "weight": 3.0 * (j - i) / n, "label": f"the field “{c['name']}”"})
            if c["role"] in ("category", "text", "id") and c["type"] == "text" and c["distinct"] <= 400:
                for (v,) in rows(f"SELECT DISTINCT {q(c['name'])} FROM {q(ds['table'])} WHERE {q(c['name'])} IS NOT NULL"):
                    vt = tuple(stem(w) for w in words(v))
                    if vt and len(vt) <= 5 and not all(w in STOP for w in vt):
                        put(vt, {"kind": "value", "ds": ds["id"], "column": c["name"], "value": v, "weight": 2.0,
                                 "label": f"“{v}” in the field “{c['name']}”"})
    for term, means in list(BUILT_IN.items()) + list(GLOSSARY.items()):
        tk = tuple(stem(w) for w in words(term))
        mk = tuple(stem(w) for w in words(means))
        custom = term in GLOSSARY
        if mk in lex and (custom or tk not in lex):
            for t in lex[mk]:
                lex.setdefault(tk, []).append(dict(t, via=means, weight=t["weight"] + (1 if custom else 0)))
    return lex


def _tokens(question):
    raw = words(question)
    return [{"raw": w, "stem": stem(w), "used": None} for w in raw]


def _match(tokens, lex):
    """Greedy longest-phrase match of the question against the lexicon."""
    matches, i, n = [], 0, len(tokens)
    singles = [k[0] for k in lex if len(k) == 1 and len(k[0]) >= 5]
    while i < n:
        hit = None
        for size in range(min(5, n - i), 0, -1):
            phrase = tuple(t["stem"] for t in tokens[i:i + size])
            if phrase in lex:
                if size == 1 and (tokens[i]["raw"] in STOP or tokens[i]["raw"].isdigit()) and \
                        not any(t.get("full") or t["kind"] == "value" for t in lex[phrase]):
                    continue
                hit = (size, lex[phrase], False)
                break
        if not hit and len(tokens[i]["stem"]) >= 5 and tokens[i]["raw"] not in STOP and not _is_keyword(tokens[i]["raw"]):
            close = difflib.get_close_matches(tokens[i]["stem"], singles, n=1, cutoff=0.84)
            if close:
                hit = (1, lex[(close[0],)], True)
        if hit:
            matches.append({"start": i, "size": hit[0], "targets": hit[1], "fuzzy": hit[2],
                            "words": " ".join(t["raw"] for t in tokens[i:i + hit[0]])})
            i += hit[0]
        else:
            i += 1
    return matches


def _is_keyword(w):
    return any(w in v for v in AGG.values()) or w in GROUP_WORDS or w in GRAINS or w in TIME_WORDS or w in LIST_WORDS \
        or w in ML_FORECAST or w in ML_DRIVER or w in MONTHS


def _pick_dataset(matches):
    scores = {}
    for m in matches:
        best = {}
        for t in m["targets"]:
            best[t["ds"]] = max(best.get(t["ds"], 0), t["weight"])
        for d, w in best.items():
            scores[d] = scores.get(d, 0) + w
    if not scores:
        return None
    def rank(d):
        ds = store.DATASETS[d]
        return (round(scores[d], 3), ds["kind"] == "table", ds["rows"])
    return max(scores, key=rank)


# ------------------------------------------------------------------ main entry
def answer(question, context=None):
    question = (question or "").strip()
    if not question:
        return _not_found(question, "Type a question about your files.")
    if not store.DATASETS and not store.PASSAGES:
        return {"type": "empty", "text": "There is nothing to search yet. Drop a file to begin.", "how": None}
    if context and context.get("kind") == "ml" and context.get("id") in ml.RESULTS:
        r = _answer_ml(question, ml.RESULTS[context["id"]])
        if r:
            return r
    tokens = _tokens(question)
    lex = _lexicon()
    matches = _match(tokens, lex)
    table = _table_answer(question, tokens, matches) if store.DATASETS else None
    text = _text_answer(question, tokens)
    if text and set(words(question)) & DOC_WORDS:
        return _clean(text)
    t_cov = table["_coverage"] if table else -1
    x_cov = text["_coverage"] if text else -1
    if table and (not text or t_cov >= x_cov - 0.15 or table["_strong"]):
        if text and x_cov >= 0.6:
            table["related"] = text["passages"][:2]
        return _clean(table)
    if text and x_cov >= 0.5:
        return _clean(text)
    return _not_found(question)


def _clean(a):
    return {k: v for k, v in a.items() if not k.startswith("_")}


def _not_found(question, text=None):
    return {"type": "not_found", "confidence": "Low",
            "text": text or "I couldn't find that in your uploaded files. I only answer from what you have uploaded, so I won't guess.",
            "suggestions": suggestions()[:4],
            "how": {"matched": [], "ignored": [w for w in words(question) if w not in STOP],
                    "steps": ["Compared every word in your question with the table names, field names, values and document text in your files.",
                              "No word matched closely enough to build an answer."], "sql": None}}


# ------------------------------------------------------------------ answers from tables
def _table_answer(question, tokens, matches):
    ds_id = _pick_dataset(matches)
    if not ds_id:
        return None
    ds = store.DATASETS[ds_id]
    n = len(tokens)
    raw = [t["raw"] for t in tokens]
    matched_notes, cols, vals, ds_named = [], [], [], False
    for m in matches:
        mine = [t for t in m["targets"] if t["ds"] == ds_id]
        if not mine:
            continue
        mine.sort(key=lambda t: (-t["weight"], t["kind"] != "column"))
        t = mine[0]
        # a partial field name that fits several fields: prefer a number field (it is probably what to measure)
        if t["kind"] == "column" and not t.get("full"):
            same = [x for x in mine if x["kind"] == "column" and abs(x["weight"] - t["weight"]) < 0.01]
            pref = [x for x in same if store.col(ds, x["column"])["role"] == "measure"]
            name_hit = [x for x in mine if x["kind"] == "dataset"]
            if name_hit and not pref:
                t = name_hit[0]
            elif pref:
                t = pref[0]
        for k in range(m["start"], m["start"] + m["size"]):
            tokens[k]["used"] = t["kind"]
        note = {"words": m["words"], "meaning": t["label"] + (f" (close spelling)" if m["fuzzy"] else "") +
                (f" — from your business words: “{m['words']}” means “{t['via']}”" if t.get("via") else "")}
        matched_notes.append(note)
        if t["kind"] == "column":
            cols.append({"name": t["column"], "pos": m["start"], "end": m["start"] + m["size"], "col": store.col(ds, t["column"])})
        elif t["kind"] == "value":
            vals.append({"column": t["column"], "value": t["value"], "pos": m["start"]})
        else:
            ds_named = True

    def use(i, kind):
        if tokens[i]["used"] is None:
            tokens[i]["used"] = kind

    # --- what kind of sum? ---
    agg = None
    for i, w in enumerate(raw):
        if tokens[i]["used"]:
            continue
        for a, ws in AGG.items():
            if w in ws:
                if a == "count" and w == "many" and not (i > 0 and raw[i - 1] == "how"):
                    continue
                if a in ("max", "min") and w in ("most", "least") and i > 0 and raw[i - 1] == "at":
                    continue
                agg = agg if agg and a in ("max", "min") and agg not in ("max", "min") else (agg if agg in ("max", "min") and a not in ("max", "min") and False else a if not agg or agg in ("max", "min") or a in ("max", "min") else agg)
                use(i, "keyword")
    superlative = next((a for a in ("max", "min") if any(w in AGG[a] and tokens[i]["used"] == "keyword" for i, w in enumerate(raw))), None)
    plain = next((a for a in ("sum", "avg", "count", "distinct") if any(w in AGG[a] and tokens[i]["used"] == "keyword" for i, w in enumerate(raw))), None)
    if "number" in raw and raw.index("number") + 1 < n and raw[raw.index("number") + 1] == "of" and tokens[raw.index("number")]["used"] is None:
        plain = plain or "count"; use(raw.index("number"), "keyword")
    top_n = None
    for i, w in enumerate(raw):
        if w in ("top", "bottom", "first", "best", "worst", "highest", "lowest", "largest", "smallest") and i + 1 < n and raw[i + 1].isdigit() and tokens[i + 1]["used"] is None:
            top_n = int(raw[i + 1]); use(i + 1, "keyword"); use(i, "keyword")
            superlative = superlative or ("min" if w in ("bottom", "worst", "lowest", "smallest") else "max")

    # --- filters: number comparisons such as "quantity over 5" ---
    filters, filter_cols = [], set()
    for i in range(n):
        for phrase, op in OPS:
            if tuple(raw[i:i + len(phrase)]) == phrase:
                j = i + len(phrase)
                if j < n and re.match(r"^\d+(\.\d+)?$", raw[j]) and tokens[j]["used"] is None:
                    before = [c for c in cols if c["col"]["type"] == "number" and c["end"] <= i and i - c["end"] <= 3]
                    target = before[-1] if before else None
                    if target:
                        num = float(raw[j])
                        filters.append({"field": target["name"], "op": op, "value": int(num) if num == int(num) else num})
                        filter_cols.add(id(target))
                        for k in range(i, j + 1):
                            use(k, "filter")
                break
    # --- filters: values found in the data ---
    by_col = {}
    for v in vals:
        by_col.setdefault(v["column"], []).append(v["value"])
    for c, vs in by_col.items():
        filters.append({"field": c, "op": "is", "value": list(dict.fromkeys(vs))})

    # --- filters: time ---
    date_cols = [c["name"] for c in ds["columns"] if c["type"] == "date"]
    mentioned_dates = [c["name"] for c in cols if c["col"]["type"] == "date"]
    date_col = (mentioned_dates or date_cols or [None])[0]
    time_notes = []
    if date_col:
        latest = store.col(ds, date_col).get("max") or "2000-01-01"
        latest_ok = scalar(f"SELECT MAX({q(date_col)}) FROM {q(ds['table'])} WHERE {q(date_col)} <= date('now', '+2 years')") or latest
        ly, lm = int(latest_ok[:4]), int(latest_ok[5:7])
        year = None
        for i, w in enumerate(raw):
            if re.match(r"^(19|20)\d\d$", w) and tokens[i]["used"] is None:
                year = int(w); use(i, "time")
        for i, w in enumerate(raw):
            if tokens[i]["used"]:
                continue
            nxt = raw[i + 1] if i + 1 < n else ""
            if w in ("last", "previous", "this", "current") and nxt in ("year", "month", "quarter"):
                back = 1 if w in ("last", "previous") else 0
                if nxt == "year":
                    year = ly - back
                    time_notes.append(f"“{w} year” means {year}, counted from the latest date in the data ({latest_ok})")
                elif nxt == "month":
                    mm = ly * 12 + lm - 1 - back
                    filters.append({"field": date_col, "op": "month", "value": f"{mm // 12}-{mm % 12 + 1:02d}"})
                    time_notes.append(f"“{w} month” is counted from the latest date in the data ({latest_ok})")
                else:
                    qq = (ly * 4 + (lm - 1) // 3) - back
                    filters.append({"field": date_col, "op": "quarter", "value": [qq // 4, qq % 4 + 1]})
                    time_notes.append(f"“{w} quarter” is counted from the latest date in the data ({latest_ok})")
                use(i, "time"); use(i + 1, "time")
        month = None
        for i, w in enumerate(raw):
            if w in MONTHS and tokens[i]["used"] is None and (w != "may" or (i > 0 and raw[i - 1] in ("in", "for", "during", "of")) or n <= 4):
                month = MONTHS[w]; use(i, "time")
        quarter = None
        for i, w in enumerate(raw):
            mq = re.match(r"^q([1-4])$", w) if tokens[i]["used"] is None else None
            if w == "q" and i + 1 < n and raw[i + 1] in "1234" and tokens[i]["used"] is None:
                quarter = int(raw[i + 1]); use(i, "time"); use(i + 1, "time")
            elif w == "quarter" and i + 1 < n and raw[i + 1] in ("1", "2", "3", "4") and tokens[i]["used"] is None:
                quarter = int(raw[i + 1]); use(i, "time"); use(i + 1, "time")
            elif mq:
                quarter = int(mq.group(1)); use(i, "time")
        if month and year:
            filters.append({"field": date_col, "op": "month", "value": f"{year}-{month:02d}"})
        elif month:
            filters.append({"field": date_col, "op": "month_of_year", "value": month})
        elif quarter:
            filters.append({"field": date_col, "op": "quarter", "value": [year, quarter]})
        elif year:
            filters.append({"field": date_col, "op": "year", "value": year})

    # --- grouping ---
    groups, grain = [], None
    for i, w in enumerate(raw):
        if tokens[i]["used"]:
            continue
        if w in GRAINS and date_col and (i > 0 and raw[i - 1] in GROUP_WORDS or w.endswith("ly") or w in ("annual", "daily")):
            grain = GRAINS[w]; use(i, "time")
            if i > 0 and raw[i - 1] in GROUP_WORDS:
                use(i - 1, "keyword")
        elif w in TIME_WORDS and date_col and (w != "time" or (i > 0 and raw[i - 1] == "over")):
            grain = grain or builder.default_grain(ds, date_col); use(i, "time")
    measures = [c for c in cols if c["col"]["role"] == "measure" and id(c) not in filter_cols]
    dims = [c for c in cols if c["col"]["role"] != "measure" and id(c) not in filter_cols]
    for c in dims:
        before = raw[max(0, c["pos"] - 3):c["pos"]]
        prev = raw[c["pos"] - 1] if c["pos"] > 0 else ""
        value_filtered = c["name"] in by_col and len(by_col[c["name"]]) == 1
        if prev in GROUP_WORDS or (c["pos"] > 1 and raw[c["pos"] - 2] in GROUP_WORDS and prev in STOP) or prev in ("which", "what", "who") \
                or (groups and prev == "and"):
            groups.append(c)
        elif superlative and not value_filtered and c["col"]["role"] in ("category", "id", "text") and (measures or plain == "count" or True) \
                and not (c["col"]["role"] == "id" and plain in ("count", "distinct")):
            groups.append(c)
    for c in dims:        # "north vs south": compare the values that were named
        if c not in groups and c["name"] in by_col and len(by_col[c["name"]]) > 1:
            groups.append(c)
    for cname, vs in by_col.items():
        if len(vs) > 1 and not any(g["name"] == cname for g in groups):
            groups.append({"name": cname, "col": store.col(ds, cname), "pos": 0})
    yes_notes = []
    for c in dims:        # "customers renewed" -> Renewed is Yes
        if c not in groups and c["name"] not in by_col and c["col"]["distinct"] == 2 and c["col"]["type"] == "text":
            yes = scalar(f"SELECT {q(c['name'])} FROM {q(ds['table'])} WHERE LOWER({q(c['name'])}) IN ('yes', 'true', 'y') LIMIT 1")
            if yes:
                filters.append({"field": c["name"], "op": "is", "value": [yes]})
                by_col[c["name"]] = [yes]
                yes_notes.append(f"Read “{c['name']}” as meaning {c['name']} is {yes}")
    for i, w in enumerate(raw):
        if w in GROUP_WORDS and tokens[i]["used"] is None and groups:
            use(i, "keyword")
    group_names = list(dict.fromkeys(g["name"] for g in groups))
    row_fields = []
    if grain and date_col:
        row_fields.append({"field": date_col, "grain": grain})
    for g in group_names:
        if g == date_col and grain:
            continue
        row_fields.append({"field": g, "grain": builder.default_grain(ds, g)} if store.col(ds, g)["type"] == "date" else {"field": g})
    split = row_fields.pop(1) if len(row_fields) > 1 else None

    # --- forecasts and "what drives" questions go to machine learning ---
    asked = set(raw)
    if (asked & ML_FORECAST) and date_col and not (asked & {"next"} and not asked & (ML_FORECAST - {"next"}) and "next" in raw and not any(x in raw for x in ("month", "months", "year", "quarter", "week", "weeks"))):
        for i, w in enumerate(raw):
            if w in ML_FORECAST or w in ("months", "month", "year", "weeks", "quarter") or (w.isdigit() and i > 0 and raw[i - 1] == "next"):
                use(i, "keyword")
        horizon = next((int(raw[i + 1]) for i, w in enumerate(raw[:-1]) if w == "next" and raw[i + 1].isdigit()), None)
        if horizon and "year" in raw and "years" not in raw:
            horizon = None
        f = ml.forecast({"dataset": ds_id, "date": date_col, "value": measures[0]["name"] if measures else None,
                         "agg": plain if plain in ("sum", "avg") else None, "horizon": horizon,
                         "filters": [x for x in filters if x["field"] != date_col]})
        if "error" not in f:
            return {"type": "ml", "text": f["headline"], "bullets": f["bullets"] + [f["reliability"]["text"]], "ml": f,
                    "confidence": _confidence(tokens)[0],
                    "how": {"matched": matched_notes + [{"words": "forecast / next", "meaning": "a forecast (machine learning)"}],
                            "ignored": _confidence(tokens)[1], "steps": f["steps"], "sql": None},
                    "_coverage": _confidence(tokens)[2], "_strong": True}
    if (asked & ML_DRIVER) and (measures or dims):
        tgt = (measures or dims)[0]["name"]
        try:
            d = ml.drivers({"dataset": ds_id, "target": tgt})
        except Exception:
            d = {"error": "x"}
        if "error" not in d:
            for i, w in enumerate(raw):
                if w in ML_DRIVER:
                    use(i, "keyword")
            return {"type": "ml", "text": d["headline"], "bullets": d["bullets"] + [d["reliability"]["text"]], "ml": d,
                    "confidence": _confidence(tokens)[0],
                    "how": {"matched": matched_notes + [{"words": "why / drives / affects", "meaning": "an analysis of what influences a result (machine learning)"}],
                            "ignored": _confidence(tokens)[1], "steps": d["steps"], "sql": None},
                    "_coverage": _confidence(tokens)[2], "_strong": True}

    # --- decide what to calculate ---
    assumptions = list(time_notes) + yes_notes
    _, _, fwords = builder.where_clause(ds, filters)
    tw = " ".join(t["raw"] for t in tokens if t["used"] == "time")
    if tw:
        meaning = "; ".join(w for f, w in zip(filters, fwords) if f["field"] == date_col and f["op"] != "is") or ""
        if grain:
            meaning = (meaning + "; " if meaning else "") + f"group by {grain} using “{date_col}”"
        matched_notes.append({"words": tw, "meaning": meaning or "a time period"})
    fw = " ".join(t["raw"] for t in tokens if t["used"] == "filter")
    if fw:
        matched_notes.append({"words": fw, "meaning": "; ".join(w for f, w in zip(filters, fwords) if f["op"] in (">", ">=", "<", "<="))})
    kw = [t["raw"] for t in tokens if t["used"] == "keyword" and t["raw"] not in GROUP_WORDS and t["raw"] not in LIST_WORDS]
    if kw:
        matched_notes.append({"words": " ".join(kw), "meaning": "how to calculate or rank the answer"})
    for i, w in enumerate(raw):
        if w in LIST_WORDS and tokens[i]["used"] is None:
            use(i, "keyword")
    wants_list = any(w in ("list", "rows", "records", "details") for w in raw)
    spec = {"dataset": ds_id, "filters": filters}
    values = []
    if measures:
        for mcol in measures[:2]:
            a = plain if plain in ("sum", "avg", "count", "distinct") else None
            if not a:
                a = "avg" if AVERAGE_NAMES.search(mcol["name"]) else "sum"
                if not superlative or row_fields:
                    assumptions.append(f"You did not say total or average for {mcol['name']}, so I used the {'average' if a == 'avg' else 'total'}")
            values.append({"field": mcol["name"], "agg": a})
    elif plain == "distinct" and dims:
        tgt = next((c for c in dims if c["name"] not in group_names), dims[0])
        values.append({"field": tgt["name"], "agg": "distinct"})
    elif plain == "count" or (row_fields and not measures):
        idc = next((c for c in dims if c["col"]["role"] == "id" and c["name"] not in group_names and c["col"]["distinct"] < ds["rows"] - c["col"]["missing"]
                    and not c["col"].get("full_name_hit")), None)
        cov_words = [t["raw"] for t in tokens if t["used"] == "column"]
        after = next((i + 1 for i, w in enumerate(raw[:-1]) if w in ("many", "count") or (w == "number" and raw[i + 1] == "of")), None)
        if after is not None and raw[after] == "of":
            after += 1
        asked_for_id = idc and after is not None and any(c is idc and c["pos"] == after for c in dims)
        if idc and plain == "count" and (not ds_named or asked_for_id):
            values.append({"field": idc["name"], "agg": "distinct"})
            assumptions.append(f"Counted each {idc['name']} once, because the same one appears on several rows")
        else:
            values.append({"field": None, "agg": "count"})
    conf, ignored, coverage = _confidence(tokens)

    if superlative and measures and not row_fields and not plain:
        # "highest sales amount" -> the single biggest row(s)
        spec.update(mode="rows", sort_field=measures[0]["name"], sort="asc" if superlative == "min" else "desc", limit=top_n or 1)
        res = builder.run(spec)
        if not res["rows"]:
            return _no_rows(res, matched_notes, ignored, conf, coverage)
        i = res["columns"].index(measures[0]["name"])
        text = f"The {'lowest' if superlative == 'min' else 'highest'} {measures[0]['name']} is {fmt(res['rows'][0][i])}" + \
               (f" (row {res['rows'][0][0]} of the file)." if (top_n or 1) == 1 else f"; the {top_n} {'lowest' if superlative == 'min' else 'highest'} rows are below.")
        return _pack("rows", text, res, matched_notes, ignored, assumptions, conf, coverage, None)
    if values and (row_fields or measures or plain):
        spec.update(rows=row_fields, split=split, values=values)
        if superlative and row_fields and not grain:
            spec["sort"] = "asc" if superlative == "min" else "desc"
            if top_n:
                spec["limit"] = top_n
        res = builder.run(spec)
        if not res["rows"] or (not row_fields and res["rows_used"] == 0):
            return _no_rows(res, matched_notes, ignored, conf, coverage)
        scope = _scope(res, ds)
        if not row_fields:
            parts = [f"{builder.value_label(v)}{scope} is {fmt(res['rows'][0][i])}" for i, v in enumerate(values)]
            text = "; ".join(parts) + "."
            if values[0]["field"] is None:
                text = f"There are {fmt(res['rows'][0][0])} rows in {ds['name']}{scope}."
            elif values[0]["agg"] == "distinct":
                text = f"There are {fmt(res['rows'][0][0])} different {values[0]['field']} values{scope}."
        else:
            first = res["rows"][0]
            lab, vi = str(first[0]), (1 if not split else None)
            name = res["columns"][1] if not split else builder.value_label(values[0])
            if superlative and vi and not grain:
                text = f"{lab} has the {'lowest' if superlative == 'min' else 'highest'} {name}{scope}: {fmt(first[1])}."
                if len(res["rows"]) > 1:
                    text += f" Next is {res['rows'][1][0]} with {fmt(res['rows'][1][1])}."
            elif superlative and vi and grain:
                best = (min if superlative == "min" else max)(res["rows"], key=lambda r: r[1] if r[1] is not None else 0)
                text = f"{best[0]} has the {'lowest' if superlative == 'min' else 'highest'} {name}{scope}: {fmt(best[1])}."
            elif split:
                text = f"Here is {name} by {row_fields[0]['field']} and {split['field']}{scope}."
            else:
                text = f"Here is {name} by {' and '.join(r['field'] for r in row_fields + ([split] if split else []))}{scope}. " + builder.describe(res).split(". ", 1)[-1]
        return _pack("table" if row_fields else "number", text, res, matched_notes, ignored, assumptions, conf, coverage,
                     {"dataset": ds_id, "rows": row_fields, "split": split, "values": values, "filters": filters,
                      "sort": spec.get("sort"), "limit": spec.get("limit")})
    if filters or wants_list:
        # only filters were recognised: show the matching rows and count them
        spec.update(mode="rows", limit=50)
        if dims and not wants_list:
            pass
        res = builder.run(spec)
        if res["rows_used"] == 0:
            return _no_rows(res, matched_notes, ignored, conf, coverage)
        text = f"{res['rows_used']:,} row{'s' if res['rows_used'] != 1 else ''} in {ds['name']} match{_scope(res, ds)}."
        if res["rows_used"] == 1 and dims:
            row = res["rows"][0]
            text = "; ".join(f"{c['name']}: {fmt(row[res['columns'].index(c['name'])])}" for c in dims[:3]) + f"{_scope(res, ds)}."
        return _pack("rows", text, res, matched_notes, ignored, assumptions, conf, coverage,
                     {"dataset": ds_id, "rows": [], "values": [{"field": None, "agg": "count"}], "filters": filters})
    if dims:
        # only a field was named: summarise that field
        c = dims[0]
        if c["col"]["type"] == "date":
            text = f"{c['name']} in {ds['name']} runs from {c['col'].get('min')} to {c['col'].get('max')}."
            res = builder.run({"dataset": ds_id, "rows": [{"field": c["name"]}], "values": [{"field": None, "agg": "count"}]})
        else:
            res = builder.run({"dataset": ds_id, "rows": [{"field": c["name"]}], "values": [{"field": None, "agg": "count"}], "limit": 25})
            text = f"{c['name']} has {c['col']['distinct']:,} different values in {ds['name']}. The most common is {res['rows'][0][0]} ({fmt(res['rows'][0][1])} rows)."
        return _pack("table", text, res, matched_notes, ignored, assumptions, conf, coverage, res["spec"])
    if ds_named:
        res = builder.run({"dataset": ds_id, "mode": "rows", "limit": 10})
        fields = ", ".join(c["name"] for c in ds["columns"][:8])
        text = f"{ds['name']} has {ds['rows']:,} rows. Its fields are: {fields}{'…' if len(ds['columns']) > 8 else ''}."
        return _pack("rows", text, res, matched_notes, ignored, assumptions, conf, coverage,
                     {"dataset": ds_id, "rows": [], "values": [{"field": None, "agg": "count"}], "filters": []})
    return None


def _scope(res, ds):
    steps = [s for s in res["steps"] if s.startswith("Kept only rows where ")]
    if not steps:
        return ""
    return " where " + steps[0][len("Kept only rows where "):].split(". That left")[0]


def _confidence(tokens):
    content = [t for t in tokens if t["raw"] not in STOP or t["used"]]
    ignored = [t["raw"] for t in content if not t["used"]]
    cov = 1 - len(ignored) / len(content) if content else 0
    return ("High" if not ignored else "Medium" if cov >= 0.6 else "Low"), ignored, cov


def _no_rows(res, matched, ignored, conf, coverage):
    return {"type": "not_found", "text": "I understood the question, but no rows in your data match it.", "confidence": conf,
            "how": {"matched": matched, "ignored": ignored, "steps": res["steps"], "sql": res["sql"]},
            "suggestions": suggestions()[:3], "_coverage": coverage, "_strong": False}


def _pack(kind, text, res, matched, ignored, assumptions, conf, coverage, builder_spec):
    steps = list(res["steps"]) + [a + "." for a in assumptions]
    strong = bool(matched) and coverage >= 0.99
    return {"type": kind, "text": text, "confidence": conf,
            "table": {"columns": res["columns"], "rows": res["rows"][:200], "types": res["types"], "more": max(0, len(res["rows"]) - 200)},
            "chart": res.get("chart") if res.get("chart") and len(res["rows"]) > 1 else None,
            "how": {"matched": matched, "ignored": ignored, "steps": steps, "sql": res["sql"],
                    "source": f"{res['dataset_name']} — {res['rows_used']:,} of {res['rows_total']:,} rows used"},
            "builder": builder_spec, "dataset": res["dataset"], "_coverage": coverage, "_strong": strong}


# ------------------------------------------------------------------ answers from document text
def _text_answer(question, tokens):
    if not store.PASSAGES:
        return None
    qs = list(dict.fromkeys(t["stem"] for t in tokens if t["raw"] not in STOP and len(t["raw"]) > 1
                            and t["raw"] not in (DOC_WORDS - {"policy"})))
    if not qs:
        return None
    docs = [set(stem(w) for w in words(p["text"])) for p in store.PASSAGES]
    n = len(docs)
    idf = {s: math.log(1 + n / (1 + sum(1 for d in docs if s in d))) for s in qs}
    total = sum(idf.values()) or 1
    scored = []
    for p, d in zip(store.PASSAGES, docs):
        hit = [s for s in qs if s in d]
        if hit:
            scored.append((sum(idf[s] for s in hit) / total, len(hit), p, hit))
    if not scored:
        return None
    scored.sort(key=lambda s: (-s[0], -s[1], len(s[2]["text"])))
    best = scored[0]
    if best[0] < 0.5:
        return None
    top = [s for s in scored[:3] if s[0] >= max(0.5, best[0] - 0.25)]
    found = set(best[3])
    raw_hit = [t["raw"] for t in tokens if t["stem"] in found and t["raw"] not in STOP]
    ignored = [t["raw"] for t in tokens if t["raw"] not in STOP and t["stem"] not in found and t["raw"] not in (DOC_WORDS - {"policy"})]
    passages = [{"text": s[2]["text"], "source": s[2]["source"], "where": s[2]["where"],
                 "highlight": [w for w in set(words(s[2]["text"])) if stem(w) in s[3]]} for s in top]
    return {"type": "text", "text": f"I found this in {best[2]['source']} ({best[2]['where']}):", "passages": passages,
            "confidence": "High" if best[0] >= 0.99 else "Medium" if best[0] >= 0.7 else "Low",
            "how": {"matched": [{"words": ", ".join(dict.fromkeys(raw_hit)), "meaning": f"the same words in {best[2]['source']}, {best[2]['where']}"}],
                    "ignored": list(dict.fromkeys(ignored)),
                    "steps": [f"Searched {n:,} pieces of text from your documents for the words in your question.",
                              "Ranked them by how many of your words they contain, giving rarer words more weight.",
                              f"The best piece contains {len(best[3])} of your {len(qs)} key words. The text is quoted exactly as written; nothing was reworded."],
                    "sql": None, "source": f"{best[2]['source']}, {best[2]['where']}"},
            "_coverage": best[0], "_strong": False}


# ------------------------------------------------------------------ questions about a machine learning result
def _answer_ml(question, r):
    w = set(words(question))
    st = {stem(x) for x in w}
    how = {"matched": [{"words": "your question", "meaning": f"the result “{r['title']}” you asked about"}], "ignored": [],
           "steps": r["steps"], "sql": None, "source": r["title"]}
    def out(text, bullets=None):
        return {"type": "ml_note", "text": text, "bullets": bullets or [], "confidence": "High", "how": how, "ml_id": r["id"]}
    if st & {"accurat", "accuracy", "reliabl", "trust", "sure", "confident", "confidenc", "good", "wrong", "error", "certain"} and r.get("reliability"):
        return out(f"Reliability is {r['reliability']['label'].lower()} ({r['reliability']['score']:.0f} out of 100). {r['reliability']['text']}")
    if st & {"band", "rang", "shad", "uncertain", "low", "high", "interval", "best", "worst"} and r["kind"] == "forecast":
        f = r["forecast"]
        return out(f"The shaded band shows where the result will probably land. For {f[0]['label']} it is {fmt(f[0]['lo'])} to {fmt(f[0]['hi'])}; "
                   f"for {f[-1]['label']} it is {fmt(f[-1]['lo'])} to {fmt(f[-1]['hi'])}. About 8 in 10 outcomes should fall inside. "
                   f"It is based on how far off the method was when tested on past {r['unit']}s.")
    if r["kind"] == "forecast":
        for f in r["forecast"]:
            lab = f["label"].lower().split()
            if lab[0] in w or (len(lab) > 1 and lab[0][:3] in {x[:3] for x in w if x in MONTHS} and (lab[-1] in w or not any(x.isdigit() for x in w))):
                return out(f"The forecast for {f['label']} is {fmt(f['value'])}, likely between {fmt(f['lo'])} and {fmt(f['hi'])}.")
        if st & {"season", "pattern", "month", "peak", "strongest", "weakest"} and r.get("season"):
            return out(f"There is a yearly pattern: {r['season']['high']} is usually the strongest month and {r['season']['low']} the weakest.")
        if st & {"trend", "grow", "growth", "declin", "rising", "falling", "direction"}:
            return out(r["bullets"][0])
        if st & {"method", "model", "how", "work", "calculat", "chose", "choos"}:
            return out("I tried these methods and kept the most accurate one:",
                       [f"{m['name']}: {m['accuracy']:.0f}% accurate in the test" + (" (chosen)" if m["chosen"] else "") for m in r["methods"]])
        if st & {"total", "sum", "overall", "altogether"}:
            return out(f"Over the next {len(r['forecast'])} {r['unit']}s the forecast adds up to {fmt(sum(f['value'] for f in r['forecast']))}.")
    if r["kind"] == "drivers":
        for i in r["importance"]:
            if {stem(x) for x in words(i["name"])} <= st:
                return out(f"{i['name']} accounts for about {i['share']:.0f}% of the model's predictive power for {r['target']}. {i['effect']}")
        if st & {"important", "importanc", "matter", "driv", "driver", "factor", "influenc", "affect", "biggest", "main", "top", "why", "most"}:
            return out(r["headline"], [f"{i['name']}: {i['share']:.0f}% of the influence. {i['effect']}" for i in r["importance"][:5]])
        if st & {"method", "model", "how", "work", "calculat"}:
            return out("Here is how the model was built:", r["steps"])
    if r["kind"] == "segments":
        for g in r["groups"]:
            if g["name"].lower().split()[-1] in w and "group" in w:
                return out(f"{g['name']} has {g['size']:,} rows ({g['share']:.0f}%). " + ("; ".join(g["traits"] + g["mix"]) or "It is close to the overall average") + ".")
        if st & {"group", "segment", "differ", "describ", "what"}:
            return out(r["headline"], r["bullets"])
    if st & {"summary", "summaris", "summariz", "explain", "mean", "tell", "result", "describ"}:
        return out(r["headline"], r["bullets"])
    return None


def describe_ml(result_id):
    r = ml.RESULTS[result_id]
    hints = {"forecast": ["How accurate is this?", "What is the likely range?", "Which method did you use?", "Is there a seasonal pattern?"],
             "drivers": ["How accurate is this?", "Which factor matters most?", "How does the model work?"],
             "segments": ["Describe the groups", "How reliable is this?"], "unusual": ["Explain the result"]}[r["kind"]]
    return {"type": "ml_note", "text": r["headline"], "bullets": r["bullets"] + ([r["reliability"]["text"]] if r.get("reliability") else []),
            "confidence": "High", "ml_id": r["id"], "suggestions": hints,
            "how": {"matched": [], "ignored": [], "steps": r["steps"], "sql": None, "source": r["title"]}}


# ------------------------------------------------------------------ starter questions built from the data itself
def suggestions():
    out = []
    tables = sorted((d for d in store.DATASETS.values() if d["kind"] == "table"), key=lambda d: -d["rows"])
    for k, ds in enumerate(tables[:2]):
        measures = [c["name"] for c in ds["columns"] if c["role"] == "measure"]
        cats = [c for c in ds["columns"] if c["role"] == "category" and c["type"] == "text" and c["distinct"] > 2]
        dates = [c["name"] for c in ds["columns"] if c["type"] == "date"]
        m = next((x for x in measures if any(w in x.lower() for w in ("amount", "sales", "revenue", "total", "spend"))), measures[-1] if measures else None)
        two = [c["name"] for c in ds["columns"] if c["role"] == "category" and c["distinct"] == 2]
        if k == 0:
            if m and cats:
                out.append(f"Total {m} by {cats[0]['name']}")
                out.append(f"Which {cats[-1]['name']} has the highest {m}?")
            if m and dates:
                out.append(f"{m} by month")
                out.append(f"Forecast {m} for the next 6 months")
            if cats:
                v = scalar(f"SELECT {q(cats[0]['name'])} FROM {q(ds['table'])} GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1")
                out.append(f"How many {ds['name']} in {v}?")
        else:
            if m and cats:
                out.append(f"Average {m} by {cats[0]['name']}")
        if two:
            out.append(f"What drives {two[-1]}?")
    if store.PASSAGES:
        out.append("What do the documents say about " + _topic() + "?")
    return out[:8]


def _topic():
    """The most distinctive word in the uploaded documents, for a starter question."""
    counts = {}
    for p in store.PASSAGES:
        for w in set(words(p["text"])):
            if len(w) > 5 and w not in STOP:
                counts[w] = counts.get(w, 0) + 1
    common = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return common[0][0] if common else "this"


def _unused():
    out = []
    seen, mixed = set(), []
    for i in range(6):                       # interleave so both tables are represented
        for k in range(0, len(out), 6):
            if k + i < len(out) and out[k + i] not in seen:
                seen.add(out[k + i]); mixed.append(out[k + i])
    return mixed[:8] if mixed else (["What does the document say about …?"] if store.PASSAGES else [])

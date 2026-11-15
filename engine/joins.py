"""Suggests which tables can be linked, and on which field.
Match % = share of rows that find a partner. Accepting a suggestion builds a
combined table (a SQL view) — the user never sees or writes SQL."""
import re, difflib
from . import store
from .store import q, rows, scalar


def _name_sim(a, b):
    n = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return difflib.SequenceMatcher(None, n(a), n(b)).ratio()


def suggest():
    base = [d for d in store.DATASETS.values() if d["kind"] == "table"]
    out = []
    for a in base:
        for b in base:
            if a["id"] == b["id"]:
                continue
            best = None
            for cb in b["columns"]:
                present_b = b["rows"] - cb["missing"]
                if present_b < 2 or cb["distinct"] < 0.98 * present_b or cb["role"] == "measure" or cb["type"] == "date":
                    continue   # the lookup side must have one row per value
                for ca in a["columns"]:
                    if ca["type"] != cb["type"] or ca["role"] in ("measure", "date") or ca["distinct"] < 2:
                        continue
                    present_a = a["rows"] - ca["missing"]
                    if not present_a:
                        continue
                    # skip when A is also unique on this field and B is the bigger table (B -> A is the natural direction)
                    matched = scalar(f"SELECT COUNT(*) FROM {q(a['table'])} x WHERE x.{q(ca['name'])} IN "
                                     f"(SELECT {q(cb['name'])} FROM {q(b['table'])})")
                    match = matched / present_a
                    if match < 0.5:
                        continue
                    sim = _name_sim(ca["name"], cb["name"])
                    if sim < 0.5 and ca["role"] != "id":
                        continue
                    score = 0.65 * match + 0.25 * sim + 0.10 * (1 if cb["distinct"] == present_b else 0.5)
                    if ca["distinct"] == present_a and a["rows"] <= b["rows"] and cb["distinct"] == present_b:
                        score -= 0.2 if a["rows"] < b["rows"] else 0
                    cand = (score, ca, cb, matched, present_a, match)
                    if not best or cand[0] > best[0]:
                        best = cand
            if not best:
                continue
            score, ca, cb, matched, present_a, match = best
            if any(l["a"] == a["id"] and l["b"] == b["id"] for l in store.LINKS):
                continue
            if any(l["a"] == b["id"] and l["b"] == a["id"] for l in store.LINKS):
                continue
            unmatched = present_a - matched
            ex = [r[0] for r in rows(f"SELECT DISTINCT {q(ca['name'])} FROM {q(a['table'])} x WHERE x.{q(ca['name'])} IS NOT NULL AND "
                                     f"x.{q(ca['name'])} NOT IN (SELECT {q(cb['name'])} FROM {q(b['table'])} WHERE {q(cb['name'])} IS NOT NULL) LIMIT 5")]
            conf = "High" if score >= 0.85 else "Medium" if score >= 0.7 else "Low"
            new_fields = [c["name"] for c in b["columns"] if c["name"] != cb["name"]]
            out.append({"key": f"{a['id']}|{ca['name']}|{b['id']}|{cb['name']}", "a": a["id"], "a_name": a["name"], "a_col": ca["name"],
                        "b": b["id"], "b_name": b["name"], "b_col": cb["name"], "match": round(100 * match, 1),
                        "matched": matched, "unmatched": unmatched, "unmatched_examples": ex, "confidence": conf,
                        "score": round(score, 3), "adds": new_fields,
                        "why": [f"{round(100 * match, 1)}% of rows in {a['name']} have a matching {cb['name']} in {b['name']}",
                                f"Every row in {b['name']} has its own {cb['name']}, so nothing gets counted twice",
                                f"The field names are {'the same' if _name_sim(ca['name'], cb['name']) > 0.95 else 'similar' if _name_sim(ca['name'], cb['name']) > 0.6 else 'different, but the values line up'}"]})
    # keep one direction per pair: the one with the better score
    seen, final = {}, []
    for s in sorted(out, key=lambda s: -s["score"]):
        pair = tuple(sorted((s["a"], s["b"])))
        if pair not in seen:
            seen[pair] = 1
            final.append(s)
    return final


def accept(key):
    a, a_col, b, b_col = key.split("|")
    store.LINKS.append({"id": store.next_id("l"), "a": a, "a_col": a_col, "b": b, "b_col": b_col})
    A, B = store.DATASETS[a], store.DATASETS[b]
    store.LOG.append({"text": f"Linked {A['name']} to {B['name']} using {a_col}."})
    return _rebuild(a)


def remove_link(link_id):
    link = next(l for l in store.LINKS if l["id"] == link_id)
    store.LINKS.remove(link)
    _rebuild(link["a"])
    return {"done": True}


def _rebuild(a):
    """(Re)create the combined view for table `a` with all of its accepted links."""
    A = store.DATASETS[a]
    links = [l for l in store.LINKS if l["a"] == a]
    view_id = next((d["id"] for d in store.DATASETS.values() if d["kind"] == "linked" and d["base"] == a), None)
    if view_id:
        store.DB.execute(f"DROP VIEW IF EXISTS {q(store.DATASETS[view_id]['table'])}")
        if not links:
            del store.DATASETS[view_id]
            return {"dataset": None}
    else:
        view_id = store.next_id()
    used = {c["name"].lower() for c in A["columns"]}
    select, joins, names, parts = [f"a.{q(c['name'])} AS {q(c['name'])}" for c in A["columns"]], [], [c["name"] for c in A["columns"]], []
    for i, l in enumerate(links):
        B = store.DATASETS[l["b"]]
        alias = f"b{i}"
        for c in B["columns"]:
            if c["name"] == l["b_col"]:
                continue
            out = c["name"] if c["name"].lower() not in used else f"{c['name']} ({B['name']})"
            used.add(out.lower())
            select.append(f"{alias}.{q(c['name'])} AS {q(out)}")
            names.append(out)
        joins.append(f"LEFT JOIN (SELECT * FROM {q(B['table'])} GROUP BY {q(l['b_col'])}) {alias} "
                     f"ON a.{q(l['a_col'])} = {alias}.{q(l['b_col'])}")
        parts.append({"link": l["id"], "with": B["name"], "on": l["a_col"]})
    view = f"view_{view_id}"
    store.DB.execute(f"CREATE VIEW {q(view)} AS SELECT a._row AS _row, {', '.join(select)} FROM {q(A['table'])} a {' '.join(joins)}")
    store.DATASETS[view_id] = {"id": view_id, "name": " + ".join([A["name"]] + [p["with"] for p in parts]), "table": view,
                               "source": "linked tables", "where": None, "kind": "linked", "base": a, "parts": parts,
                               "columns": [{"name": n} for n in names], "dismissed": []}
    store.refresh(view_id)
    return {"dataset": view_id}

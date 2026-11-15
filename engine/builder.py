"""Turns a drag-and-drop layout (rows / split / values / filters) into SQL,
runs it, and explains each step in plain English."""
import io, csv, base64
from . import store
from .store import q, rows, scalar, fmt

AGG_SQL = {"sum": "SUM({c})", "avg": "AVG({c})", "count": "COUNT({c})", "distinct": "COUNT(DISTINCT {c})",
           "min": "MIN({c})", "max": "MAX({c})"}
AGG_WORDS = {"sum": "Total", "avg": "Average", "count": "Count of", "distinct": "Number of different", "min": "Lowest", "max": "Highest"}
AGG_STEP = {"sum": "added up", "avg": "took the average of", "count": "counted the entries in", "distinct": "counted the different values of",
            "min": "found the lowest", "max": "found the highest"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def grain_sql(c, grain):
    if grain == "year":
        return f"strftime('%Y', {c})"
    if grain == "quarter":
        return f"strftime('%Y', {c}) || ' Q' || ((CAST(strftime('%m', {c}) AS INTEGER) + 2) / 3)"
    if grain == "month":
        return f"strftime('%Y-%m', {c})"
    if grain == "month_of_year":
        return f"strftime('%m', {c})"
    return c


def default_grain(ds, field):
    c = store.col(ds, field)
    if c["type"] != "date" or not c.get("min"):
        return None
    days = (int(c["max"][:4]) - int(c["min"][:4])) * 365 + (int(c["max"][5:7]) - int(c["min"][5:7])) * 30
    return "year" if days > 365 * 6 else "month" if days > 62 else "day"


def value_label(v):
    if not v.get("field"):
        return "Number of rows"
    return f"{AGG_WORDS[v['agg']]} {v['field']}"


def _filter_sql(ds, f):
    c = q(f["field"])
    op, v = f.get("op", "is"), f.get("value")
    words = f["field"]
    if op == "is":
        vals = v if isinstance(v, list) else [v]
        return f"{c} IN ({','.join('?' * len(vals))})", list(vals), f"{words} is {' or '.join(fmt(x) for x in vals)}"
    if op == "is_not":
        vals = v if isinstance(v, list) else [v]
        return f"({c} NOT IN ({','.join('?' * len(vals))}) OR {c} IS NULL)", list(vals), f"{words} is not {' or '.join(fmt(x) for x in vals)}"
    if op in (">", ">=", "<", "<="):
        w = {">": "is more than", ">=": "is at least", "<": "is less than", "<=": "is at most"}[op]
        return f"{c} {op} ?", [v], f"{words} {w} {fmt(v)}"
    if op == "between":
        return f"{c} BETWEEN ? AND ?", list(v), f"{words} is between {fmt(v[0])} and {fmt(v[1])}"
    if op == "year":
        return f"strftime('%Y', {c}) = ?", [str(v)], f"{words} is in {v}"
    if op == "month":          # 'YYYY-MM'
        return f"strftime('%Y-%m', {c}) = ?", [v], f"{words} is in {MONTHS[int(v[5:7]) - 1]} {v[:4]}"
    if op == "month_of_year":  # 1..12
        return f"CAST(strftime('%m', {c}) AS INTEGER) = ?", [int(v)], f"{words} is in {MONTHS[int(v) - 1]} (any year)"
    if op == "quarter":        # [year or None, q]
        y, k = v
        sql = f"((CAST(strftime('%m', {c}) AS INTEGER) + 2) / 3) = ?"
        if y:
            return sql + f" AND strftime('%Y', {c}) = ?", [int(k), str(y)], f"{words} is in quarter {k} of {y}"
        return sql, [int(k)], f"{words} is in quarter {k} (any year)"
    if op == "blank":
        return f"{c} IS NULL", [], f"{words} is blank"
    raise ValueError(f"Unknown filter {op}")


def where_clause(ds, filters):
    parts, params, words = [], [], []
    for f in filters or []:
        s, p, w = _filter_sql(ds, f)
        parts.append(s); params += p; words.append(w)
    return (" WHERE " + " AND ".join(parts) if parts else ""), params, words


def pretty_sql(sql, params):
    out = sql
    for d in store.DATASETS.values():      # show the friendly table name, not the internal one
        out = out.replace(q(d["table"]), q(d["name"]))
    for p in params:
        out = out.replace("?", "'" + str(p).replace("'", "''") + "'" if isinstance(p, str) else str(p), 1)
    return out


def _label(v, grain):
    if v is None:
        return "(blank)"
    if grain == "month_of_year":
        return MONTHS[int(v) - 1]
    if grain == "month":
        return f"{MONTHS[int(v[5:7]) - 1][:3]} {v[:4]}"
    return v


def run(spec, limit_display=1000):
    ds = store.DATASETS[spec["dataset"]]
    t = q(ds["table"])
    row_fields = [r for r in spec.get("rows") or [] if r.get("field")]
    split = spec.get("split") if (spec.get("split") or {}).get("field") else None
    values = spec.get("values") or []
    where, params, filter_words = where_clause(ds, spec.get("filters"))
    total = ds["rows"]
    used = scalar(f"SELECT COUNT(*) FROM {t}{where}", params)
    steps = [f"Started with “{ds['name']}” ({total:,} rows) from {ds['source']}."]
    if filter_words:
        steps.append(f"Kept only rows where {' and '.join(filter_words)}. That left {used:,} of {total:,} rows.")
    out = {"dataset": ds["id"], "dataset_name": ds["name"], "rows_used": used, "rows_total": total, "spec": spec}

    if spec.get("mode") == "rows":                       # plain list of matching rows
        names = spec.get("columns") or [c["name"] for c in ds["columns"]]
        order = ""
        if spec.get("sort_field"):
            order = f" ORDER BY {q(spec['sort_field'])} {'ASC' if spec.get('sort') == 'asc' else 'DESC'} NULLS LAST"
            steps.append(f"Sorted by {spec['sort_field']}, {'lowest' if spec.get('sort') == 'asc' else 'highest'} first.")
        lim = int(spec.get("limit") or 50)
        sql = f"SELECT _row, {', '.join(q(n) for n in names)} FROM {t}{where}{order} LIMIT {lim}"
        data = [list(r) for r in rows(sql, params)]
        steps.append(f"Showing {len(data):,} row{'s' if len(data) != 1 else ''}" + (f" (the first {lim})." if used > lim else "."))
        return out | {"columns": ["Row in file"] + names, "rows": data, "types": ["number"] + [store.col(ds, n)["type"] for n in names],
                      "sql": pretty_sql(sql, params), "steps": steps, "chart": None}

    if not values:
        values = [{"field": None, "agg": "count"}]
    dims = []
    for r in row_fields + ([split] if split else []):
        grain = r.get("grain") or default_grain(ds, r["field"])
        dims.append((r["field"], grain, grain_sql(q(r["field"]), grain) if grain else q(r["field"])))
    vals_sql = [AGG_SQL[v["agg"]].format(c=q(v["field"])) if v.get("field") else "COUNT(*)" for v in values]
    labels = [value_label(v) for v in values]
    select = [f"{d[2]} AS d{i}" for i, d in enumerate(dims)] + [f"{s} AS v{i}" for i, s in enumerate(vals_sql)]
    group = f" GROUP BY {', '.join(f'd{i}' for i in range(len(dims)))}" if dims else ""
    time_axis = bool(dims) and dims[0][1] is not None
    sort = spec.get("sort") or ("label" if time_axis else "desc")
    order = " ORDER BY d0" if dims and sort == "label" else f" ORDER BY v0 {'ASC' if sort == 'asc' else 'DESC'}" if dims else ""
    sql = f"SELECT {', '.join(select)} FROM {t}{where}{group}{order}"
    data = rows(sql, params)
    if dims:
        steps.append("Grouped the rows by " + " and ".join(d[0] + (f" ({d[1].replace('_', ' ')})" if d[1] and d[1] != "day" else "") for d in dims) + ".")
    for v in values:
        what = f"{AGG_STEP[v['agg']]} {v['field']}" if v.get("field") else "counted the rows"
        steps.append(f"For each group, {what}." if dims else f"{what[0].upper()}{what[1:]}.")
    limit = spec.get("limit")
    n_row_dims = len(row_fields)
    if split and data:                                   # pivot the split field into columns
        v0 = [r[len(dims)] or 0 for r in data]
        totals = {}
        for r, v in zip(data, v0):
            totals[r[n_row_dims]] = totals.get(r[n_row_dims], 0) + abs(v or 0)
        top = [k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])][:7]
        keys, table = [], {}
        for r in data:
            key = tuple(r[:n_row_dims])
            if key not in table:
                table[key] = {}; keys.append(key)
            s = r[n_row_dims] if r[n_row_dims] in top else "Other"
            table[key][s] = (table[key].get(s) or 0) + (r[len(dims)] or 0) if values[0]["agg"] in ("sum", "count") or s != "Other" else None
        series_names = top + (["Other"] if len(totals) > 7 and values[0]["agg"] in ("sum", "count") else [])
        if sort != "label":
            keys.sort(key=lambda k: sum(v or 0 for v in table[k].values()), reverse=(sort != "asc"))
        if limit:
            keys = keys[:int(limit)]
        columns = [d[0] for d in dims[:n_row_dims]] + [str(_label(s, dims[-1][1])) for s in series_names]
        result = [[_label(k[i], dims[i][1]) for i in range(n_row_dims)] + [table[k].get(s) for s in series_names] for k in keys]
        steps.append(f"Split the result into one column for each {split['field']}" + (" (the 7 largest, the rest as “Other”)." if "Other" in series_names else "."))
        chart_series = [{"name": str(_label(s, dims[-1][1])), "data": [table[k].get(s) for k in keys]} for s in series_names]
        chart_labels = [" · ".join(str(_label(k[i], dims[i][1])) for i in range(n_row_dims)) or labels[0] for k in keys]
        types = ["text"] * n_row_dims + ["number"] * len(series_names)
    else:
        if limit and dims:
            steps.append(f"Sorted {'lowest' if sort == 'asc' else 'highest'} first and kept the top {limit}.")
            data = data[:int(limit)]
        columns = [d[0] for d in dims] + labels
        result = [[_label(r[i], dims[i][1]) for i in range(len(dims))] + list(r[len(dims):]) for r in data]
        chart_labels = [" · ".join(str(x) for x in r[:len(dims)]) for r in result] if dims else labels
        chart_series = [{"name": labels[i], "data": [r[len(dims) + i] for r in result]} for i in range(len(values))] if dims else \
                       [{"name": "Value", "data": list(result[0])}] if result else []
        types = ["text"] * len(dims) + ["number"] * len(values)
    kind = spec.get("chart") or "auto"
    if kind == "auto":
        kind = "line" if time_axis and len(result) > 1 else "bar"
    chart = {"type": kind, "labels": [str(x) for x in chart_labels], "series": chart_series,
             "x_title": dims[0][0] if dims else "", "y_title": labels[0] if len(values) == 1 else "",
             "time": time_axis} if dims and result else None
    truncated = len(result) > limit_display
    return out | {"columns": columns, "rows": result[:limit_display], "types": types, "truncated": truncated,
                  "sql": pretty_sql(sql, params), "steps": steps, "chart": chart}


def export(spec, kind="xlsx"):
    r = run(spec, limit_display=10 ** 7)
    if kind == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(r["columns"]); w.writerows(r["rows"])
        data = buf.getvalue().encode("utf-8-sig")
    else:
        import openpyxl
        from openpyxl.styles import Font
        wb = openpyxl.Workbook()
        ws = wb.active; ws.title = "Table"
        ws.append(r["columns"])
        for c in ws[1]:
            c.font = Font(bold=True)
        for row in r["rows"]:
            ws.append(row)
        for i, name in enumerate(r["columns"]):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i + 1)].width = max(12, min(40, len(str(name)) + 4))
        how = wb.create_sheet("How this was made")
        how.append(["How this table was made"]); how["A1"].font = Font(bold=True)
        for i, s in enumerate(r["steps"], 1):
            how.append([f"{i}. {s}"])
        how.append([]); how.append(["Technical detail (the exact query that was run)"]); how.append([r["sql"]])
        how.column_dimensions["A"].width = 110
        buf = io.BytesIO(); wb.save(buf); data = buf.getvalue()
    return {"base64": base64.b64encode(data).decode(), "filename": f"{r['dataset_name']} table.{kind}"}


def field_values(ds_id, field, limit=200):
    ds = store.DATASETS[ds_id]
    c = q(field)
    return [r[0] for r in rows(f"SELECT {c}, COUNT(*) n FROM {q(ds['table'])} WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT {limit}")]


def describe(result):
    """A plain-English reading of a built table (used by 'Ask about this')."""
    rws, cols = result["rows"], result["columns"]
    if not rws:
        return "This table is empty, so there is nothing to describe yet."
    num_idx = [i for i, t in enumerate(result["types"]) if t == "number" and cols[i] != "Row in file"]
    lab_idx = [i for i, t in enumerate(result["types"]) if t != "number"]
    if not num_idx or not lab_idx:
        return f"This table has {len(rws):,} rows and {len(cols)} columns."
    i, name = num_idx[0], cols[num_idx[0]]
    pts = [(" · ".join(str(r[j]) for j in lab_idx), r[i]) for r in rws if r[i] is not None]
    if not pts:
        return "There are no numbers in this table to describe."
    hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
    total = sum(p[1] for p in pts)
    out = [f"{name} across {len(pts):,} {cols[lab_idx[0]]} groups.",
           f"Highest: {hi[0]} with {fmt(hi[1])}. Lowest: {lo[0]} with {fmt(lo[1])}."]
    if name.startswith(("Total", "Number", "Count")) and total:
        out.append(f"Together they come to {fmt(total)}; {hi[0]} is {100 * hi[1] / total:.0f}% of that.")
    else:
        out.append(f"The typical value is about {fmt(total / len(pts))}.")
    if (result.get("chart") or {}).get("time") and len(pts) >= 4 and pts[0][1]:
        change = 100 * (pts[-1][1] - pts[0][1]) / abs(pts[0][1])
        out.append(f"From {pts[0][0]} to {pts[-1][0]} it went {'up' if change >= 0 else 'down'} {abs(change):.0f}% "
                   f"({fmt(pts[0][1])} → {fmt(pts[-1][1])}).")
    return " ".join(out)

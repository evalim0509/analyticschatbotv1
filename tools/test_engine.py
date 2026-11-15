"""End-to-end check of the Python engine on the sample data.  Run:  python tools/test_engine.py"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import api

def call(fn, **kw):
    blob = kw.pop("data", None)
    r = json.loads(api.dispatch(fn, json.dumps(kw), blob))
    assert r["ok"], r
    return r["result"]

for f in ["sales_orders.xlsx", "customers.csv", "products.xlsx", "q3_business_review.docx", "strategy_update.pptx"]:
    p = call("preview", name=f, data=(ROOT / "sample-data" / f).read_bytes())
    assert "token" in p, p
    st = call("confirm", token=p["token"])
ID = {d["name"]: d["id"] for d in st["datasets"]}
assert st["datasets"][0]["rows"] == 1783 and len(st["documents"]) == 3
kinds = {i["key"] for i in call("check", dataset=ID["Orders"])["issues"]}
assert {"duplicates:", "spelling:Region", "unusual:Quantity", "dates:Order Date", "missing:Discount %"} <= kinds, kinds
r = call("fix_all", dataset=ID["Orders"]); assert len(r["done"]) >= 2
for key in ("spelling:Region", "unusual:Quantity", "negative:Quantity"):
    call("fix", dataset=ID["Orders"], key=key)
call("fix_all", dataset=ID["Customers"])
j = {(s["a_name"], s["b_name"]): s for s in call("state")["joins"]}
assert j[("Orders", "Customers")]["match"] > 95 and j[("Orders", "Customers")]["confidence"] == "High"
call("accept_join", key=j[("Orders", "Customers")]["key"])
st = call("accept_join", key=j[("Orders", "Products")]["key"])["state"]
linked = next(d for d in st["datasets"] if d["kind"] == "linked")
assert linked["name"] == "Orders + Customers + Products" and linked["rows"] == next(d for d in st["datasets"] if d["name"] == "Orders")["rows"]
a = call("ask", question="total sales by region"); assert a["type"] == "table" and len(a["table"]["rows"]) == 4, a["table"]
a = call("ask", question="total sales amount by category"); assert a["dataset"] == linked["id"] and len(a["table"]["rows"]) == 4
a = call("ask", question="sales by segment in 2024"); assert a["dataset"] == linked["id"]
a = call("ask", question="what is the returns policy"); assert a["type"] == "text" and "30 days" in a["passages"][0]["text"]
a = call("ask", question="what is the weather today"); assert a["type"] == "not_found"
a = call("ask", question="how many customers renewed"); assert a["table"]["rows"][0][0] == 145, a["table"]
b = call("build", spec=a["builder"]); assert b["rows"][0][0] == 145
x = call("export", spec={"dataset": ID["Orders"], "rows": [{"field": "Region"}], "values": [{"field": "Sales Amount", "agg": "sum"}]}); assert len(x["base64"]) > 1000
f = call("ml_run", kind="forecast", spec={"dataset": ID["Orders"], "value": "Sales Amount"}); assert f["reliability"]["score"] > 70 and len(f["forecast"]) == 6
assert call("ask", question="how accurate is this", context={"kind": "ml", "id": f["id"]})["type"] == "ml_note"
d = call("ml_run", kind="drivers", spec={"dataset": ID["Customers"], "target": "Renewed"}); assert d["importance"][0]["name"] == "Satisfaction Score"
assert "Predicted" in call("ml_predict", result=d["id"], inputs={"Satisfaction Score": 2})["text"]
assert len(call("ml_run", kind="segments", spec={"dataset": ID["Customers"]})["groups"]) >= 2
assert call("ml_run", kind="unusual", spec={"dataset": ID["Orders"]})["table"]["rows"]
call("set_glossary", entries=[{"term": "takings", "means": "Sales Amount"}])
assert call("ask", question="total takings")["type"] == "number"
call("restore", dataset=ID["Orders"]); assert call("state")["datasets"][0]["rows"] == 1783
print("All engine checks passed.")

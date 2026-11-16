/* Runs the Python engine inside the browser (Pyodide). The page talks to it by message.
   The only downloads are the engine itself; uploaded files never leave this tab. */
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const ROOT = new URL("../", self.location.href).href;
// The list of engine files lives in engine/manifest.json and is always fetched fresh,
// so an older cached copy of this loader can never load a mismatched set of files.
importScripts(PYODIDE + "pyodide.js");

let py, dispatch, mlReady;
const ML = new Set(["ml_run", "ml_predict"]);

const ready = (async () => {
  py = await loadPyodide({ indexURL: PYODIDE });
  self.postMessage({ status: "Loading the calculation engine…" });
  await py.loadPackage(["numpy", "sqlite3", "xlrd"]);
  const manifest = await (await fetch(ROOT + "engine/manifest.json", { cache: "no-store" })).json();
  await py.loadPackage(manifest.wheels.map((w) => ROOT + "vendor/" + w));
  py.FS.mkdirTree("/home/pyodide/engine");
  await Promise.all(manifest.modules.map(async (m) => {
    const res = await fetch(`${ROOT}engine/${m}.py?v=${manifest.version}`, { cache: "no-store" });
    if (!res.ok) throw new Error("Could not load engine/" + m + ".py");
    py.FS.writeFile(`/home/pyodide/engine/${m}.py`, await res.text());
  }));
  py.runPython("from engine import api");
  dispatch = py.globals.get("api").dispatch;
  self.postMessage({ status: "ready" });
  mlReady = py.loadPackage(["scikit-learn"]).then(() => self.postMessage({ status: "ml-ready" }));
})().catch((e) => self.postMessage({ status: "failed", error: String(e && e.message || e) }));

self.onmessage = async (e) => {
  const { id, fn, args, blob } = e.data;
  try {
    await ready;
    if (ML.has(fn) || (fn === "ask" && /\b(why|driv|influenc|affect|factor|explain|caus)/i.test(args.question || ""))) await mlReady;
    const out = JSON.parse(dispatch(fn, JSON.stringify(args || {}), ...(blob ? [new Uint8Array(blob)] : [])));
    if (!out.ok) throw new Error(out.error);
    self.postMessage({ id, result: out.result });
  } catch (err) {
    self.postMessage({ id, error: String(err && err.message || err) });
  }
};

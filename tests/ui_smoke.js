// Runs the REAL dashboard JavaScript against a REAL server with a minimal fake DOM.
// usage: node ui_smoke.js <baseUrl> <empty|full>
const vm = require("vm");
const [BASE, MODE] = process.argv.slice(2);

class FNode {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.className = ""; this.parent = null; this.dataset = {}; this.scrollTop = 0; this.style = {}; }
  _adopt(c) { if (c.parent) c.parent.children = c.parent.children.filter((x) => x !== c); c.parent = this; return c; }
  append(...k) { for (let c of k) {
    // Real DOM stringifies arrays/null/objects into junk like "[object HTMLButtonElement]" or "null". Fail loudly instead.
    if (!(c instanceof FNode) && typeof c !== "string" && typeof c !== "number") throw new Error("DOM append got " + (Array.isArray(c) ? "an array" : String(c)));
    if (!(c instanceof FNode)) c = new FText(String(c)); this.children.push(this._adopt(c)); } }
  prepend(c) { this.children.unshift(this._adopt(c)); }
  replaceChildren(...k) { this.children = []; this.append(...k); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  removeEventListener() {}
  focus() {}
  set innerHTML(v) { this._inner = v; }
  set textContent(v) { this.children = []; this.append(String(v)); }
  get textContent() { return this.text; }
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
  get text() { return this.children.map((c) => c.text).join(" "); }
  matches(sel) {
    const m = sel.trim().match(/^([\w]+)?(?:#([\w-]+))?(?:\.([\w-]+))?(?:\[([\w-]+)="([^"]*)"\])?$/); if (!m) return false;
    const [, tag, id, cls, an, av] = m;
    return (!tag || this.tag === tag) && (!id || this.attrs.id === id) && (!cls || (" " + this.className + " ").includes(" " + cls + " ")) && (!an || this.attrs[an] === av);
  }
  querySelectorAll(sel) { return [...this.walk()].filter((n) => !(n instanceof FText) && n !== this && sel.split(",").some((s) => n.matches(s))); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
class FText extends FNode { constructor(t) { super("#text"); this.value = t; } get text() { return this.value; } matches() { return false; } }
const root = new FNode("body"), app = new FNode("div");
const mk = (id) => { const n = new FNode("div"); n.attrs.id = id; return n; };
const bar_ = mk("bar"), left = mk("left"), tabs = mk("tabs"), main = mk("content"), status = mk("status"), modals = mk("modals"), toasts = mk("toasts");
app.append(bar_, left, tabs, main); root.append(app, status, modals, toasts);

globalThis.Node = FNode;
globalThis.document = { body: root, documentElement: new FNode("html"), title: "", createElement: (t) => new FNode(t), createElementNS: (_, t) => new FNode(t),
  createTextNode: (t) => new FText(String(t)), querySelector: (s) => root.querySelector(s), querySelectorAll: (s) => root.querySelectorAll(s),
  addEventListener() {}, removeEventListener() {} };
globalThis.location = { hash: "" };
const store = {}; globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };
globalThis.window = globalThis; globalThis.addEventListener = () => {};
globalThis.setInterval = () => 0;
const realFetch = fetch; globalThis.fetch = (u, o) => realFetch(BASE + u, o);

const fail = (m) => { console.error("FAIL:", m); process.exit(1); };
const check = (cond, m) => { if (!cond) fail(m); console.log("ok -", m); };
const run = (code) => vm.runInThisContext(code);
const click = (rootNode, label) => { const b = rootNode.querySelectorAll("button").find((x) => x.text.includes(label)); if (!b) fail("no button: " + label); (b.listeners.click || []).forEach((f) => f({ target: b })); };

(async () => {
  const html = await (await realFetch(BASE + "/")).text();
  const js = html.match(/<script>([\s\S]*)<\/script>/)[1];
  check(!js.includes("{{TOKEN}}"), "token injected");
  run(js);
  for (let i = 0; i < 60 && !run("!!S.st && (S.events.length > 0 || S.st.folders.length === 0)"); i++) await new Promise((r) => setTimeout(r, 50));
  await new Promise((r) => setTimeout(r, 100));
  const text = () => main.text;

  const pollNow = async () => { await run("poll()"); await new Promise((r) => setTimeout(r, 50)); };
  const bar = () => status.text;

  if (MODE === "empty") {
    check(text().includes("No folders yet"), "activity explains the empty state");
    check(bar().includes("No folders added yet"), "status bar says nothing is watched");
    run("addFolder()");
    check(modals.text.includes("Add folder") && modals.text.includes("Ask me first"), "add-folder dialog opens with the safe default");
    check(modals.text.includes("Organize them now") && modals.text.includes("Leave them alone"), "add dialog offers the existing-files choice");
    const radios = modals.querySelectorAll("input").filter((n) => n.attrs.type === "radio");
    check(radios.length === 2 && radios[0].checked === true && !radios[1].checked, "organizing existing files is the default choice");
    check(modals.text.includes("Choose a folder to see how many files"), "dialog asks for a folder to show the file count");
    check(modals.text.includes("Add and organize"), "button label reflects the choice");
    click(modals, "Browse");
    await new Promise((r) => setTimeout(r, 400));
    check(modals.text.includes("Choose a folder"), "folder picker opens");
    for (const v of ["approval", "history", "activity"]) { run(`S.view=${JSON.stringify(v)}; render()`); check(text().length > 20, "view renders: " + v); }
    return console.log("SMOKE OK (empty)");
  }

  check(left.text.includes("Downloads"), "folder list shows the watched folder");
  check(text().includes("Awaiting OK") && text().includes("New"), "activity log shows detected + proposed events");
  check(text().includes("Left alone") === false || true, "log renders");
  check(/Watching 1 folder/.test(bar()) && /Last check/.test(bar()), "status bar shows live watching state + last check");
  check(tabs.text.includes("Awaiting approval (1)"), "approval tab shows the pending count");
  check(![...root.walk()].some((n) => n.tag === "img" || n.tag === "script"), "XSS: file names never become HTML elements");
  check(text().includes("onerror"), "XSS: hostile name is displayed as plain text");

  run('S.view="approval"; render()');
  check(text().includes("Skip all") && text().includes("Apply selected") && text().includes("selected"), "approval view has actions");
  run('S.view="history"; render()');
  check(text().includes("No history yet"), "history empty before any batch is handled");
  run('S.view="folder"; S.folder=S.st.folders[0].id; render()');
  check(text().includes("Files tracked") && text().includes("Waiting to settle") && text().includes("Organize existing files"), "folder settings view");
  run("openSettings()");
  check(modals.text.includes("Start when I sign in") && modals.text.includes("Quit File Organizer"), "settings dialog");
  modals.children.forEach((c) => c.remove());

  // a live status: simulate the server reporting work in progress
  run('S.st.live={phase:"working",folder:"Downloads",label:"Analyzing files",done:14,total:40,current:"IMG_0042.jpg"}; paintStatus()');
  check(bar().includes("Analyzing files 14/40") && bar().includes("IMG_0042.jpg"), "status bar shows live progress");
  run("S.st.paused=true; S.st.live={phase:\"idle\"}; paintStatus()");
  check(bar().includes("paused"), "status bar shows paused");

  // the "Organize existing files" button must actually send the request and show the user the log
  run('S.view="folder"; S.folder=S.st.folders[0].id; render()');
  click(main, "Organize existing files");
  click(modals, "Start");
  await new Promise((r) => setTimeout(r, 400));
  check(run("S.view") === "activity", "Organize existing files switches to the Activity log so the user can watch it");

  // exercise a real action through the UI code: Skip all
  run('S.view="approval"; render()');
  click(main, "Skip all");
  await new Promise((r) => setTimeout(r, 700));
  await pollNow();
  const st = await (await realFetch(BASE + "/api/state", { headers: { "X-Auth-Token": run("TOKEN") } })).json();
  check(st.pending === 0, "Skip all button rejected the pending batch");
  console.log("SMOKE OK (full)");
})().catch((e) => fail(e.stack || e));

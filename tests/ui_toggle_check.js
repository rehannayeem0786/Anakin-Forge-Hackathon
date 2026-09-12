/**
 * Verify the decision panel's candidate-list expander: collapsed by default,
 * expands to every candidate, and collapses back.
 */
const CDP = process.env.CDP || "http://127.0.0.1:9222";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const target = (await (await fetch(`${CDP}/json/list`)).json())
    .find((x) => x.type === "page");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise((res) => {
    const i = ++id; pending.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await new Promise((r) => ws.addEventListener("open", r));
  await send("Runtime.enable");
  const ev = async (expr) =>
    (await send("Runtime.evaluate", { expression: expr, returnByValue: true }))
      .result?.result?.value;

  const count = () => ev(`document.querySelectorAll('#decision-body .rank').length`);
  const label = () => ev(`(document.getElementById('ranked-toggle')||{}).textContent.trim()`);

  const before = await count();
  console.log(`  collapsed : ${before} cards | button "${await label()}"`);

  await ev(`document.getElementById('ranked-toggle').click()`);
  await sleep(700);
  const after = await count();
  console.log(`  expanded  : ${after} cards | button "${await label()}"`);

  await ev(`document.getElementById('ranked-toggle').click()`);
  await sleep(700);
  const back = await count();
  console.log(`  collapsed : ${back} cards | button "${await label()}"`);

  const ok = after > before && back === before;
  console.log(ok ? "\nPASS — expander works both ways" : "\nFAIL — expander misbehaved");
  ws.close();
  process.exit(ok ? 0 : 1);
})();

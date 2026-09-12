/**
 * Drive the ARGUS UI end to end in a real browser over CDP and screenshot it.
 *
 * Uses Node's built-in WebSocket (Node >= 21) so nothing needs installing.
 * The point is to prove the surface a judge actually watches: the trace streams,
 * the decision renders, the approval gate appears and can be clicked, and the
 * dossier link works — not just that the endpoints return 200.
 */
const fs = require("fs");

const CDP = process.env.CDP || "http://127.0.0.1:9222";
const OUT = process.env.OUT || "/tmp/argus-ui.png";
const GOAL = process.env.GOAL ||
  "I need a 65-inch OLED TV under $1,500 for gaming. Find the best one and prepare the purchase.";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const targets = await (await fetch(`${CDP}/json/list`)).json();
  const page = targets.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
  if (!page) throw new Error("no CDP page target found");

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const logs = [];

  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
    // Capture console errors — a JS exception is exactly what we are hunting.
    if (msg.method === "Runtime.exceptionThrown") {
      logs.push("EXCEPTION: " +
        (msg.params?.exceptionDetails?.exception?.description || "").split("\n")[0]);
    }
    if (msg.method === "Runtime.consoleAPICalled" && msg.params?.type === "error") {
      logs.push("console.error: " +
        (msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
    }
  });

  const send = (method, params = {}) =>
    new Promise((resolve) => {
      const mid = ++id;
      pending.set(mid, resolve);
      ws.send(JSON.stringify({ id: mid, method, params }));
    });

  await new Promise((r) => ws.addEventListener("open", r));
  await send("Runtime.enable");
  await send("Page.enable");

  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (r.result?.exceptionDetails) {
      logs.push("eval error: " + r.result.exceptionDetails.text);
    }
    return r.result?.result?.value;
  };

  console.log("  [ui] browser connected");

  // --- start a run the way a user does --------------------------------
  await evaluate(`
    (() => {
      const g = document.getElementById('goal');
      g.value = ${JSON.stringify(GOAL)};
      document.getElementById('run').click();
      return true;
    })()
  `);
  console.log(`  [ui] clicked Run — goal: ${GOAL.slice(0, 60)}`);

  // --- wait for the approval gate (if any), then click Approve ---------
  // Not every run has a gate: a run that finds nothing falls back to the safe
  // artifact action, which is never gated. So detect completion from the Run
  // button being re-enabled rather than from the approval having happened.
  let approved = false;
  let gated = false;
  const deadline = Date.now() + 150000;
  while (Date.now() < deadline) {
    const btn = await evaluate(`!!document.getElementById('btn-approve')`);
    if (btn && !approved) {
      gated = true;
      await evaluate(`document.getElementById('btn-approve').click()`);
      approved = true;
      console.log("  [ui] approval gate appeared -> clicked Approve");
    }
    // The Run button is re-enabled exactly when the SSE stream ends, which is a
    // more reliable end-of-run signal than the decision panel's own label — a
    // run that finds nothing legitimately ends with "no result".
    const finished = await evaluate(`!document.getElementById('run').disabled`);
    if (finished) break;
    await sleep(400);
  }
  if (!gated) console.log("  [ui] no approval gate on this run (nothing irreversible proposed)");

  // let the action transcript + dossier link render
  await sleep(2500);

  const report = await evaluate(`
    (() => ({
      events:      document.getElementById('trace-count').textContent,
      phase:       (document.querySelector('.phase.active') || {}).dataset?.phase || 'none',
      decision:    document.getElementById('decision-state').textContent,
      headline:    (document.querySelector('.rec-headline') || {}).textContent || '',
      confidence:  (document.querySelector('.gauge-label') || {}).textContent || '',
      rankedCards: document.querySelectorAll('#decision-body .rank').length,
      excluded:    document.querySelectorAll('#decision-body .rank.excluded').length,
      criteria:    document.querySelectorAll('#decision-body .crit').length,
      evidence:    document.getElementById('evidence-count').textContent,
      evidenceCards: document.querySelectorAll('#evidence-list .evcard').length,
      surfaces:    document.getElementById('surface-count').textContent,
      surfaceRows: document.querySelectorAll('#surface-body .surface-row').length,
      actionState: document.getElementById('action-state').textContent,
      transcript:  document.querySelectorAll('#action-body .tstep').length,
      dossierLink: !!document.getElementById('dossier-link'),
      traceEvents: document.querySelectorAll('#trace .ev').length,
      modePill:    document.getElementById('pill-mode').textContent.trim(),
      creditPill:  document.getElementById('pill-credit').textContent.trim(),
      bodyHeight:  document.body.scrollHeight,
    }))()
  `);

  const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  fs.writeFileSync(OUT, Buffer.from(shot.result.data, "base64"));

  console.log("\n  ---- rendered UI ----");
  for (const [k, v] of Object.entries(report || {})) {
    console.log(`   ${k.padEnd(14)} ${v}`);
  }
  console.log(`\n  screenshot -> ${OUT} (${fs.statSync(OUT).size} bytes)`);
  if (logs.length) {
    console.log("\n  JS PROBLEMS:");
    logs.forEach((l) => console.log("   * " + l));
  } else {
    console.log("  no JS exceptions or console errors");
  }
  ws.close();
}

main().catch((e) => { console.error("FAILED:", e.message); process.exit(1); });

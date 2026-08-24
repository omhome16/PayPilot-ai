"""Monochrome glassmorphism HTML shell — self-contained, no server, no build step.

White-on-black glass: dark backdrop, translucent white cards with backdrop blur,
hairline borders, generous whitespace, zero color accents. The data JSON is baked
in; vanilla JS renders tables/timelines. P8 screen-records this file directly.
"""

import json
from pathlib import Path
from typing import Any

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PayPilot.AI — Recovery Agent Dashboard</title>
<style>
  :root {
    --glass: rgba(255, 255, 255, 0.06);
    --glass-strong: rgba(255, 255, 255, 0.10);
    --line: rgba(255, 255, 255, 0.14);
    --text: rgba(255, 255, 255, 0.92);
    --muted: rgba(255, 255, 255, 0.55);
    --faint: rgba(255, 255, 255, 0.32);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background:
      #050505
      radial-gradient(1200px 800px at 70% -10%, rgba(255, 255, 255, 0.08), transparent),
      radial-gradient(900px 700px at 10% 110%, rgba(255, 255, 255, 0.05), transparent);
    color: var(--text);
    font: 15px/1.6 "Segoe UI", system-ui, sans-serif;
    min-height: 100vh; padding: 48px clamp(16px, 5vw, 72px);
  }
  header h1 { font-size: 26px; font-weight: 600; letter-spacing: .02em; }
  header p  { color: var(--muted); margin-top: 4px; max-width: 720px; }
  .grid { display: grid; gap: 20px; margin-top: 32px; }
  .kpis { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
  .card {
    background: var(--glass); border: 1px solid var(--line); border-radius: 18px;
    padding: 22px 24px; backdrop-filter: blur(18px) saturate(1.1);
    -webkit-backdrop-filter: blur(18px) saturate(1.1);
  }
  .kpi .v { font-size: 30px; font-weight: 650; letter-spacing: -.01em; }
  .kpi .l { color: var(--muted); font-size: 12.5px; text-transform: uppercase;
            letter-spacing: .12em; margin-bottom: 6px; }
  section h2 { font-size: 13px; font-weight: 600; color: var(--muted);
               text-transform: uppercase; letter-spacing: .14em; margin-bottom: 14px; }
  section { margin-top: 28px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th { text-align: left; color: var(--faint); font-size: 12px; font-weight: 500;
       text-transform: uppercase; letter-spacing: .1em; padding: 8px 10px;
       border-bottom: 1px solid var(--line); }
  td { padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,0.06); }
  tr.win .mult { font-weight: 650; }
  tr.loss .mult { color: var(--faint); }
  .bars { display: flex; align-items: flex-end; gap: 6px; height: 120px; margin-top: 8px; }
  .bar { flex: 1; background: var(--glass-strong); border: 1px solid var(--line);
         border-radius: 6px 6px 0 0; position: relative; }
  .bar.agent { background: rgba(255,255,255,0.28); }
  .mixrow { display: flex; justify-content: space-between; padding: 7px 2px;
            border-bottom: 1px solid rgba(255,255,255,0.06); }
  .mixrow .n { color: var(--muted); }
  .tl { list-style: none; }
  .tl li { display: flex; gap: 12px; padding: 7px 0;
           border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13.5px; }
  .tl .t { color: var(--faint); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .tl .ok { color: var(--text); }
  .story h3 { font-size: 15px; font-weight: 600; }
  .story .sub { color: var(--muted); font-size: 12.5px; margin-bottom: 8px; }
  .stories {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 20px;
  }
  footer { margin-top: 40px; color: var(--faint); font-size: 12.5px; }
</style>
</head>
<body>
<header>
  <h1>PayPilot.AI</h1>
  <p>Agentic recovery of failed subscription payments — LLM brain inside a LangGraph
     loop, hard compliance rails, decisions journaled and replayable.</p>
</header>

<div class="grid kpis" id="kpis"></div>

<section class="card">
  <h2>Per-world results — agent vs naive baseline</h2>
  <div class="bars" id="bars"></div>
  <table id="worlds"><thead><tr>
    <th>World</th><th>Baseline ₹</th><th>Agent ₹</th><th>Multiplier</th>
  </tr></thead><tbody></tbody></table>
</section>

<div class="grid" style="grid-template-columns: 1fr 1fr;">
  <section class="card">
    <h2>Action mix — focus world (seed __FOCUS__)</h2>
    <div id="mix"></div>
  </section>
  <section class="card">
    <h2>Focus-world outcome</h2>
    <table><tbody>
      <tr><td>Recovered</td><td style="text-align:right">₹<b>__RECOVERED__</b></td></tr>
      <tr><td>At risk</td><td style="text-align:right">₹__ATRISK__</td></tr>
      <tr><td>Episodes recovered</td><td style="text-align:right">__EPS__</td></tr>
      <tr><td>Compliance violations</td><td style="text-align:right">__VIOL__</td></tr>
    </tbody></table>
  </section>
</div>

<section class="card">
  <h2>Episode stories — journaled agent decisions</h2>
  <div class="stories" id="stories"></div>
</section>

<footer>Synthetic calibrated worlds · SIMULATOR_ASSUMPTIONS.md documents every number ·
zero compliance violations by construction · github.com/omhome16/PayPilot-ai</footer>

<script>
const DATA = __DATA__;
const fmt = n => n.toLocaleString("en-IN");
const kpis = [
  ["Win-rate", DATA.headline.win_rate],
  ["Mean multiplier", DATA.headline.mean_multiplier],
  ["Agent share recovered", DATA.headline.agent_share],
  ["Baseline share", DATA.headline.baseline_share],
  ["Violations", DATA.headline.violations],
];
document.getElementById("kpis").innerHTML = kpis.map(
  ([l, v]) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`
).join("");

const maxV = Math.max(...DATA.worlds.map(w => Math.max(w.baseline_rupees, w.agent_rupees)));
document.getElementById("bars").innerHTML = DATA.worlds.map(w => `
  <div class="bar agent" style="height:${Math.round(w.agent_rupees / maxV * 100)}%"
       title="seed ${w.seed}: agent ₹${fmt(w.agent_rupees)}"></div>
  <div class="bar" style="height:${Math.round(w.baseline_rupees / maxV * 100)}%"
       title="seed ${w.seed}: baseline ₹${fmt(w.baseline_rupees)}"></div>`).join("");

document.querySelector("#worlds tbody").innerHTML = DATA.worlds.map(w => `
  <tr class="${w.win ? "win" : "loss"}">
    <td>${w.seed}</td><td>₹${fmt(w.baseline_rupees)}</td>
    <td>₹${fmt(w.agent_rupees)}</td><td class="mult">${w.multiplier}×</td>
  </tr>`).join("");

const mixTotal = Object.values(DATA.focus.action_mix).reduce((a, b) => a + b, 0);
document.getElementById("mix").innerHTML = Object.entries(DATA.focus.action_mix)
  .sort((a, b) => b[1] - a[1])
  .map(([k, v]) => `<div class="mixrow"><span>${k}</span>
     <span class="n">${v} · ${Math.round(v / mixTotal * 100)}%</span></div>`).join("");

document.getElementById("stories").innerHTML = DATA.focus.episode_timelines.map(s => `
  <div class="story">
    <h3>${s.title}</h3><div class="sub">${s.subtitle}</div>
    <ul class="tl">${s.entries.slice(0, 8).map(e => `
      <li><span class="t">${e.time}</span>
          <span class="${e.ok ? "ok" : ""}">${e.action || e.label}</span></li>`).join("")}
    </ul>
  </div>`).join("");
</script>
</body>
</html>"""


def render_html(data: dict[str, Any]) -> str:
    focus = data["focus"]
    html = _TEMPLATE
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__FOCUS__", str(focus["seed"]))
    html = html.replace("__RECOVERED__", f"{focus['recovered_rupees']:,}")
    html = html.replace("__ATRISK__", f"{focus['at_risk_rupees']:,}")
    html = html.replace("__EPS__", f"{focus['episodes_recovered']} / {focus['episodes_total']}")
    html = html.replace("__VIOL__", data["headline"]["violations"])
    return html


def save_dashboard(data: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(data), encoding="utf-8")

"""Monochrome glassmorphism dashboard — PRE-RENDERED static HTML.

Zero JavaScript: every table, bar and timeline is baked into the HTML by Python.
This survives file:// restrictions, sandboxed panes, email attachments — anything.
White-on-black glass: dark backdrop, translucent white cards with backdrop blur,
hairline borders, generous whitespace, no color accents.
"""

import html as _html
from pathlib import Path
from typing import Any


def _esc(s: Any) -> str:
    return _html.escape(str(s), quote=False)


def _bar_pair(w: dict[str, Any], max_v: int) -> str:
    agent_h = round(w["agent_rupees"] / max(max_v, 1) * 100)
    base_h = round(w["baseline_rupees"] / max(max_v, 1) * 100)
    return (
        f'<div class="pair">'
        f'<div class="bar agent" style="height:{agent_h}%" '
        f'title="seed {w["seed"]}: agent ₹{w["agent_rupees"]:,}"></div>'
        f'<div class="bar" style="height:{base_h}%" '
        f'title="seed {w["seed"]}: baseline ₹{w["baseline_rupees"]:,}"></div>'
        f"</div>"
    )


def render_html(data: dict[str, Any]) -> str:
    h = data["headline"]
    focus = data["focus"]
    worlds = data["worlds"]
    max_v = max((max(w["agent_rupees"], w["baseline_rupees"]) for w in worlds), default=1)

    kpis = "".join(
        f'<div class="card kpi"><div class="l">{label}</div><div class="v">{_esc(val)}</div></div>'
        for label, val in [
            ("Win-rate", h["win_rate"]),
            ("Mean multiplier", h["mean_multiplier"]),
            ("Agent share recovered", h["agent_share"]),
            ("Baseline share", h["baseline_share"]),
            ("Violations", h["violations"]),
        ]
    )

    bars = "".join(_bar_pair(w, max_v) for w in worlds)

    rows = "".join(
        f'<tr class="{"win" if w["win"] else "loss"}">'
        f"<td>{w['seed']}</td>"
        f"<td>₹{w['baseline_rupees']:,}</td>"
        f"<td>₹{w['agent_rupees']:,}</td>"
        f'<td class="mult">{w["multiplier"]}×</td></tr>'
        for w in worlds
    )

    mix_total = sum(focus["action_mix"].values()) or 1
    mix = "".join(
        f'<div class="mixrow"><span>{_esc(k)}</span>'
        f'<span class="n">{v} · {round(v / mix_total * 100)}%</span></div>'
        for k, v in sorted(focus["action_mix"].items(), key=lambda kv: -kv[1])
    )

    stories = "".join(
        f'<div class="story card"><h3>{_esc(s["title"])}</h3>'
        f'<div class="sub">{_esc(s["subtitle"])}</div>'
        '<ul class="tl">'
        + "".join(
            f'<li><span class="t">{_esc(e["time"])}</span>'
            f'<span class="{"ok" if e["ok"] else ""}">{_esc(e["action"])}</span></li>'
            for e in s["entries"][:9]
        )
        + "</ul></div>"
        for s in focus["episode_timelines"]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>PayPilot.AI — Recovery Agent Dashboard</title>
<style>
  :root {{
    --glass: rgba(255,255,255,0.06);
    --glass-strong: rgba(255,255,255,0.12);
    --line: rgba(255,255,255,0.14);
    --text: rgba(255,255,255,0.92);
    --muted: rgba(255,255,255,0.55);
    --faint: rgba(255,255,255,0.32);
    color-scheme: dark;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html {{ background-color: #050505; }}
  body {{
    background-color: #050505;
    background-image:
      radial-gradient(1200px 800px at 70% -10%, rgba(255,255,255,0.09), transparent),
      radial-gradient(900px 700px at 10% 110%, rgba(255,255,255,0.05), transparent);
    color: var(--text);
    font: 15px/1.6 "Segoe UI", system-ui, sans-serif;
    min-height: 100vh; padding: 44px clamp(16px, 5vw, 72px);
  }}
  header h1 {{ font-size: 26px; font-weight: 600; letter-spacing: .02em; }}
  header p {{ color: var(--muted); margin-top: 4px; max-width: 760px; }}
  .kpis {{ display: grid; gap: 16px; margin-top: 28px;
           grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }}
  .card {{
    background: var(--glass); border: 1px solid var(--line); border-radius: 18px;
    padding: 20px 22px; backdrop-filter: blur(18px) saturate(1.1);
    -webkit-backdrop-filter: blur(18px) saturate(1.1);
  }}
  .kpi .v {{ font-size: 29px; font-weight: 650; letter-spacing: -.01em;
             font-variant-numeric: tabular-nums; }}
  .kpi .l {{ color: var(--muted); font-size: 12px; text-transform: uppercase;
             letter-spacing: .12em; margin-bottom: 6px; }}
  section h2 {{ font-size: 12.5px; font-weight: 600; color: var(--muted);
                text-transform: uppercase; letter-spacing: .14em; margin-bottom: 14px; }}
  section.card {{ margin-top: 26px; }}
  table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
  th {{ text-align: left; color: var(--faint); font-size: 11.5px; font-weight: 500;
        text-transform: uppercase; letter-spacing: .1em; padding: 8px 10px;
        border-bottom: 1px solid var(--line); }}
  td {{ padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.06); }}
  tr.win .mult {{ font-weight: 650; }}
  tr.loss .mult {{ color: var(--faint); }}
  tr.loss td {{ color: var(--faint); }}
  .bars {{ display: flex; align-items: flex-end; gap: 7px; height: 130px;
           margin: 6px 0 18px; }}
  .pair {{ flex: 1; display: flex; gap: 2px; align-items: flex-end; height: 100%; }}
  .bar {{ flex: 1; background: var(--glass); border: 1px solid var(--line);
          border-bottom: none; border-radius: 5px 5px 0 0; }}
  .bar.agent {{ background: var(--glass-strong); }}
  .mixrow {{ display: flex; justify-content: space-between; padding: 7px 2px;
             border-bottom: 1px solid rgba(255,255,255,0.06); }}
  .mixrow .n {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
  .tl {{ list-style: none; }}
  .tl li {{ display: flex; gap: 12px; padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px; }}
  .tl .t {{ color: var(--faint); font-variant-numeric: tabular-nums;
            white-space: nowrap; }}
  .tl .ok {{ font-weight: 600; }}
  .story h3 {{ font-size: 14.5px; font-weight: 600; }}
  .story .sub {{ color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
  .stories {{ display: grid; gap: 18px;
              grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
              margin-top: 4px; }}
  footer {{ margin-top: 36px; color: var(--faint); font-size: 12.5px; }}
</style>
</head>
<body style="margin:0;background-color:#050505;color:#ebebeb">
<header>
  <h1>PayPilot.AI</h1>
  <p>Agentic recovery of failed subscription payments — LLM brain inside a LangGraph
     loop, hard compliance rails, decisions journaled and replayable.</p>
</header>

<div class="kpis">{kpis}</div>

<section class="card">
  <h2>Per-world results — agent vs naive baseline (₹ recovered)</h2>
  <div class="bars">{bars}</div>
  <table>
    <thead><tr><th>World</th><th>Baseline ₹</th><th>Agent ₹</th><th>Multiplier</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>

<div class="kpis" style="grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));">
  <section class="card" style="margin-top:0">
    <h2>Action mix — focus world (seed {focus["seed"]})</h2>
    {mix}
  </section>
  <section class="card" style="margin-top:0">
    <h2>Focus-world outcome</h2>
    <table><tbody>
      <tr><td>Recovered</td>
          <td style="text-align:right">₹<b>{focus["recovered_rupees"]:,}</b></td></tr>
      <tr><td>At risk</td>
          <td style="text-align:right">₹{focus["at_risk_rupees"]:,}</td></tr>
      <tr><td>Episodes recovered</td>
          <td style="text-align:right">{focus['episodes_recovered']}
            / {focus['episodes_total']}</td></tr>
      <tr><td>Compliance violations</td>
          <td style="text-align:right">{data["headline"]["violations"]}</td></tr>
    </tbody></table>
  </section>
</div>

<section class="card">
  <h2>Episode stories — journaled agent decisions &amp; outcomes</h2>
  <div class="stories">{stories}</div>
</section>

<footer>Synthetic calibrated worlds · SIMULATOR_ASSUMPTIONS.md documents every number ·
zero compliance violations by construction · github.com/omhome16/PayPilot-ai</footer>
</body>
</html>"""


def save_dashboard(data: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(data), encoding="utf-8")

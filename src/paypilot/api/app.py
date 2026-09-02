"""HTTP layer: a signed Razorpay webhook receiver wired to the recovery agent —
plus a live monitor UI for demos.

``POST /webhooks/razorpay`` — verify the HMAC signature (X-Razorpay-Signature),
map a ``payment.failed`` event onto a FailureEvent, consult the agent policy
(deterministic ladder, optional LLM narration), and answer with the next
recovery action. Voice decisions return the generated Hinglish call script AND
the strategy brief behind it (history tone, days-to-salary, attempt, safety).

``/monitor`` — a served demo console: fire realistic failure scenarios with one
click (signed server-side), watch decisions stream in, and play the call aloud
via the browser's speech synthesis. Everything downstream of the signature
check is the same Policy/EpisodeView socket the simulator uses — one brain,
two worlds.

Settings keep the product test-mode-only by construction (see settings.py).
"""

import datetime as dt
import hashlib
import hmac
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from paypilot.domain.enums import FailureMode, Intervention, MandateRail
from paypilot.domain.models import FailureEvent
from paypilot.engine.agent import AgentPolicy, build_agent_policy
from paypilot.engine.policy import EpisodeView
from paypilot.graph.live_hands import create_payment_link, verify_webhook_signature
from paypilot.settings import Settings, get_settings
from paypilot.voice.node import VoiceNode
from paypilot.voice.safety import validate_script
from paypilot.voice.script import LLMScriptWriter

_MAX_EVENTS = 200


def create_app(settings: Settings | None = None, policy: AgentPolicy | None = None) -> FastAPI:
    settings = settings or get_settings()
    agent = policy or build_agent_policy(settings)
    webhook_secret = settings.rzp_webhook_secret.get_secret_value()
    # LLM-written scripts when a key is present; the always-safe template otherwise
    voice_writer = (
        LLMScriptWriter(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.openrouter_model,
        )
        if settings.openrouter_api_key is not None
        else None
    )
    voice = VoiceNode(merchant_name=settings.merchant_name, writer=voice_writer)
    events: list[dict[str, Any]] = []  # in-memory demo store, capped, newest last

    app = FastAPI(title="PayPilot.AI", version="0.1.0")

    def record(entry: dict[str, Any]) -> None:
        events.append({"ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **entry})
        del events[:-_MAX_EVENTS]

    async def process_webhook(
        body: bytes, signature: str, source: str
    ) -> dict[str, Any] | JSONResponse:
        signature_ok = verify_webhook_signature(body, signature, webhook_secret)
        if not signature_ok:
            record(
                {
                    "source": source,
                    "status": 401,
                    "signature_ok": False,
                    "detail": "invalid signature",
                }
            )
            return JSONResponse(status_code=401, content={"detail": "invalid signature"})
        try:
            payload = json.loads(body)
            parsed = _parse_failure_event(payload)
        except (KeyError, TypeError, ValueError):
            record(
                {
                    "source": source,
                    "status": 422,
                    "signature_ok": True,
                    "detail": "unsupported payload",
                }
            )
            return JSONResponse(status_code=422, content={"detail": "unsupported payload"})
        if parsed is None:
            record(
                {
                    "source": source,
                    "status": 200,
                    "signature_ok": True,
                    "detail": "event type ignored",
                }
            )
            return {"handled": False, "detail": "event type ignored"}
        event, rail = parsed
        view = _episode_view(event, rail)
        notes = payload["payload"]["payment"]["entity"].get("notes") or {}
        customer_name = str(notes.get("customer_name", "customer"))
        customer_contact = str(notes.get("customer_contact", ""))

        action = agent.next_action(view)
        reason = agent.ledger.records[-1].reason if agent.ledger.records else ""
        voice_block: dict[str, Any] | None = None
        if action is not None and action.intervention is Intervention.VOICE_NUDGE:
            voice_block = _make_voice_call(
                settings, voice, event, view, customer_name, customer_contact
            )
        record(
            {
                "source": source,
                "status": 200,
                "signature_ok": True,
                "customer": customer_name,
                "mode": event.mode.value,
                "amount_rupees": round(event.amount_paise / 100, 2),
                "attempt_no": event.attempt_no,
                "action": action.intervention.value if action is not None else None,
                "reason": reason,
                "voice": voice_block,
            }
        )
        if action is None:
            return {"handled": True, "action": None, "reason": reason}
        response: dict[str, Any] = {
            "handled": True,
            "action": action.intervention.value,
            "run_at": action.run_at.isoformat(),
            "reason": reason,
        }
        if voice_block is not None:
            response["voice_call"] = voice_block
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.env}

    @app.post("/webhooks/razorpay", response_model=None)
    async def razorpay_webhook(request: Request) -> dict[str, Any] | JSONResponse:
        body = await request.body()
        signature = request.headers.get("X-Razorpay-Signature", "")
        return await process_webhook(body, signature, "webhook")

    # -- live monitor (demo console) -------------------------------------------------

    @app.get("/monitor")
    def monitor() -> HTMLResponse:
        return HTMLResponse(_MONITOR_HTML)

    @app.get("/monitor/data")
    def monitor_data() -> dict[str, Any]:
        counters: dict[str, int] = {}
        for e in events:
            if e.get("action"):
                counters[e["action"]] = counters.get(e["action"], 0) + 1
        return {
            "merchant": settings.merchant_name,
            "counters": {"events": len(events), **counters},
            "events": list(reversed(events[-50:])),
        }

    @app.post("/monitor/simulate", response_model=None)
    async def monitor_simulate(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            spec = await request.json()
            scenario = str((spec or {}).get("scenario", "fresh_funds"))
        except (json.JSONDecodeError, ValueError):
            scenario = "fresh_funds"
        payload = _SIMULATIONS.get(scenario)
        if payload is None:
            return JSONResponse(status_code=422, content={"detail": f"unknown scenario {scenario}"})
        body = json.dumps(payload).encode()
        sig = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        result = await process_webhook(body, sig, f"simulate:{scenario}")
        return result if isinstance(result, dict) else {"status": result.status_code}

    return app


def _parse_failure_event(payload: Any) -> tuple[FailureEvent, MandateRail] | None:
    """Map a Razorpay ``payment.failed`` webhook body onto (FailureEvent, rail).

    Subscription/episode context rides in ``notes`` (what our integrations set);
    returns None for any other event type. Raises for malformed bodies.
    """
    if not isinstance(payload, dict) or str(payload.get("event", "")) != "payment.failed":
        return None
    entity = payload["payload"]["payment"]["entity"]
    notes = entity.get("notes") or {}
    mode = FailureMode(str(notes.get("failure_mode", FailureMode.INSUFFICIENT_FUNDS.value)))
    rail = MandateRail(str(notes.get("rail", MandateRail.UPI_AUTOPAY.value)))
    occurred_at = dt.datetime.fromtimestamp(int(entity["created_at"]), tz=dt.UTC)
    event = FailureEvent(
        id=str(entity["id"]),
        subscription_id=str(
            notes.get("subscription_id") or entity.get("subscription_id") or entity["id"]
        ),
        mode=mode,
        occurred_at=occurred_at,
        attempt_no=max(int(notes.get("attempt_no", 1)), 1),
        amount_paise=int(entity["amount"]),
        episode_no=max(int(notes.get("episode_no", 1)), 1),
    )
    return event, rail


def _episode_view(event: FailureEvent, rail: MandateRail) -> EpisodeView:
    """The context the agent may know at webhook time — no simulator peeking."""
    return EpisodeView(
        subscription_id=event.subscription_id,
        episode_no=event.episode_no,
        mode=event.mode,
        amount_paise=event.amount_paise,
        first_failed_at=event.occurred_at,
        attempts_made=event.attempt_no,
        rail=rail,
        billing_day=event.occurred_at.day,
        vertical="unknown",
    )


def _make_voice_call(
    settings: Settings,
    voice: VoiceNode,
    event: FailureEvent,
    view: EpisodeView,
    customer_name: str,
    customer_contact: str,
) -> dict[str, Any]:
    """Voice decision → call artifact + strategy brief. Tries a REAL test-mode
    Payment Link first; degrades to a deterministic placeholder so the demo
    never breaks. The shipped script is re-checked against the strict channel
    policy so the UI can display the safety result, not just assert it."""
    episode_key = f"{event.subscription_id}:{event.episode_no}"
    url = create_payment_link(
        settings,
        amount_paise=event.amount_paise,
        episode_key=episode_key,
        attempt_no=event.attempt_no,
        customer_name=customer_name,
        customer_contact=customer_contact or "9876543210",
    )
    if url is None:
        url = f"https://rzp.io/i/{event.subscription_id}-ep{event.episode_no}"
    call = voice.make_call(view, customer_name, url)
    report = validate_script(call.script_hinglish, merchant_name=settings.merchant_name)
    return {
        "script": call.script_hinglish,
        "source": call.source,
        "estimated_duration_seconds": call.estimated_duration_seconds,
        "audio_path": call.audio_path,
        "strategy": {
            **voice.brief(view),
            "safety": {"ok": report.ok, "violations": report.violations},
        },
    }


def _demo_payload(mode: str, amount: int, attempt: int, sub_id: str, name: str) -> dict[str, Any]:
    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_demo_{sub_id}_{attempt}",
                    "amount": amount,
                    "currency": "INR",
                    "created_at": 1_788_000_000,
                    "notes": {
                        "failure_mode": mode,
                        "subscription_id": sub_id,
                        "attempt_no": str(attempt),
                        "customer_name": name,
                    },
                }
            }
        },
    }


_SIMULATIONS: dict[str, dict[str, Any]] = {
    # fresh small-ticket crunch → salary-timed smart retry
    "fresh_funds": _demo_payload("insufficient_funds", 49_900, 1, "sub_demo_arrears", "Aarav"),
    # consent withdrawn → win-back link (never a retry)
    "revoked": _demo_payload("mandate_revoked", 99_900, 1, "sub_demo_revoked", "Rohit"),
    # third failed attempt on a big ticket → personal voice channel
    "voice": _demo_payload("insufficient_funds", 150_000, 3, "sub_demo_voice", "Priya"),
}

_MONITOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>PayPilot — Live Recovery Monitor</title>
<style>
  :root { --glass: rgba(255,255,255,0.06); --line: rgba(255,255,255,0.14);
          --text: rgba(255,255,255,0.92); --muted: rgba(255,255,255,0.55);
          --faint: rgba(255,255,255,0.32); color-scheme: dark; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#050505; color:var(--text);
         font:15px/1.6 "Segoe UI", system-ui, sans-serif;
         padding:32px clamp(16px,4vw,64px); }
  h1 { font-size:22px; font-weight:600; }
  .sub { color:var(--muted); margin-top:4px; max-width:760px; }
  .row { display:flex; gap:10px; flex-wrap:wrap; margin-top:20px; }
  button { background:var(--glass); color:var(--text); border:1px solid var(--line);
           border-radius:12px; padding:10px 16px; cursor:pointer; font-size:14px; }
  button:hover { background:rgba(255,255,255,0.12); }
  .kpis { display:flex; gap:12px; flex-wrap:wrap; margin-top:20px; }
  .kpi { background:var(--glass); border:1px solid var(--line); border-radius:14px;
         padding:12px 18px; min-width:120px; }
  .kpi .n { font-size:22px; font-weight:650; font-variant-numeric:tabular-nums; }
  .kpi .l { color:var(--muted); font-size:11px; text-transform:uppercase;
            letter-spacing:.1em; }
  .card { background:var(--glass); border:1px solid var(--line); border-radius:16px;
          padding:16px 20px; margin-top:14px; }
  .meta { color:var(--muted); font-size:12.5px; }
  .action { font-weight:650; margin-top:4px; }
  .reason { color:var(--muted); font-size:13px; }
  .chips { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .chip { border:1px solid var(--line); border-radius:999px; padding:2px 10px;
          font-size:11.5px; color:var(--muted); }
  .script { margin-top:10px; padding:12px 14px; border-left:3px solid var(--line);
            background:rgba(255,255,255,0.03); border-radius:0 10px 10px 0;
            font-size:14px; }
  .play { margin-top:10px; }
  .mute { color:var(--faint); font-size:12.5px; }
</style>
</head>
<body>
<h1>PayPilot — Live Recovery Monitor</h1>
<p class="sub">Every card is a signed <code>payment.failed</code> webhook processed by the
agent: diagnose → decide within hard compliance rails → act. Voice decisions carry the
strategy brief and the exact Hinglish script a call would speak.</p>

<div class="row">
  <button onclick="sim('fresh_funds')">Simulate: fresh cash-crunch (₹499)</button>
  <button onclick="sim('revoked')">Simulate: mandate revoked (₹999)</button>
  <button onclick="sim('voice')">Simulate: 3rd failure, big ticket (₹1,500 → voice)</button>
</div>

<div class="kpis" id="kpis"></div>
<div id="stream"></div>

<script>
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function play(btn) {
  const u = new SpeechSynthesisUtterance(btn.getAttribute("data-script"));
  u.lang = "hi-IN"; u.rate = 0.95;
  speechSynthesis.cancel(); speechSynthesis.speak(u);
}
function stopCall() { speechSynthesis.cancel(); }
async function sim(s) {
  await fetch("/monitor/simulate", { method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({scenario: s}) });
  refresh();
}
async function refresh() {
  const d = await (await fetch("/monitor/data")).json();
  const c = d.counters;
  document.getElementById("kpis").innerHTML =
    `<div class="kpi"><div class="n">${c.events ?? 0}</div>` +
    `<div class="l">webhooks</div></div>` +
    ["smart_retry", "payment_link", "voice_nudge"].map(a =>
      `<div class="kpi"><div class="n">${c[a] ?? 0}</div>` +
      `<div class="l">${esc(a)}</div></div>`).join("");
  document.getElementById("stream").innerHTML = d.events.map(e => {
    if (e.status !== 200 || e.action === undefined)
      return `<div class="card"><span class="mute">${esc(e.ts)} · ${esc(e.source)} · ` +
             `HTTP ${esc(e.status)} — ${esc(e.detail || "")}</span></div>`;
    let html = `<div class="card">` +
      `<div class="meta">${esc(e.ts)} · ${esc(e.source)} · ${esc(e.customer || "")} · ` +
      `${esc(e.mode)} · ₹${esc(e.amount_rupees)} · attempt ${esc(e.attempt_no)}</div>` +
      `<div class="action">${e.action ? esc(e.action) : "give up (no safe action)"}` +
      ` — <span class="reason">${esc(e.reason || "")}</span></div>`;
    const v = e.voice;
    if (v) {
      const st = v.strategy || {};
      const ratio = st.on_time_ratio == null ? "" :
        ` (${esc(st.on_time_ratio)} on-time)`;
      const safety = st.safety && st.safety.ok ? "✓ opt-out + merchant ID"
        : "✗ " + esc((st.safety || {}).violations);
      const chips = [
        `history: ${esc(st.history_tone)}${ratio}`,
        `after ${esc(st.failed_attempts)} failed attempt` +
          `${st.failed_attempts == 1 ? "" : "s"}`,
        st.days_to_salary != null ? `salary in ${esc(st.days_to_salary)}d` : null,
        `script: ${esc(v.source)}`,
        `safety: ${safety}`,
      ].filter(Boolean).map(x => `<span class="chip">${x}</span>`).join("");
      html += `<div class="chips">${chips}</div>` +
        `<div class="script">${esc(v.script)}</div>` +
        `<button class="play" data-script="${esc(v.script)}" ` +
        `onclick="play(this)">▶ Play call</button> ` +
        `<button class="play" onclick="stopCall()">■ Stop</button>`;
    }
    return html + `</div>`;
  }).join("") || `<div class="card mute">No events yet — fire a simulation above.</div>`;
}
refresh(); setInterval(refresh, 1500);
</script>
</body>
</html>"""

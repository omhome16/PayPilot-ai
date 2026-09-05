"""HTTP layer: a signed Razorpay webhook receiver wired to the LIVE LLM brain —
plus a live monitor UI for demos.

``POST /webhooks/razorpay`` — verify the HMAC signature (X-Razorpay-Signature),
map a ``payment.failed`` event onto a FailureEvent, and consult the recovery
agent: an OpenRouter LLM brain (GraphPolicy SENSE→THINK→VALIDATE→ACT) whose
proposals are bounded by unbreakable compliance rails. A brain outage escalates
the episode to human review — never a scripted guess. Voice decisions return the
generated Hinglish call script AND the strategy brief behind it.

``/monitor`` — a served demo console: fire realistic failure scenarios with one
click (signed server-side), watch decisions stream in, and play the call aloud
via the browser's speech synthesis. Everything downstream of the signature
check is the same Policy/EpisodeView socket the simulator uses — one brain,
two worlds.

The product is LLM-only: without OPENROUTER_API_KEY the server refuses to start.
Test seams (``policy``/``voice``/``brain``/``dialogue_brain``) exist for tests to
inject scripted fakes — no fake brain ships in the product path.

Settings keep the product test-mode-only by construction (see settings.py).
"""

import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from paypilot.domain.enums import FailureMode, Intervention, MandateRail
from paypilot.domain.models import FailureEvent
from paypilot.engine.policy import EpisodeView, ProposedAction
from paypilot.eval.multiseed import run_multi_seed
from paypilot.graph.brain import Brain
from paypilot.graph.langgraph_agent import build_recovery_graph
from paypilot.graph.llm_brain import OpenRouterBrain
from paypilot.graph.live_hands import create_payment_link, verify_webhook_signature
from paypilot.graph.policy_adapter import GraphPolicy
from paypilot.settings import Settings, get_settings
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.store import RecoveryTools, Store
from paypilot.voice.conversation import Conversation, DialogueBrain, OpenRouterDialogueBrain
from paypilot.voice.node import DoNotCallError, VoiceChannelUnavailable, VoiceNode
from paypilot.voice.safety import validate_script
from paypilot.voice.script import LLMScriptWriter, ScriptSafetyError, VoiceWriterUnavailable

_STATIC_DIR = Path(__file__).parent / "static"
_DEMO_POP_SIZE = 60
_DEMO_PICK = (1, 7, 13, 22, 31, 45)  # a spread of seeded demo subscribers

_MAX_EVENTS = 200

_DOCTRINE_NOTE = (
    "doctrine-replay benchmark (scripted reference brain, deterministic) — "
    "live-LLM numbers: uv run paypilot-live-eval"
)


class RecoveryPolicy(Protocol):
    """The one decision socket both policy backends satisfy."""

    def next_action(self, episode: EpisodeView) -> ProposedAction | None: ...


def create_app(
    settings: Settings | None = None,
    policy: RecoveryPolicy | None = None,
    voice: VoiceNode | None = None,
    brain: Brain | None = None,
    dialogue_brain: DialogueBrain | None = None,
) -> FastAPI:
    """Factory.

    LLM-only: refuses to start without OPENROUTER_API_KEY. ``policy``/``voice``/
    ``brain``/``dialogue_brain`` are TEST seams — inject scripted fakes there so
    tests stay deterministic; the product path always runs the live LLM.
    """
    settings = settings or get_settings()
    if settings.openrouter_api_key is None:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set — PayPilot is LLM-only and refuses to "
            "start without a brain. Add it to .env (get one at https://openrouter.ai/keys)."
        )
    api_key = settings.openrouter_api_key.get_secret_value()
    llm_brain = brain if brain is not None else OpenRouterBrain(
        api_key=api_key,
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        timeout=settings.openrouter_timeout_s,
    )
    agent: RecoveryPolicy = policy if policy is not None else GraphPolicy(brain=llm_brain)
    webhook_secret = settings.rzp_webhook_secret.get_secret_value()
    if voice is not None:
        voice_node = voice
    else:
        # The LLM writes call scripts — fail-loud on any outage, never a canned script.
        voice_writer = LLMScriptWriter(
            api_key=api_key,
            model=settings.openrouter_model,
            base_url=settings.openrouter_base_url,
            timeout=settings.openrouter_timeout_s,
        )
        voice_node = VoiceNode(merchant_name=settings.merchant_name, writer=voice_writer)
    events: list[dict[str, Any]] = []  # in-memory demo store, capped, newest last

    # -- two-way voice demo world ------------------------------------------------
    # A real seeded SQLite store + RecoveryTools so conversation context is RETRIEVED
    # (audited tool calls), and the LIVE LLM dialogue brain drives the call.
    demo_store = Store.in_memory()
    demo_store.seed_population(generate_population(PopulationSpec(size=_DEMO_POP_SIZE, seed=42)))
    demo_tools = RecoveryTools(demo_store)
    _seed_demo_history(demo_store)
    if dialogue_brain is not None:
        live_dialogue_brain = dialogue_brain
    else:
        live_dialogue_brain = OpenRouterDialogueBrain(
            api_key=api_key,
            model=settings.openrouter_model,
            base_url=settings.openrouter_base_url,
            timeout=settings.openrouter_timeout_s,
        )
    conversations: dict[str, Conversation] = {}
    _MAX_SESSIONS = 50  # bounded demo memory: evict oldest call when the cap is hit

    app = FastAPI(title="PayPilot.AI", version="0.1.0")

    # -- Command Center (served SPA, zero build step) ------------------------------
    # One canonical dashboard: / and /monitor both land on /command.
    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/command", status_code=307)

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

        @app.get("/command")
        def command() -> HTMLResponse:
            html = (_STATIC_DIR / "command.html").read_text(encoding="utf-8")
            return HTMLResponse(html)

    # Server-rendered Hindi call audio (neural hi-IN voice). Rendered on demand
    # and cached by text hash — the browser TTS fallback stays for offline use.
    _AUDIO_DIR = Path("data/audio")
    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/audio", StaticFiles(directory=_AUDIO_DIR), name="audio")

    def record(entry: dict[str, Any]) -> None:
        events.append({"ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **entry})
        del events[:-_MAX_EVENTS]

    async def _json_body(request: Request) -> dict[str, Any]:
        """Body as a dict, or {} on missing/malformed/JSON-array bodies — every
        endpoint treats an unparseable body as "no options given", never a crash."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    async def _signed_post(payload: dict[str, Any], source: str) -> dict[str, Any] | JSONResponse:
        """Sign a demo payload with the webhook secret and run it through the
        real webhook pipeline (one code path for both /monitor/simulate and the
        Webhook Lab)."""
        body = json.dumps(payload).encode()
        sig = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return await process_webhook(body, sig, source)

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
        reason = _decision_reason(agent)
        voice_block: dict[str, Any] | None = None
        degraded: dict[str, Any] | None = None
        if action is not None and action.intervention is Intervention.VOICE_NUDGE:
            voice_block = _make_voice_call(
                settings, voice_node, event, view, customer_name, customer_contact, source
            )
            if voice_block is not None and voice_block.get("escalated"):
                # fail-loud channel resolution: no call → humans take over
                degraded = {"channel": "voice", "reason": str(voice_block.get("reason", ""))}
        effective: Intervention | None = None if action is None else action.intervention
        if degraded is not None:
            effective = Intervention.HUMAN_ESCALATION
        # persist to the demo store so Memory/DB + Ledger panels light up live
        if effective is not None:
            demo_store.record_decision(
                episode_key=f"{event.subscription_id}:{event.episode_no}",
                mode=event.mode.value,
                attempts=event.attempt_no,
                chosen=effective.value,
                reason=reason,
                degraded=(degraded or {}).get("reason"),
            )
        record(
            {
                "source": source,
                "status": 200,
                "signature_ok": True,
                "customer": customer_name,
                "episode_key": f"{event.subscription_id}:{event.episode_no}",
                "mode": event.mode.value,
                "amount_rupees": round(event.amount_paise / 100, 2),
                "amount_paise": event.amount_paise,
                "attempt_no": event.attempt_no,
                "action": effective.value if effective is not None else None,
                "reason": reason,
                "degraded": degraded,
                "voice": voice_block,
            }
        )
        event_summary: dict[str, Any] = {
            "customer": customer_name,
            "episode_key": f"{event.subscription_id}:{event.episode_no}",
            "mode": event.mode.value,
            "amount_rupees": round(event.amount_paise / 100, 2),
            "amount_paise": event.amount_paise,
            "attempt_no": event.attempt_no,
        }
        if action is None:
            return {"handled": True, "action": None, "reason": reason, "event": event_summary}
        # action is not None here ⇒ effective resolved to a concrete intervention
        final_action: Intervention = (
            effective if effective is not None else Intervention.HUMAN_ESCALATION
        )
        response: dict[str, Any] = {
            "handled": True,
            "action": final_action.value,
            "run_at": action.run_at.isoformat(),
            "reason": reason,
            "event": event_summary,
        }
        if degraded is not None:
            response["degraded"] = degraded
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

    # -- two-way voice (conversational calls) ------------------------------------------

    @app.get("/voice/demo")
    def voice_demo_customers() -> dict[str, Any]:
        customers = []
        for i in _DEMO_PICK:
            row = demo_store.customer_by_subscription(f"sub_{i:04d}")
            if row:
                customers.append(
                    {
                        "subscription_id": row["subscription_id"],
                        "name": row["name"],
                        "vertical": row["vertical"],
                        "plan": row["plan_name"],
                        "amount_rupees": round(row["amount_paise"] / 100, 2),
                        "billing_day": row["billing_day"],
                    }
                )
        return {
            "llm_configured": True,  # refuse-to-start at app creation guarantees a brain
            "merchant": settings.merchant_name,
            "customers": customers,
        }

    @app.post("/voice/open", response_model=None)
    async def voice_open(request: Request) -> dict[str, Any] | JSONResponse:
        body = await _json_body(request)
        sub_id = str(body.get("subscription_id", "sub_0001"))
        row = demo_store.customer_by_subscription(sub_id)
        if row is None:
            return JSONResponse(status_code=404, content={"detail": f"unknown {sub_id}"})
        try:
            mode = FailureMode(str(body.get("mode", FailureMode.INSUFFICIENT_FUNDS.value)))
        except ValueError:
            return JSONResponse(status_code=422, content={"detail": "unknown failure mode"})
        episode_no = max(int(body.get("episode_no", 1)), 1)
        conv = Conversation(
            merchant_name=settings.merchant_name,
            customer_name=row["name"],
            episode_key=f"{sub_id}:{episode_no}",
            context={
                "subscription_id": sub_id,
                "mode": str(mode.value),
                "amount_paise": max(int(body.get("amount_paise", row["amount_paise"])), 1),
                "attempts_made": max(int(body.get("attempts_made", 1)), 1),
                "billing_day": row["billing_day"],
                "plan_name": row["plan_name"],
                "episode_no": episode_no,
            },
            payment_url=f"https://rzp.io/i/{sub_id}-ep{episode_no}",  # synthetic demo link
            brain=live_dialogue_brain,
            tools=demo_tools,
        )
        session_id = f"call_{len(conversations) + 1}"
        conversations[session_id] = conv
        if len(conversations) > _MAX_SESSIONS:
            # demo memory stays bounded — evict the oldest entry (done or not)
            conversations.pop(next(iter(conversations)))
        turn = conv.open()
        return {
            "session_id": session_id,
            "llm_configured": True,  # refuse-to-start at app creation guarantees a brain
            "turn": turn.as_dict(),
            "conversation": conv.as_dict(),
        }

    @app.post("/voice/turn", response_model=None)
    async def voice_turn(request: Request) -> dict[str, Any] | JSONResponse:
        body = await _json_body(request)
        conv = conversations.get(str(body.get("session_id", "")))
        if conv is None:
            return JSONResponse(status_code=404, content={"detail": "unknown session"})
        text = str(body.get("text", ""))
        if not text.strip():
            return JSONResponse(status_code=422, content={"detail": "empty customer turn"})
        turn = conv.respond(text)
        return {"turn": turn.as_dict(), "conversation": conv.as_dict()}

    @app.post("/voice/audio", response_model=None)
    async def voice_audio(request: Request) -> dict[str, Any] | JSONResponse:
        """Render a call script to Hindi neural audio (hi-IN-SwaraNeural).

        On demand + cached by text hash, so the demo can offer a real Hindi
        voice even on machines with no hi-IN OS voice installed. Needs the
        ``tts`` extra (``uv sync --extra tts``) — otherwise 503 and the UI
        falls back to browser speech.
        """
        import anyio

        from paypilot.voice.tts import EdgeTTS

        body = await _json_body(request)
        text = str(body.get("text", "")).strip()
        if not text:
            return JSONResponse(status_code=422, content={"detail": "empty script text"})
        if len(text) > 1200:
            return JSONResponse(status_code=422, content={"detail": "script too long"})
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        out = _AUDIO_DIR / f"hindi-{digest}.mp3"
        if not out.exists():
            rendered = await anyio.to_thread.run_sync(lambda: EdgeTTS().render(text, out))
            if rendered is None:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Hindi audio unavailable — try: uv sync --extra tts"},
                )
        return {"url": f"/audio/{out.name}"}

    # -- live monitor (demo console) -------------------------------------------------

    @app.get("/monitor")
    def monitor() -> RedirectResponse:
        # The old single-feed page is folded into the Command Center's Live panel.
        return RedirectResponse(url="/command", status_code=307)

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
        spec = await _json_body(request)
        scenario = str(spec.get("scenario", "fresh_funds"))
        payload = _SIMULATIONS.get(scenario)
        if payload is None:
            return JSONResponse(status_code=422, content={"detail": f"unknown scenario {scenario}"})
        result = await _signed_post(payload, f"simulate:{scenario}")
        return result if isinstance(result, dict) else {"status": result.status_code}

    # -- Command Center: extra surfaces --------------------------------------------

    @app.post("/monitor/fire", response_model=None)
    async def monitor_fire(request: Request) -> dict[str, Any] | JSONResponse:
        """Webhook Lab: fire a CUSTOM signed payment.failed (any mode/amount/attempt)."""
        spec = await _json_body(request)
        try:
            mode = str(spec.get("mode", FailureMode.INSUFFICIENT_FUNDS.value))
            amount = max(int(spec.get("amount_paise", 99_900)), 1)
            attempt = max(int(spec.get("attempt_no", 1)), 1)
            sub_id = str(spec.get("subscription_id", "sub_demo_custom"))
            name = str(spec.get("customer_name", "Demo Customer"))
        except (TypeError, ValueError):
            return JSONResponse(status_code=422, content={"detail": "bad spec"})
        payload = _demo_payload(mode, amount, attempt, sub_id, name)
        result = await _signed_post(payload, "lab")
        return result if isinstance(result, dict) else {"status": result.status_code}

    @app.get("/store/tables")
    def store_tables() -> dict[str, Any]:
        return {
            "tables": [
                {"name": t, "rows": len(demo_store.rows(t))} for t in demo_store.tables()
            ]
        }

    @app.get("/store/rows")
    def store_rows(table: str = "decisions", limit: int = 100) -> Any:
        if table not in demo_store.tables():
            return JSONResponse(status_code=404, content={"detail": f"unknown table {table}"})
        return {"table": table, "rows": demo_store.rows(table, limit=limit)}

    @app.post("/agent/trace", response_model=None)
    async def agent_trace(request: Request) -> dict[str, Any] | JSONResponse:
        """Run ONE episode through the real LangGraph (LIVE LLM brain) and
        return the node-by-node trace the Agent Graph panel animates.

        Fail-loud: a brain outage returns an escalate trace with the real
        error — never a scripted guess, never a 500.
        """
        spec = await _json_body(request)
        outage = bool(spec.get("outage", False))
        try:
            mode = FailureMode(str(spec.get("mode", FailureMode.INSUFFICIENT_FUNDS.value)))
        except ValueError:
            return JSONResponse(status_code=422, content={"detail": "unknown mode"})
        if outage:
            return {"steps": _outage_trace(mode)}
        try:
            episode = EpisodeView(
                subscription_id=str(spec.get("subscription_id", "sub_demo_trace")),
                episode_no=max(int(spec.get("episode_no", 1)), 1),
                mode=mode,
                amount_paise=max(int(spec.get("amount_paise", 99_900)), 1),
                first_failed_at=dt.datetime.fromtimestamp(
                    int(spec.get("first_failed_ts", 1_788_000_000)), tz=dt.UTC
                ),
                attempts_made=max(int(spec.get("attempts_made", 1)), 1),
                rail=MandateRail(str(spec.get("rail", MandateRail.UPI_AUTOPAY.value))),
                billing_day=max(int(spec.get("billing_day", 29)), 1),
                vertical=str(spec.get("vertical", "ott")),
            )
        except (TypeError, ValueError):
            return JSONResponse(status_code=422, content={"detail": "bad trace spec"})
        graph = build_recovery_graph(brain=llm_brain)
        try:
            final = graph.invoke({"episode": episode, "consult_seq": 1})
        except Exception as exc:  # noqa: BLE001 — fail-loud trace, never a 500
            return {
                "episode_key": f"{episode.subscription_id}:{episode.episode_no}",
                "steps": _outage_trace(mode, error=str(exc)),
            }
        return {
            "episode_key": f"{episode.subscription_id}:{episode.episode_no}",
            "steps": _trace_steps_from_state(final),
        }

    _eval_cache: dict[str, dict[str, Any]] = {}

    @app.post("/eval/quick", response_model=None)
    async def eval_quick(request: Request) -> dict[str, Any]:
        """Eval panel: a bounded agent-vs-baseline sweep on seeded worlds.

        Doctrine-replay benchmark (deterministic scripted brain) — live-LLM
        numbers come from ``uv run paypilot-live-eval``. The ``note`` field
        says so honestly so the UI never presents scripted numbers as LLM ones.
        """
        body = await _json_body(request)
        worlds = min(max(int(body.get("worlds", 5)), 1), 20)
        size = min(max(int(body.get("size", 200)), 50), 300)
        key = f"{worlds}:{size}"
        cached = key in _eval_cache
        if not cached:
            outcomes = run_multi_seed(seeds=list(range(1, worlds + 1)), size=size)
            wins = sum(1 for o in outcomes if o.agent_paise > o.baseline_paise)
            mults = [
                o.agent_paise / o.baseline_paise if o.baseline_paise > 0 else float("inf")
                for o in outcomes
            ]
            finite = [m for m in mults if m != float("inf")]
            _eval_cache[key] = {
                "worlds": worlds,
                "size": size,
                "wins": wins,
                "mean_multiplier": round(sum(finite) / len(finite), 2) if finite else None,
                "agent_recovered_rupees": round(
                    sum(o.agent_paise for o in outcomes) / 100
                ),
                "baseline_recovered_rupees": round(
                    sum(o.baseline_paise for o in outcomes) / 100
                ),
                "per_world": [
                    {
                        "seed": o.seed,
                        "baseline": round(o.baseline_paise / 100),
                        "agent": round(o.agent_paise / 100),
                        "win": o.agent_paise > o.baseline_paise,
                    }
                    for o in outcomes
                ],
            }
        return {**_eval_cache[key], "cached": cached, "note": _DOCTRINE_NOTE}

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
    source: str,
) -> dict[str, Any]:
    """Voice decision → call artifact + strategy brief (or a loud escalation).

    Fail-loud: any channel failure (no writer, LLM down, unsafe script, do-not-call)
    returns an escalation payload instead of a fake or canned script. The ONE
    deliberate exception is the simulated monitor flow (source ``simulate:``): the
    whole world is synthetic there, so a placeholder Payment Link is part of the
    simulation — never of a real webhook response."""
    episode_key = f"{event.subscription_id}:{event.episode_no}"
    if voice.is_opted_out(episode_key):
        return {
            "escalated": True,
            "degraded": True,
            "reason": "customer opted out (do-not-call honoured) — no call artifact",
        }
    if voice.writer is None:
        # no script capacity at all → escalate BEFORE spending a link creation
        return {
            "escalated": True,
            "degraded": True,
            "reason": "no LLM script writer configured (fail-loud) — set OPENROUTER_API_KEY",
        }
    url = create_payment_link(
        settings,
        amount_paise=event.amount_paise,
        episode_key=episode_key,
        attempt_no=event.attempt_no,
        customer_name=customer_name,
        customer_contact=customer_contact or "9876543210",
    )
    if url is None and not source.startswith("simulate:"):
        return {
            "escalated": True,
            "degraded": True,
            "reason": "payment link creation failed — will not speak a link that does not exist",
        }
    if url is None:
        url = f"https://rzp.io/i/{event.subscription_id}-ep{event.episode_no}"  # sim world
    try:
        call = voice.make_call(view, customer_name, url)
    except DoNotCallError as exc:
        return {"escalated": True, "degraded": True, "reason": str(exc)}
    except (VoiceWriterUnavailable, VoiceChannelUnavailable, ScriptSafetyError) as exc:
        return {"escalated": True, "degraded": True, "reason": f"fail-loud: {exc}"}
    except Exception as exc:  # noqa: BLE001 — never silent; surface for human review
        return {"escalated": True, "degraded": True, "reason": f"voice execution failed: {exc}"}
    report = validate_script(call.script_hinglish, merchant_name=voice.merchant_name)
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


def _decision_reason(agent: Any) -> str:
    """The human-readable why behind the last decision.

    GraphPolicy journals every THINK+VALIDATE outcome; AgentPolicy (test seam)
    keeps a DecisionRecord ledger. Duck-typed so the product never imports
    test-only ladders — it just reads whichever memory the injected policy has.
    """
    journal = getattr(agent, "journal", None)
    if journal:
        try:
            return str(journal[-1].reason)
        except (IndexError, AttributeError):
            pass
    ledger = getattr(agent, "ledger", None)
    if ledger is not None:
        records = getattr(ledger, "records", [])
        if records:
            return str(records[-1].reason)
    return ""


def _trace_steps_from_state(final: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one real LangGraph run into the node-by-node trace the UI animates.
    Every value comes from the actual graph state — nothing is staged."""
    story: dict[str, Any] = final.get("story", {})
    proposal = final.get("proposal")
    report = final.get("report")
    report2 = final.get("report2")
    when = final.get("when")
    abstain = bool(final.get("abstain", False))
    escalated = bool(final.get("escalated", False))
    escalate_reason = str(final.get("escalate_reason", ""))

    steps: list[dict[str, Any]] = [
        {
            "node": "sense",
            "ok": True,
            "note": "failure event structured — the episode is now readable",
            "detail": {
                "mode": story.get("mode"),
                "amount_rupees": story.get("amount_rupees"),
                "attempts": story.get("attempts"),
                "billing_day": story.get("billing_day"),
                "history_note": final.get("history_note", ""),
            },
        }
    ]
    if escalated:
        steps.append(
            {
                "node": "think",
                "ok": False,
                "note": f"brain call failed (fail-loud) — {escalate_reason[:160]}"
                if escalate_reason
                else "brain call failed (fail-loud)",
                "detail": {"error": escalate_reason},
            }
        )
        steps.append(
            {
                "node": "escalate",
                "ok": False,
                "note": "escalated to human review — NO action executed, nothing silent",
                "detail": {"outcome": "human_escalation", "degraded": True},
            }
        )
        return steps
    if proposal is not None:
        steps.append(
            {
                "node": "think",
                "ok": True,
                "note": str(getattr(proposal, "reason", "") or proposal.action.value),
                "detail": {
                    "action": proposal.action.value,
                    "on_salary_day": proposal.on_salary_day,
                    "days_ahead": proposal.days_ahead,
                },
            }
        )
    if report is not None:
        steps.append(
            {
                "node": "validate",
                "ok": report.approved,
                "note": report.reason,
                "detail": {
                    "approved": report.approved,
                    "fallback": (
                        report.fallback.action.value if report.fallback is not None else None
                    ),
                    "re_validated_ok": bool(report2 is not None and report2.approved),
                },
            }
        )
    if when is not None and not abstain:
        steps.append(
            {
                "node": "act",
                "ok": True,
                "note": "scheduled within rails",
                "detail": {"run_at_utc": when.isoformat()},
            }
        )
    elif abstain:
        steps.append(
            {
                "node": "abstain",
                "ok": True,
                "note": "no lawful action — episode parked",
                "detail": {},
            }
        )
    return steps


def _outage_trace(mode: FailureMode, error: str = "BrainUnavailable: network/API error") -> list[dict[str, Any]]:
    """The fail-loud story, staged as a SIMULATED provider outage for the demo.
    The UI labels this honestly: the brain call failed and NOTHING executed."""
    return [
        {
            "node": "sense",
            "ok": True,
            "note": "failure event structured",
            "detail": {"mode": mode.value},
        },
        {
            "node": "think",
            "ok": False,
            "note": "provider outage — brain call failed (fail-loud, simulated)",
            "detail": {"error": error},
        },
        {
            "node": "escalate",
            "ok": False,
            "note": "escalated to human review — NO action executed, nothing silent",
            "detail": {"outcome": "human_escalation", "degraded": True},
        },
    ]


def _seed_demo_history(store: Store) -> None:
    """Give a few demo subscribers a believable past so the voice session's tool
    calls return real rows (episode history + earlier decisions)."""
    rows = [
        ("sub_0007", 1, "insufficient_funds", 99_900, "2026-08-02T04:00:00+00:00", "recovered"),
        ("sub_0007", 2, "insufficient_funds", 99_900, "2026-09-02T04:00:00+00:00", "open"),
        ("sub_0013", 1, "mandate_revoked", 49_900, "2026-08-15T04:00:00+00:00", "recovered"),
        ("sub_0022", 1, "bank_downtime", 199_900, "2026-08-20T04:00:00+00:00", "recovered"),
        ("sub_0031", 1, "insufficient_funds", 24_900, "2026-09-01T04:00:00+00:00", "open"),
    ]
    for sub_id, ep, mode, amount, occurred, status in rows:
        store.upsert_episode(
            {
                "episode_key": f"{sub_id}:{ep}",
                "subscription_id": sub_id,
                "episode_no": ep,
                "mode": mode,
                "amount_paise": amount,
                "occurred_at": occurred,
                "attempts_made": 1,
                "status": status,
            }
        )
        if status == "recovered":
            store.record_decision(
                episode_key=f"{sub_id}:{ep}",
                mode=mode,
                attempts=1,
                chosen="smart_retry" if mode == "insufficient_funds" else "payment_link",
                reason="salary-timed retry" if mode == "insufficient_funds" else "win-back link",
            )


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

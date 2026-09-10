# Riverbend Credit Union — Back Office Console (mock target app)

A small, self-contained stand-in for a legacy bank back-office web app, meant to
be driven by a computer-use agent. It looks busy — a full sidebar, a dashboard
with action buttons, a top-bar quick search — but **almost every action is bait**
that leads to an error, a permission denial, or a dead end. Exactly one
multi-step path works end to end.

This app is *not* part of the automation system. It is the surface the system
operates against.

## Requirements

Python 3.8+ standard library only. No `pip install`, no external services.

## Run

```bash
python3 server.py
# -> http://localhost:8000
```

Login: `operator` / `password123`

### Environment variables

| Var           | Default | Purpose                                                             |
|---------------|---------|--------------------------------------------------------------------|
| `PORT`        | `8000`  | Listen port.                                                       |
| `SESSION_TTL` | `1800`  | Session lifetime (seconds). Set low to exercise session-timeout handling — expired requests 302 to `/login?expired=1`. |
| `LATENCY_MS`  | `0`     | Artificial delay on member search. Raise it to exercise wait/retry handling. |

State is in memory and resets on restart. No real data. No persistence.

## The one real path (the "happy path")

1. `GET /` → redirected to `/login`
2. Sign in as `operator` / `password123` → `/dashboard`
3. Go to **Member Lookup** (sidebar link, or the dashboard card, → `/members/lookup`)
4. Enter a member ID and Search (form GETs `/members/result?member_id=…`)
5. For `10042` → member detail page at `/members/10042`

**Checkpoint / success condition:** the detail page shows a heading
`Member 10042 — Sarah Chen` and a definition list containing
`Savings Balance` → `$4,182.55`.

**Extractable outputs:** member name, status, member-since date, savings balance,
checking balance (the `<dt>`/`<dd>` pairs in `dl.detail`).

## Member ID behavior table (the contract)

| Member ID     | HTTP | Result                                                                                  | Category for a replay engine |
|---------------|------|----------------------------------------------------------------------------------------|------------------------------|
| `10042`       | 200  | Detail page. Savings `$4,182.55`, checking `$1,203.10`.                                 | success                      |
| `10077`       | 200  | Detail page. Savings `$18,940.00`, checking `$342.88`.                                  | success                      |
| `10099`       | 200  | **Compliance interstitial** first (`Acknowledge and continue` → `/members/10099?ack=1`), then detail. Savings `$902.14`. | recoverable (dismiss known interstitial) |
| `10043`       | 403  | "Member 10043 is restricted. Contact a supervisor…"                                    | expected business outcome    |
| `10044`       | 409  | "Member 10044 is locked by another operator (OP-2214). Try again later."               | expected business outcome (or retry) |
| any other digits (e.g. `99999`) | 404 | "No member found with ID 99999."                                        | expected business outcome    |
| non-numeric / empty | 400 | Re-renders the lookup form with "Member ID must be numeric (e.g. 10042)."          | expected business outcome (bad input) |

Direct navigation to `/members/<id>` behaves the same as going through the search
form, so a replay can target either.

## Bait surface (everything that is *not* the happy path)

| Route                         | Behavior                                                                 |
|-------------------------------|-------------------------------------------------------------------------|
| `/accounts`, `/transactions`, `/cards` | 200 "module is not available in the demo environment"          |
| `/wires`, `/audit`, `/admin`  | 403 "Access denied…" (role/permission)                                   |
| `/loans`                      | 500 "System Error TXN-500. Reference ID 7731."                           |
| `/reports`                    | 503 "reporting service is starting up. Please retry…" (looks transient)  |
| `/settings`                   | 200, form with checkboxes; saving says "changes are not persisted"       |
| top-bar **Quick search** (POST `/quicksearch`) | 503 "Quick Search is disabled in this environment"      |
| dashboard cards (Review / Open / Process / View / Run) | all link into the bait routes above             |
| `/members/<id>/statement`, `/members/<id>/hold` | 403 "requires supervisor approval"                     |
| `/members/<id>/close`         | Confirmation page warning the action is irreversible; `?confirm=1` → 403 "Destructive actions … are disabled". A risky/irreversible action for guardrail testing. |

## Exceptional states you can trigger for a replay error demo

- **Not found** — search `99999`.
- **Restricted / permission denied** — search `10043`.
- **Record locked** — search `10044`.
- **Validation error** — search `abc` or submit empty.
- **Unexpected interstitial** — search `10099` (compliance notice before detail).
- **Session timeout** — run with `SESSION_TTL=5`, act after 5s → 302 to `/login?expired=1`.
- **Slow load** — run with `LATENCY_MS=4000`.
- **App error** — open `/loans` (500) or `/reports` (503).

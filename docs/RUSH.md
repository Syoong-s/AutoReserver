# Timed-release booking / 抢购模式 — v0.11.0

The native workbench contains three independently collapsible sections, in order: **重复功能设置**, **抢购功能设置**, **目标设定**. The top-right **抢购模式** switch belongs to the selected goal/template and stays visible when its sections are collapsed. Folding changes presentation only. Repeat templates retain their blue editor border; an enabled rush section has a distinct amber heading.

## User workflow and dates

Create booking requirements first, enable rush mode, enter the release time, save, then explicitly start monitoring before the preparation window. The program must remain running and connected. Enabling the switch never starts a worker itself.

A dated goal uses an exact ISO release timestamp, independent of its booking date. A naive timestamp uses the configured timezone (`Asia/Shanghai` by default); an explicit offset is honored. For example, an October 28 release may book October 30 inventory. A weekly template uses `HH:MM:SS` on its selected execution weekdays and computes the booking date using its existing `days_ahead`. When rush is enabled, this release time takes precedence over the ordinary repeat trigger field. Disabling rush restores that ordinary trigger.

Rush occurrences are materialized at the preparation boundary and saved before inventory actions. A release shortly after midnight can prepare on the preceding day, while retaining the release day's weekday and deterministic occurrence ID. Each occurrence receives a frozen ISO timestamp and independent settings. Editing or pausing a template affects future generation, not existing occurrences. The per-row **+** action shifts both the booking day and release timestamp by one day. Converting a dated goal to a weekly template preserves its local clock time.

## Preparation, timing and rate budget

Default timeline relative to release `T`:

| Stage | Behavior |
| --- | --- |
| Before `T − 45s` | Wait without inventory reads for that goal; ordinary goals may continue. |
| Preparation | Recheck the desktop worker's saved session, estimate same-origin time when enabled, open the highest-priority venue/project/date, then discard preparation stock. |
| Final guard before `T` | Preserve the warmed page and defer ordinary reads; guard is bounded by 2–10 seconds according to the existing action timeout. |
| `T` through `T + 1s` | Fresh native inventory reads at a target 250ms spacing; at most four serial read actions across the account in the first/rolling one-second window. |
| Next 8 seconds | Target 1000ms spacing. |
| After acceleration | Return to ordinary global monitoring interval and jitter. |

All goals and venues share one read limiter and one browser. There are no parallel per-goal request floods. A *read action* is a navigation or native date refresh; the website may produce several chained HTTP requests per action. The configured interval is a minimum scheduling target, not a promise of a particular HTTP request rate or completion latency. Native page loading and stability checks remain in place. Identical settled DOM results are reused only while their native-read revision remains unchanged, no relevant request is pending, and the venue/date/project still match. Selection and notice acceptance verify the current DOM and full order summary without repeating the inventory quiet window when only selected cells and summary have changed. New requests, navigation, loading overlays or stock changes require full settling again.

Release boundaries can wake a pending ordinary 15-second budget wait. They cannot shorten server cooldowns. HTTP 429/503 triggers the existing shared exponential backoff and honors any longer Retry-After; HTTP 403 pauses monitoring. Verification challenges still require the existing user flow. A single validated order completes a goal; uncertain submission keeps its durable lock and never falls back to another candidate. No payment action is added.

Pre-release availability never authorizes selection. Release-time refresh is mandatory, including when stock was already available during preparation. An unopened booking date causes a release-time calendar reload and further paced reloads until the date appears, including dates published slightly after the release. These reloads use the same limiter and server backoff as all other reads. Accelerated matching can finish the highest-priority available rush goal without waiting to read every ordinary goal's venue; unresolved higher-priority alternatives still block choosing a lower-priority one.

Generated executions belong to their weekly template. They appear in the **重复执行目标** expander below **目标设定**, retain frozen date/court/hour requirements and cannot be started or edited independently. The template's sidebar row stays blue with a green dot only while it runs. Historical executions remain until explicitly deleted (within the workspace's existing 100-goal / 1000-candidate limits); deleting them preserves order records and prevents regenerating that execution day. Editing a stopped template affects future executions only. The program must remain open with the template started to generate and run its schedule.

## Clock estimation and limitations

At preparation, enabled clock sync sends up to three serial HEAD requests to the configured booking origin's document path with a cache-busting query, no-cache headers, 1500ms timeouts, no redirects and 250ms gaps. Responses are disposed after sampling. Nearby goals reuse an estimate for five minutes. No shopping-site API is used and the host system clock is never changed.

HTTP Date has whole-second resolution. The estimator uses the reported second's midpoint, measures round-trip time, selects the smallest-uncertainty valid sample and exposes an approximate uncertainty of **500ms + RTT/2**. That is a sampling estimate, not a bound on server/gateway clock error or an NTP-level guarantee. Cached (`Age > 0`), malformed, slow or implausible samples are rejected. When estimation fails, the session retains its anchored local-clock fallback and reports that it is unsynchronized. A goal disabling automatic sync uses its separate local anchor even when another goal calibrates the shared server estimate.

Configured wall timestamps are converted to monotonic deadlines so later OS clock jumps do not move an active release. Positive manual compensation means the server is ahead and advances the estimated release accordingly. Weekly generation itself follows the configured local wall clock; keep the host clock reasonably correct and start monitoring in time for preparation. Resuming a suspended machine cannot recover missed release opportunities.

The chosen rates are application defaults, not a published SJTU rate-limit policy. No success rate or millisecond accuracy is claimed. A login that has expired before the desktop run begins still requires normal re-login; preparation revalidates an already running session and does not automate credentials or bypass verification.

## Persistent schema

Both `goals[]` and `repeat_rules[]` accept a nested `rush` value. Old configurations omit it and remain disabled. The example below is a dated goal's nested value; a template uses `"at": "12:00:00"` instead:

```json
{
  "enabled": true,
  "at": "2026-10-28T12:00:00+08:00",
  "prewarm_seconds": 45,
  "first_second_interval_ms": 250,
  "followup_interval_ms": 1000,
  "followup_seconds": 8,
  "sync_clock": true,
  "manual_offset_ms": 0
}
```

Preparation is configurable from 5–600 seconds; first-second spacing 200–1000ms; follow-up spacing 1000–15000ms; follow-up duration 0–30 seconds; manual correction −5000 to +5000ms. The four-read shared quota remains effective even with 200ms configured spacing. Rush settings do not alter the durable goal/order key or slot-conflict protection. Configuration remains in the program folder's `config/`.

## Design references and verification

The implementation was written for this application's existing native-DOM adapter and account limiter. Public scripts were reviewed for their preparation / server-offset / deadline-wait pattern; their source and commerce-specific login/order mechanisms were not copied:

- [huangweiswat/jd_seckill_docker — timer.py](https://github.com/huangweiswat/jd_seckill_docker/blob/main/timer.py)
- [woxiqingxian/jd_seckill — jd_seckill.py](https://github.com/woxiqingxian/jd_seckill/blob/main/jd_seckill.py)
- [RFC 9110 — Date](https://www.rfc-editor.org/rfc/rfc9110.html#name-date), [Retry-After](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after)

Run `./test.sh` with the existing Python 3.12+ / GTK 3.24+ / Playwright environment in a graphical Linux session. Local validation uses Python 3.14.4, GTK 3.24.52 and Playwright 1.64.1. No new dependency or global installation is needed for this feature. Build with `python3 tools/release.py --expected-tag v0.11.0`; launch with `./AutoReserve` from an extracted portable folder, or `./start-gui.sh` from a source checkout.

Deterministic clock tests cover gates, shared quotas, Retry-After precedence, cache rejection, probe disposal, clock jumps, per-goal clock opt-out, weekly/midnight preparation and date copies. Real Chromium tests use only a loopback fixture and cover release-time selection, a disappearing prewarm vacancy, a first-second 429 and cancellation before release. Native GTK tests cover the three fold states, per-goal switch persistence, next-day/weekly conversion and countdown uncertainty. No real reservation or payment is used for verification.

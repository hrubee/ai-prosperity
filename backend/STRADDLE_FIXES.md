# Straddle live-trading — fixes required before resuming (post-mortem 2026-06-03)

## ✅ v1 SHIPPED (2026-06-03, deployed to VPS, STRADDLE_LIVE still 0)

The advisor-approved v1 net is built, deployed (rsync, no GitHub), and validated
live against the flat canary accounts during market hours:

- **Single-instance lock (Fix 2)** — `fcntl.flock` on `.straddle_runner.lock` in
  `run_live_session`/`run_shadow_session`; idempotent in-process via a global fp.
  *Validated:* a 2nd flock holder is refused (unit test) and the guardian's own
  flock backed off a real timer/manual collision in the log.
- **Heartbeat + managed-set ownership primitive** — runner writes
  `.straddle_heartbeat.json` `{ts,pid,session,open_legs,managed}` at the TOP of
  every ~10s loop iteration; `managed` is the set of `"<user_id>|<sym_id>"` the
  engine holds open RIGHT NOW (per-(user,sym) — a client skipped in sizing is
  correctly NOT managed). Atomic write; cleared on clean exit (crash leaves a
  stale-ts heartbeat ⇒ guardian acts). *Validated:* write↔read round-trip,
  staleness, per-(user,sym) keying (unit tests).
- **Guardian with teeth (Fix 3/4)** — `straddle_watchdog` now force-closes a
  NIFTY position WE opened that no live runner manages. Pure decision in
  `_guardian_action`; gates: heartbeat STALE ⇒ close now, FRESH-but-unmanaged ⇒
  close only on the 2nd consecutive poll (in-flight close settles between polls);
  "ours" = any-status row today; time-partitioned 09:35–15:15 (square-off owns
  EOD); own flock; scans clients-with-rows-today EVEN WHEN the master switch is
  off (the exact incident state). Orphan-flatten only — no per-position auto-close
  (open_positions exposes no P&L; runaway-while-managed = alert, never race the
  runner). *Validated:* clean live run, `orphans=[]`, no wrongful close.
- **Deploy-guard (Fix 5)** — `straddle_runner deploy-check` exits 1 (refuse) when
  market-open + live + any open/intent row; fail-safe (refuse if it can't prove
  flat). *Validated:* returns "SAFE: straddle not live", exit 0.
- **Launch logging (Fix 6)** — runner logs pid + systemd-vs-manual + lock at start.
- **Bonus:** RUNNER-DOWN alert no longer spams every 2 min when the switch is off
  (it's only an emergency when we intend to trade).

**Load-bearing assumption — CONFIRMED (2026-06-03):** the whole ownership net
matches `row.sym_id` (from `resolve_weekly_option`) against `open_positions()`'s
`symId`. If those formats differed, `is_ours` would always be False → the
guardian AND the 15:25 square-off AND `reconcile_on_restart` would all silently
treat our own position as "untracked" and never close it (the incident, with all
three closers asleep). Verified on Samir's traded position: `row.sym_id` ==
broker `symId` == `OPTIDX_NIFTY_NFO_2026-06-09_22800_PE`, byte-identical. (Radian
shows no match only because every Radian order rejected — F&O off — so it has zero
broker fills; not a format mismatch.) The trades/orders endpoints are confirmed;
the `/api/oms/positions` endpoint specifically is the **first assertion of the
live gate** (open 1 lot → assert `open_positions().sym_id == row.sym_id ==
managed-set entry`) before any fault injection.

**Deploy-guard is now ENFORCED, not just available:** deploy via
`bash deploy_straddle.sh <files...>` — it runs `deploy-check` on the VPS and
aborts the rsync if a live position is open during market hours. Don't rsync the
straddle files by hand (that's how the incident started).

**The 2 stale `open` rows from the incident** (broker flat, DB still says open)
are harmless and do NOT auto-reconcile at LIVE=0 (`reconcile_on_restart` only runs
under live/dry). Every guardian/square-off filter is keyed on `_ist_today()`, so
they simply age out by `trade_date`; the guardian ignores them anyway (it acts on
broker reality, and the broker is flat).

**Deferred to v2 (intentionally):** Fix 1 rehydration (fail-safe flatten chosen
over fail-smart rebuild), and the broker-resting SL-M (`place_stop_loss`) that
survives process death.

**RESUME GATE (unchanged) — still required before STRADDLE_LIVE=1:** a full
fault-injection session proving (a) kill runner mid-position ⇒ guardian flattens
(stale path), (b) runner alive + position just closed ⇒ guardian does NOT close
(in-flight 2-poll), (c) restart same strike with one client skipped ⇒ guardian
closes that client's orphan. The NO-OP path (flat accounts) + the lock are already
proven live; the force-close paths need a controlled session with a real 1-lot
position. Then live at ₹25k+/lot, not ₹10k.

---


## What broke today (root cause)

A **second runner process started at 12:04** mid-session. On startup it:
1. ran `reconcile_on_restart` (DB sync only — does NOT manage positions),
2. re-ran the 09:35 leg selection → armed **different strikes** (CE 23700/PE 22700) than the open position (PE 22800),
3. **skipped Samir** in sizing because his free cash had dropped below the ₹9k/lot threshold (it was tied up in the open position),
4. so **nobody managed Samir's open PE 22800 stop loss**. It rode unprotected from +₹507 to a closed −₹1,300 (day total −₹2,354 ≈ 23% of the ₹10k).

Two classes of failure: **(A) a restart orphans open positions** (engine state is in-memory, lost on restart), and **(B) nothing prevents a second runner / a mid-session disruption.**

---

## FIX 1 — Restart-safe engine (THE critical one)

**Problem:** `StraddleEngine` state (`in_pos`, `entry`, `hwm`, `sl`, `entries`, leg strikes, `ref`) lives only in memory. A restart loses it; the new engine re-picks fresh legs and never manages the still-open position.

**Fix — persist + rehydrate:**
1. **Persist per-position stop state every tick.** Add two columns to `StraddlePosition`: `high_water_px` and `stop_px`. While a leg is open, the executor/runner writes the current HWM and SL to the row each minute.
2. **On restart, rehydrate before doing anything else.** After `reconcile_on_restart`, query today's `status=open` rows. If any exist:
   - Rebuild the engine's leg state from them: `in_pos=True`, `entry=entry_px`, `hwm=high_water_px`, `sl=stop_px`, `entries=<count today>`, on the **same strikes/sym_ids** as the open rows.
   - **Do NOT re-pick fresh legs and do NOT attempt new entries** when restarting mid-session (the 09:35 arming can't be replayed). The restarted runner's only job is to **manage and exit** what's open (stop / trail / 15:20 square-off).
3. **Manage open positions regardless of sizing.** A client with an open position must always be managed, even if their *new-entry* cash sizing now says "skip" (their cash is tied up in the position — that's expected, not a reason to abandon it).

**Verify:** start a session, open a dry position, `systemctl restart` the runner, confirm the new process logs "rehydrated 1 open position" and continues moving its stop (not "armed fresh legs").

---

## FIX 2 — Single-instance lock (prevent two runners)

**Problem:** Two runner processes ran at once. Nothing stops a duplicate (systemd start, a manual run, or a restart racing the old process).

**Fix:** On startup the runner acquires an **exclusive file lock** (`flock` on `/root/aiprosperity/backend/.straddle_runner.lock`). If the lock is held, the new instance **logs "another runner is live — refusing to start" and exits**. Release on clean exit. This makes it impossible for two runners to manage positions simultaneously.

**Verify:** launch the runner twice; the second must refuse and exit non-zero.

---

## FIX 3 — Independent intraday guardian (the safety net that should have caught this)

**Problem:** The crash-safe square-off only fires at 15:25. The watchdog only *alerts* (read-only). So an orphaned/runaway position bleeds for hours with no automatic action. Today the watchdog correctly alerted "near daily cap" but couldn't act.

**Fix — give the guardian teeth (carefully):** extend the watchdog (or a sibling process) to **force-close** a position when it detects any of:
- **Orphan:** an open broker position whose `StraddlePosition` row exists but **no live runner is managing it** (runner dead, or open >N minutes with the stop not moving), OR
- **Runaway:** open-position unrealized loss beyond a hard limit (see Fix 5), OR
- **Runner dead during market hours** with any open position.

It closes via the same broker-qty `close_position` invariant. Must be **idempotent** and respect the single-owner rule so it can't fight the runner. This is the independent net: even if the runner dies or orphans a position, the guardian flattens it within ~2 minutes.

**Verify:** kill the runner with an open position; confirm the guardian force-closes it within one poll and alerts.

---

## FIX 4 — Hard per-position loss stop (not just a new-entry cap)

**Problem:** `STRADDLE_DAILY_LOSS_CAP_INR` only blocks *new entries* and only counts *realized* loss. An open position's unrealized loss can blow past it (today it hit ~−₹1,300 unrealized with no action).

**Fix:** add a hard **per-position / per-day unrealized loss limit**. When an open position's loss (or day realized+unrealized) crosses it, **force-close immediately** (via the guardian in Fix 3). This is the circuit breaker that actually closes a losing open trade, not just stops opening new ones.

**Verify:** simulate an open position past the limit; confirm it force-closes.

---

## FIX 5 — No deploys / restarts during a live session (operational + enforced)

**Problem:** I changed the engine file mid-session while a live position was open, which is the likely trigger for the restart. That should be impossible.

**Fix (process + guard):**
- **Never deploy to the live path during market hours.** Stage changes; apply only after 15:20 square-off confirms flat.
- **Enforce it:** the deploy/update step checks — if market is open AND a live session is running AND any position is open, it **refuses** (or requires an explicit override flag). Add the same guard to anything that could restart the service.

**Verify:** attempt a deploy during a (paper) live session with an open position; it must refuse.

---

## FIX 6 — Investigate the exact restart trigger

Confirm *what* started the 12:04 process (systemd records show no `Starting` line for it — so it was likely a manual/ad-hoc `python -m app.straddle_runner` or a side effect of a deploy). Pin it down so Fix 2 + Fix 5 fully close it. Add a startup log line recording **how** the runner was launched (systemd vs manual) + the PID + lock status.

---

## ⚠️ v1 SCOPE REVISION (advisor, post-mortem)

The guardian (Fix 3) is a **third thing that can place close orders** (alongside the runner and the 15:20 square-off). Two closers each reading `netQty=65` before fills settle → both sell 65 → **naked short** (broker-qty invariant does NOT save this race). So:

- **Build the ownership primitive FIRST:** the runner writes a **heartbeat** every tick = `{ts, pid, managed:[sym_ids it is actively managing]}`. The guardian force-closes a position **only if** the heartbeat is **stale** OR the sym_id is **not in `managed`**. Live runner managing it → guardian **alerts only, never closes**. This is load-bearing — nothing closes until it's right.
- **DROP Fix 1 (rehydration) from v1.** Once there's a lock + guardian + managed-set, a restart that doesn't *claim* the open position → the guardian flattens it within a poll. **Fail-safe (flatten), not fail-smart (rehydrate).** Rehydration is a v2 P&L optimization for a rare event, shipped only after the net is proven.
- **Endgame note (v2+):** the structurally robust stop is a **broker-resting SL-M** (`tradejini.place_stop_loss`) that survives any process death — has cancel/replace-on-trail + expiry-day costs, so not v1, but it's the real fix for stop robustness.

**v1 order:** (1) lock → (2) heartbeat + managed-set → (3) guardian gated by #2 → (4) deploy-guard + launch logging. Rehydration = v2.

## Order to implement + the gate to resume

1. Fix 2 (lock) + Fix 1 (rehydration) — the core restart-safety.
2. Fix 3 + Fix 4 — the independent guardian + hard loss stop.
3. Fix 5 + Fix 6 — operational guards.
4. **Re-validate with a full DRY-RUN day** (STRADDLE_DRY_RUN=1), including a deliberate `systemctl restart` mid-session to prove the position is rehydrated and managed, and a deliberate runner-kill to prove the guardian force-closes. **Only after a clean dry-run with both fault injections does live resume** — and at proper sizing (₹25k+/lot), not ₹10k/1-lot.

## Standing rules (unchanged)
- `STRADDLE_LIVE=0` until all of the above ship and the fault-injection dry-run passes.
- Broker-qty close invariant (every sell sizes from live netQty) — keep.
- Square-off backstop stays 15:25/15:30 (never 15:20).

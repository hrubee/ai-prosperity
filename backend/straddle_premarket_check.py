"""One-time first-live-run verification after the straddle cutover to the new box.
Runs ~09:40 IST (7 min after the 09:33 launch). Confirms the runner service came
up, the engine is armed/healthy, and Tradejini is reachable — then Telegrams a
single confirmation (or a problem flag). One-shot: the timer does not repeat, so
this adds no ongoing notification noise."""
import os
import subprocess
import sys

HERE = "/root/aiprosperity/backend"
for _l in open(os.path.join(HERE, ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)


def _svc(unit):
    try:
        return subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                              text=True, timeout=8).stdout.strip()
    except Exception:
        return "?"


def main():
    runner = _svc("straddle-shadow.service")
    watchdog = _svc("straddle-watchdog.timer")
    live = os.environ.get("STRADDLE_LIVE", "0")
    dry = os.environ.get("STRADDLE_DRY_RUN", "0")
    mode = "LIVE" if (live == "1" and dry != "1") else ("DRY-RUN" if dry == "1" else "SHADOW")

    # engine + broker snapshot (reuse fno_status)
    try:
        body = subprocess.run([os.path.join(HERE, ".venv/bin/python"),
                               os.path.join(HERE, "fno_status.py")],
                              capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:
        body = "(fno_status error: {})".format(str(e)[:120])

    runner_ok = runner in ("active", "activating")
    icon = "✅" if runner_ok else "⚠️"
    head = "{} NEW-BOX straddle FIRST live-run check\nrunner: {} | watchdog-timer: {} | mode: {}".format(
        icon, runner, watchdog, mode)
    if not runner_ok:
        head = "⚠️ NEW-BOX straddle did NOT launch cleanly — runner={} (expected active). CHECK NOW.\n".format(runner) + head

    try:
        from app import telegram_notify
        telegram_notify.send_message(head + "\n\n" + body)
        print("sent:", head.splitlines()[0])
    except Exception as e:
        print("telegram failed:", str(e)[:160])
        print(head)
        print(body)


if __name__ == "__main__":
    main()

"""Fleet digest (2026-08-06; weekly since 2026-09-26): one email summarizing
the digest window (DIGEST_WINDOW_HOURS, default the last 7 days) of every
store's sync + backfill runs, sent through this repo's normal alert route.
Replaces watching per-run alerts across the fleet's Actions tabs.

Reads the GitHub Actions API for all seven store repos (FLEET_GITHUB_TOKEN)
and parses each completed sync run's summary lines. A store that errors
mid-collection reports the error in its own section - one broken store
never sinks the whole digest.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

import notify

PK_TZ = ZoneInfo("Asia/Karachi")
TOKEN = os.environ["FLEET_GITHUB_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "fleet-digest",
           "Accept": "application/vnd.github+json"}
API = "https://api.github.com"

# (label, owner/repo, tier).
STORES = [
    ("GTV (main)", "gtvgermanalpha-automate/GTV-automate", "full"),
    ("OpenMaal Full", "csopmaal-auto/openmaal-full-automate", "full"),
    ("YRA Full", "yraglobalalpha-automate/yra-full-automate", "full"),
    ("Arden", "arden-auto/arden-onbuy-auto", "semi"),
    ("Arden Full", "arden-auto/arden-full-automate", "full"),
    ("GTV Semi", "gtvgermanalpha-automate/GTV-semi-automate", "semi"),
    ("YRA Semi", "yraglobalalpha-automate/yra-global-onbuy-sync", "semi"),
    ("Makstore Full", "makstore-auto/makstore-automate", "full"),
]
SYNC_WF = "run.yml"
BACKFILL_WF = "backfill_onbuy_status.yml"

# Label-tolerant: matches "161 created", "2 awaiting category (worklist)",
# "4 skipped (dead eBay link)" etc. from BOTH tier formats.
PAIR_RE = re.compile(r"(\d+) ([a-zA-Z][a-zA-Z ()-]*?)(?:,|$)")


def api(path, params=None):
    resp = requests.get(f"{API}{path}", headers=HEADERS, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def job_log(slug, run_id):
    jobs = api(f"/repos/{slug}/actions/runs/{run_id}/jobs").get("jobs", [])
    if not jobs:
        return ""
    resp = requests.get(f"{API}/repos/{slug}/actions/jobs/{jobs[0]['id']}/logs",
                        headers=HEADERS, timeout=60, allow_redirects=True)
    return resp.text if resp.status_code == 200 else ""


def store_section(label, slug, since_iso):
    # A week of runs per repo overflows one page - collect them all (a
    # hard 10-page ceiling keeps a runaway repo from eating the API quota).
    runs, page = [], 1
    while True:
        batch = api(f"/repos/{slug}/actions/runs",
                    {"created": f">{since_iso}", "per_page": "100", "page": str(page)}).get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100 or page >= 10:
            break
        page += 1
    syncs = [r for r in runs if r.get("path", "").endswith(SYNC_WF)]
    backfills = [r for r in runs if r.get("path", "").endswith(BACKFILL_WF)]

    if not syncs and not backfills:
        return f"{label}: no runs in this digest window (paused or schedule dropped)."

    done_syncs = [r for r in syncs if r["status"] == "completed"]
    green = sum(1 for r in done_syncs if r["conclusion"] == "success")
    red = [r for r in done_syncs if r["conclusion"] != "success"]

    totals = {}
    export_fails = 0
    catalog_rows = None
    for r in done_syncs:
        log = job_log(slug, r["id"])
        m = re.search(r"OnBuy: ([^\n]+)", log)
        if m:
            for num, lab in PAIR_RE.findall(m.group(1)):
                lab = lab.strip()
                totals[lab] = totals.get(lab, 0) + int(num)
        if re.search(r"Supabase database export: skipped/failed", log):
            export_fails += 1
        rows = re.search(r"TOTAL ROWS IN SHEET: (\d+)", log)
        if rows:
            catalog_rows = int(rows.group(1))

    bits = [f"{green}/{len(done_syncs)} syncs green"]
    for key in ("created", "updated", "failed", "awaiting category (worklist)",
                "skipped (dead eBay link)", "postponed (transient)",
                "brand-blocked (flagged)"):
        if totals.get(key):
            bits.append(f"{totals[key]} {key}")
    if export_fails:
        bits.append(f"{export_fails} DATABASE EXPORT FAILURES")
    if catalog_rows:
        bits.append(f"catalog {catalog_rows} rows")
    bf_done = [r for r in backfills if r["status"] == "completed"]
    bf_red = sum(1 for r in bf_done if r["conclusion"] != "success")
    if bf_done:
        bits.append(f"backfills {len(bf_done) - bf_red}/{len(bf_done)} green")

    lines = [f"{label}: " + ", ".join(bits) + "."]
    for r in red:
        lines.append(f"  RED sync: {r['html_url']}")
    if bf_red:
        for r in bf_done:
            if r["conclusion"] != "success":
                lines.append(f"  RED backfill: {r['html_url']}")
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc)
    window_h = int(os.getenv("DIGEST_WINDOW_HOURS") or "168")
    since_iso = (now - timedelta(hours=window_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now(PK_TZ).strftime("%A %d %B %Y")

    sections = []
    for label, slug, _tier in STORES:
        try:
            sections.append(store_section(label, slug, since_iso))
        except Exception as exc:  # one broken store must not sink the digest
            sections.append(f"{label}: digest collection FAILED - {exc}")

    span = f"{window_h // 24} day(s)" if window_h % 24 == 0 else f"{window_h}h"
    body = (f"Fleet digest for the {span} up to {datetime.now(PK_TZ).strftime('%H:%M')} PKT, "
            f"{today}.\n\n" + "\n\n".join(sections) +
            "\n\nRed runs need a look; everything else is routine. "
            "Counts come from each run's own summary line.")
    print(body)
    # send_alert_email never raises and returns None by design, so the only
    # failure this job CAN surface is missing SMTP config - check it upfront
    # rather than sending the digest into a logged-but-green void.
    if not (os.getenv("SMTP_USER") and os.getenv("SMTP_APP_PASSWORD")):
        raise SystemExit("SMTP_USER/SMTP_APP_PASSWORD not configured - digest not sent")
    notify.send_alert_email(f"Weekly fleet digest - {today}", body)


if __name__ == "__main__":
    main()

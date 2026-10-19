# Operations runbook

How GymCast runs on the production droplet, and how to check, deploy and
recover it. Recorded on 2026-10-19 from a read-only snapshot of the droplet.
Unit file contents are not in the repository; this page lists what they do.
Where something is inferred rather than observed, it says so.

## Host

| | |
|---|---|
| OS | Ubuntu 24.04 LTS, 961 MB RAM, 24 GB disk |
| Checkout | `/root/gym_predictor` (`main`), venv at `/root/gym_predictor/venv` (Python 3.12) |
| Secrets | `/root/gym_predictor/.env` holds `GOBOARD_API_KEY` |
| Edge | Cloudflare in front of Nginx; TLS certificates from Certbot |

## Services

All GymCast units are in `/etc/systemd/system/`, run as root, and use
`/root/gym_predictor/src` as their working directory.

| Unit | Trigger | Runs | On failure |
|---|---|---|---|
| `gymcast-collector.service` | always running | `python collector.py` (polls every 5 min) | restart after 10 s |
| `gymcast-refresh.timer` → `.service` | 2 min after boot, then 15 min after each run | `python features.py`, then `python predict.py` | next timer run |
| `gymcast-train.timer` → `.service` | daily 03:00 America/New_York, catches up if missed | `python train.py` | next night |
| `gymcast-api.service` | always running | `gunicorn --workers 2 serve:app`, bound to `127.0.0.1:8000` (inferred from the Nginx upstream) | restart after 5 s |

Root's crontab also runs `health_check.sh` every 15 minutes. That script is
not in the repository; it is probably the one untracked file in the checkout.

Nothing runs `evaluate.py` on a schedule. The deployed
`outputs/evaluation_predictions.json`, which feeds the "How GymCast performs"
chart, dates from 2026-10-07 and does not track the nightly model.

## Request routing (Nginx site `gymcast`)

- `/` serves the static frontend from `/var/www/gymcast`, falling back to
  `index.html`.
- `/api/refresh` returns 403. The refresh timer runs the pipeline directly, so
  the endpoint is not needed in production.
- `/api/` proxies to Gunicorn on `127.0.0.1:8000` with `Host`, `X-Real-IP`,
  `X-Forwarded-For` and `X-Forwarded-Proto` headers.
- Port 80 redirects to HTTPS.

## Everyday checks

```bash
systemctl list-timers 'gymcast-*'                 # next and last refresh/train runs
systemctl status gymcast-collector gymcast-api    # long-running services
journalctl -u gymcast-refresh -n 20 --no-pager    # last refresh output
ls -l /root/gym_predictor/outputs/predictions.json  # should be under ~20 min old
```

`predictions.json` older than about 20 minutes means the refresh job is failing.
A `features.csv` noticeably newer than `predictions.json` means `predict.py` is
the failing step.

## Deploying

The frontend and backend deploy separately. Both use the checked-out commit.

```bash
cd /root/gym_predictor
git pull --ff-only
venv/bin/python -m pip install -r requirements.txt   # only when pins changed

# Backend: long-running services load code at start, so restart them.
# Timer-driven jobs pick up new code on their next run.
systemctl restart gymcast-collector gymcast-api

# Frontend: copies web/ to /var/www/gymcast with cache-busting versions,
# after backing up the current site to /root/gymcast-backups/.
./deploy_frontend.sh
```

To roll back the frontend, extract the newest archive in
`/root/gymcast-backups/` into `/var/www/gymcast`. To roll back the backend,
`git checkout <previous commit>` and restart the same two services.

## Manual runs

```bash
cd /root/gym_predictor/src
systemctl start gymcast-refresh.service   # refresh now, with the unit's settings
systemctl start gymcast-train.service     # retrain now
../venv/bin/python evaluate.py --report-out ../outputs/eval.json   # updates the accuracy chart
```

`evaluate.py` refits a model for each fold; on this 1 GB host, run it when the
train job is not running.

## Known risks

- **Leaked API key.** Before the collector fix on branch
  `refactor/engineering-review`, failed polls logged the full request URL.
  `data/poll_errors.csv` held 89 rows containing the key on 2026-10-19.
  Rotate the key, then remove those rows or the file.
- **Everything runs as root.** A flaw in the Flask app or a dependency would run
  with root privileges. A dedicated user that owns `data/`, `models/` and
  `outputs/` would contain that.
- **No memory limits.** The API, refresh and train jobs share 961 MB and no
  unit sets `MemoryMax`. If the train job and a refresh overlap, the kernel's
  out-of-memory killer picks the victim.
- **`.env` is world-readable (mode 644).** `/root` is normally `700`, which
  keeps other users out, but `chmod 600 .env` removes the dependency on that.

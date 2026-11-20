# Sim Lab

Sim Lab is a single-user DraftKings NFL lab. Import a slate, edit a player pool, build classic or Showdown lineups, and run correlated simulations in the background. It is a model for ranking lineups. It does not enter contests, and the sim ROI it reports is not real contest ROI.

## What it uses

- [nflreadpy](https://github.com/nflverse/nflreadpy) for schedules, play-by-play, injuries, and snap counts.
- [pydfs-lineup-optimizer 3.6.1](https://github.com/DimaKudosh/pydfs-lineup-optimizer) for DraftKings classic and Showdown optimization. That is the last release (it is pinned, and it pins `PuLP==2.4`). Captain mode only knows CPT and FLEX, and its ownership rule is an average rather than a cumulative total, so Sim Lab adds `OptimizerRule` hooks and `PlayersGroup` exposure caps on top. The deprecated `randomness=True` flag is not used.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` before you start the app. Set a long `SIMLAB_SECRET_KEY`, your email, and a password. Nothing in `.env` belongs in git. The example file only has placeholders.

```bash
uvicorn simlab.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and sign in with `SIMLAB_EMAIL` / `SIMLAB_PASSWORD`.

Password login is on when `SIMLAB_PASSWORD` is set. The password is hashed with PBKDF2 at startup and is not stored in the repo. Leave the password empty to force magic links.

Magic links: set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` to send mail. For local use without SMTP, set `SIMLAB_DEV_EXPOSE_MAGIC_LINK=true` and the API returns the link. Leave that flag false on any host other people can reach. Set `SIMLAB_COOKIE_SECURE=true` behind HTTPS, and set `SIMLAB_BASE_URL` to the public origin (no trailing slash) so email links point at the right place.

```bash
pytest
```

## Try the IND@HOU sample

`sample_data/dksal.csv` is a DraftKings Showdown salary file. `sample_data/rgp_projections.csv` is the projection file (`Name`, `Salary`, `Team`, `Opponent`, `Position`, `Points`, `Ownership`, `CPT Ownership`).

1. Sign in.
2. Upload both CSVs, or click **Load IND@HOU sample**.
3. On the pool, cut Xavier Hutchinson, Seth McGowan, and Tyler Warren. DK `OUT` / `IR` rows are already badged.
4. On Lineups, check captains C.J. Stroud, Jonathan Taylor, Nico Collins, and Tank Dell. Defaults are a QB stack, a bring-back, no two pass catchers without their QB, salary at least $46,000, cumulative ownership 160–210%, at most 4 shared players, and at most 2 lineups per captain.
5. Generate 4 lineups. Each card shows salary, projection, and cumulative ownership. **Export DK CSV** writes captain and flex player IDs.
6. On Sims, run 10,000 sims. The page stays usable while the job runs. Results are labeled as model output: top 0.1%, top 1%, ITM, and sim ROI, plus captain script frequencies.

**Pull injuries and snaps** asks nflreadpy for that season's schedule, injuries, snap counts, and play-by-play pace. If the season is not published yet, the DK status flags stay as they are.

You can edit a lineup by swapping any slot. The editor enforces a legal roster, the $50,000 cap, and at least two teams. Stack rules are not reapplied to a hand edit.

## How the sim works

Showdown enumerates legal captain + 5 flex lineups (salary cap, both teams) inside a pool of the highest projections plus anyone already in your lineups, then solves the optimal lineup in every sim. Classic slates are too large to enumerate, so the optimal is the best optimizer candidate in that sim and the result says so.

Scores share a game-script factor. A quarterback moves with his pass catchers. Running backs move with team scoring and against pass rate. A defense moves against the opposing offense. Cheap pass catchers have a fatter tail and a bust chance.

The field is optimizer-style: captains are drawn from captain ownership, projections are noised, flex weights are nudged toward flex ownership, and about 85% of the field must spend at least $49,000. A duplicate is that exact lineup showing up in the field, scaled to the contest. Ownership percentages are never multiplied together.

Contest size, entry fee, max entries, cash rate, rake, and an optional payout table (`1-1:10000`) are inputs. Blank payouts use a top-heavy curve. Sim ROI splits a prize with the expected number of exact copies. It is a ranking model, not a forecast of profit.

## Deploy

The container listens on port 8000 and stores SQLite at `SIMLAB_DATABASE_URL` (default `sqlite:////data/simlab.db`). Put that file on a persistent disk. Set the env vars from `.env.example` in the host's secret store. Do not bake a `.env` into the image.

```bash
docker compose up --build
```

Render: use `render.yaml` (Docker runtime, health check `/api/health`, a 1 GB disk at `/data`). Fill `SIMLAB_EMAIL`, `SIMLAB_PASSWORD`, and `SIMLAB_BASE_URL` in the dashboard. `SIMLAB_SECRET_KEY` is generated.

Railway: new project from this repo, Dockerfile build, mount a volume at `/data`, set the same env vars, and point `SIMLAB_BASE_URL` at the Railway domain.

Fly: `fly launch --no-deploy`, `fly volumes create simlab_data --size 1`, set secrets (`fly secrets set SIMLAB_SECRET_KEY=... SIMLAB_EMAIL=... SIMLAB_PASSWORD=... SIMLAB_BASE_URL=https://your-app.fly.dev`), then `fly deploy`. `fly.toml` is a starting point; rename the app first.

Keep `SIMLAB_DEV_EXPOSE_MAGIC_LINK` false in every deployed environment.

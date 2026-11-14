# Staging deployment (port 8001)

Runs the exact same code as production (`webapp/docker-compose.yml`, port
8000), but as a fully separate Docker Compose project — its own container,
its own data folder (`webapp/data-staging/`) — so nothing you do on staging
can touch the real `webapp/data/mahjong.db`, and vice versa.

## One-time setup

From the OMV machine, in the repo root:

```bash
mkdir -p webapp/data-staging
cp webapp/data/mahjong.db webapp/data-staging/mahjong.db
```

This seeds staging with a snapshot of the real data. After this, the two
copies are independent — scraping on staging (or running a correction
script there) never writes back to the real DB, and new games scraped on
production never show up on staging.

## Start / rebuild staging

```bash
cd webapp
docker compose -f docker-compose.staging.yml up -d --build
```

Open it at `http://<omv-ip>:8001`.

Because `docker-compose.staging.yml` declares its own Compose project name
(`mahjong-staging`), none of these commands ever interact with the
production container — you can rebuild, stop, or tear down staging freely
without any risk to the port-8000 deployment running from
`docker-compose.yml`.

## Stop staging

```bash
cd webapp
docker compose -f docker-compose.staging.yml down
```

(Leaves `data-staging/` on disk — only removes the container.)

## Refresh staging's data from production

Repeat the one-time setup's copy step (stop staging first, so nothing is
mid-write while the file is replaced):

```bash
cd webapp
docker compose -f docker-compose.staging.yml down
cp data/mahjong.db data-staging/mahjong.db
docker compose -f docker-compose.staging.yml up -d
```

## Typical workflow for testing a change before it goes live

```bash
cd webapp
docker compose -f docker-compose.staging.yml build
docker compose -f docker-compose.staging.yml up -d
docker compose -f docker-compose.staging.yml logs -f --tail 50
```

Check it at port 8001. Once you're happy, roll the same change out to
production as usual (see [DEPLOY_UPDATE.md](DEPLOY_UPDATE.md)):

```bash
docker compose build
docker compose up -d
```

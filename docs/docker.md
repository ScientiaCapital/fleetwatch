# Running with Docker

```bash
git clone https://github.com/ScientiaCapital/fleetwatch.git && cd fleetwatch
cp .env.example .env
docker compose run --rm fleetwatch login   # paste the redirect URL when asked
docker compose up -d
docker compose logs -f
```

The container runs as a non-root user with a read-only filesystem, all capabilities dropped, and no open port.
`docker compose ps` shows the container as healthy while heartbeats are landing. It turns unhealthy when no
heartbeat has read the fleet for three intervals, for example after the sign-in expires.

The token and state live on the `fleetwatch-state` volume. `policy.yaml` is mounted read-only from the repo, so
edit it on the host and run `docker compose restart`.

Images for `linux/amd64` and `linux/arm64` are published to `ghcr.io/scientiacapital/fleetwatch` for each
release, with build provenance. Until the first release, `docker compose` builds the image locally.

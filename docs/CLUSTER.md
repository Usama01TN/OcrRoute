# Cluster sync

Run several OcrRoute servers with one configuration. One server is the **leader**: you change configuration there.
Every other server is a **follower**: it mirrors the leader and stays read-only for configuration.

## What is synchronised

Providers, credentials, routes (with their members), client API keys, panel users, runtime settings, and which engines
are enabled. A client API key created on the leader works on every follower, so a load balancer can send any
request to any server.

Per server, never synchronised: OCR runs, usage and costs, the result cache, jobs, logs, provider health and circuit
breakers, credential usage counters and quota state, "last used" / "last login" timestamps, and each server's own
address, port and sync settings.

## Set up from the dashboard (recommended)

On each server, open the web panel, go to **System > Cluster sync**, pick a role and click **Save and apply**. It takes
effect immediately, without a restart.

1. **Leader** (e.g. `ocr-1`): choose **Leader** and save. A token is generated: click **Copy**. The page also lists the
   addresses followers should use (e.g. `http://192.168.1.10:20256`).
2. **Each follower**: choose **Follower**, paste the leader address and the token, click **Test connection** (it shows
   how many providers, routes and keys the leader shares), then **Save and apply**. The status card shows the last sync,
   any error and what was copied; **Sync now** forces a sync.

Two things the dashboard cannot change, because they apply when the server starts:
- The leader must listen on the network: set `OCRROUTE_HOST=0.0.0.0` in its `.env`, restart, and allow port 20256 in
  the firewall. The page warns when the server only listens on `127.0.0.1`.
- After the first sync a follower uses the leader's API keys and panel users: sign in with an account from the leader.

## Set up with .env (alternative)

`.env` / environment variables take priority over the dashboard: when they set sync, the Cluster sync page is
read-only.

```bash
ocrroute sync token                      # generate one shared token, e.g. on the leader
```

Leader (e.g. `ocr-1`):

```bash
OCRROUTE_SYNC_ROLE=leader
OCRROUTE_SYNC_TOKEN=<token>
```

Each follower:

```bash
OCRROUTE_SYNC_ROLE=follower
OCRROUTE_SYNC_TOKEN=<same token>
OCRROUTE_SYNC_LEADER_URL=https://ocr-1.example.com
OCRROUTE_SYNC_INTERVAL_SECONDS=30        # optional, default 30
```

Put them in the environment or in `.env`, then (re)start the servers. Check a server with
`ocrroute sync status --url http://host:20256 --key <admin key>` or `GET /v1/sync/status`; force a sync on a follower
with `POST /v1/sync/now`.

## How it works

- Followers poll `GET /v1/sync/snapshot` on the leader with the header `X-OcrRoute-Sync-Token`. An unchanged
  configuration answers `304 Not Modified` (ETag digest), so polling is cheap. Failed polls back off (up to 8x).
- A snapshot is applied in one transaction and mirrored exactly: rows removed on the leader are removed on followers.
- **Secrets**: the leader decrypts credentials with its own master key and re-encrypts them for transport with a key
  derived from the sync token; each follower re-encrypts them with its own master key. No server ever stores another
  server's master key. The token is the only shared secret: keep it private, and serve the leader over HTTPS
  (reverse proxy or tunnel) when servers talk across an untrusted network.
- **Followers are read-only for configuration**: `POST`/`PATCH`/`DELETE` on providers, credentials, routes, keys,
  users and settings answer `409` with the leader's address. Edits would otherwise be overwritten at the next sync.
- Followers do not auto-create providers for their engines; they use the leader's.
- A provider whose engine is not installed on a follower is skipped there (reported in the sync status), for example
  a Full-edition engine on a Lean-edition follower.

## Promoting a follower

If the leader is lost, set `OCRROUTE_SYNC_ROLE=leader` on one follower (it already holds the full configuration) and
point the other followers' `OCRROUTE_SYNC_LEADER_URL` at it.

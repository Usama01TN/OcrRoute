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

## Many servers behind tunnels (ngrok, Cloudflare, Tailscale...)

Only the **leader** needs a public address: followers connect *out* to it, nothing connects in to a follower, so the
tunnels on the other servers serve OCR clients and play no part in sync. Behind a tunnel the leader needs neither
`OCRROUTE_HOST=0.0.0.0` nor a firewall rule (the tunnel forwards to `127.0.0.1` on the same machine).

- On the leader, the Cluster sync page lists its addresses best first: running tunnels, the manual public URL, then
  LAN. Give followers the first one, as `https://...`.
- **Use a stable address for the leader.** Cloudflare *quick* tunnels (`*.trycloudflare.com`) and free ngrok URLs
  (`*.ngrok-free.app`) change on every restart; the page flags them. Prefer a Tailscale name (`*.ts.net`, private to
  your machines), a Cloudflare named tunnel on your own domain, or an ngrok static domain. If the leader's URL does
  change, update the leader address on every follower (**Test connection** confirms the new one).
- The leader's page lists every **follower** (name, address, version, last contact, up to date or behind), so all
  servers are visible in one place.
- The token is the only secret: anyone with it can read the leader's configuration through the public URL, so keep it
  private and regenerate it if it leaks. A Cloudflare Access login in front of the leader blocks followers too:
  exclude `/v1/sync/*` from that policy, or sync over Tailscale.

## Server cards (leader)

On the leader, **Cluster sync > Servers** shows one card per follower: name, host, address, version, last contact and
status (up to date, behind, paused, offline after 5 minutes without contact, never connected).

- **Add server** creates a card with **its own token**, shown once with the leader address to paste on that server.
  Only a hash of the token is stored.
- **Edit** renames the card and sets notes. **Pause / Resume** stops or restarts sending configuration to that server
  (it reports "paused" in its status). **New token** replaces a leaked token (the server stops syncing until it gets
  the new one). **Delete** revokes the server: its own token stops working, and a server that used the shared token
  is refused by its server id; add a new card to let it back in.
- Servers using the shared token appear as cards automatically. Each server's secrets travel encrypted with the token
  it uses, so revoking one server never exposes the others.
- API: `GET/POST /v1/sync/nodes`, `PATCH/DELETE /v1/sync/nodes/{key}`.

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

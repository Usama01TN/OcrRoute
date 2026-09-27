# Cluster

Several OcrRoute servers can share one configuration, the way Proxmox or Docker Swarm clusters work: one server is the
**primary**, the others **join** it with a **join code**.

## How it works

1. On the server that will hold the configuration: **Cluster > Create a cluster**. It becomes the **primary**.
2. On the primary: **Add servers** (one or many at once). Each server gets its own **join code**: one string with the
   primary's addresses and that server's secret.
3. On each other server: **Cluster > Join a cluster**, paste its code, **Join cluster**. Nothing else to type.
4. From then on you edit providers, credentials, routes, API keys and users **on the primary only**; every member copies
   them within seconds and is read-only for configuration. OCR history, usage, cache, logs and each server's own address
   stay per server.

Every server shows the member list with each member's status. On the primary each member card has **Edit** (name, notes)
and **Remove**, plus **Show join code**, **New join code** and **Pause**. A member can **Leave cluster**; the primary
can **Delete cluster**. Leaving or being removed keeps the configuration the server already has.

After joining, a member uses the primary's API keys and panel users: sign in with an account from the primary.

## Addresses

The join code carries every address of the primary, best first: running tunnels (Endpoints page), its public URL, then
LAN addresses. Members fail over between them, and the primary tells members when its address changes (for example a
Cloudflare quick tunnel that restarted with a new name). A stable primary address (Tailscale `*.ts.net`, a Cloudflare
named tunnel, an ngrok static domain) remains the most reliable. The primary does not need `OCRROUTE_HOST=0.0.0.0`
behind a tunnel; without a tunnel, other computers need it (and the port open in the firewall).

## Security

- A join code contains a secret: send it privately. **New join code** replaces it; **Remove** revokes it.
- Before a member sends its secret to an address, the primary must prove it knows that secret (challenge / response),
  so an address announced by someone else, or a typo, never leaks it.
- Credentials travel encrypted with the member's own secret and are re-encrypted with each server's own master key.
- Serve the primary over HTTPS (tunnel or reverse proxy) when servers talk across the internet.

## Automation (.env)

`OCRROUTE_SYNC_ROLE=leader|follower`, `OCRROUTE_SYNC_TOKEN`, `OCRROUTE_SYNC_LEADER_URL` (comma-separated addresses) and
`OCRROUTE_SYNC_INTERVAL_SECONDS` still work and take priority (the Cluster page is then read-only).
API: `GET /v1/cluster`, `POST /v1/cluster/create | join | leave | delete`, `POST /v1/sync/nodes/bulk` (returns join
codes), `PATCH / DELETE /v1/sync/nodes/{key}`, `GET /v1/cluster/members/{key}/join-code`.

## When the leader's address changes

Cloudflare quick tunnels (`*.trycloudflare.com`) and free ngrok URLs get a new random name every time the tunnel
restarts; the old name disappears from DNS (a follower then fails with "no longer exists"). OcrRoute handles this:

- **Push:** each follower tells the leader how to reach it (its tunnel / public URL). When the leader's own address
  changes, it sends the new one to every follower within about 20 seconds (`OCRROUTE_SYNC_WATCH_SECONDS`). **Notify
  followers** on the leader's page sends it immediately. Each server card shows where the follower is reachable and the
  result of the last notice.
- **Failover:** followers remember every address of the leader (tunnels, public URL, LAN) and try the others when one
  stops working; a verified address learned this way is added to the follower's address list.
- **Safe by design:** a pushed or learned address is never trusted blindly. Before sending its token to a new address,
  the follower asks that server to prove it knows the token (challenge / response: an HMAC of a random nonce), and that
  it is the same leader as before. An impostor learns nothing.
- Errors are explained in plain language, and each distinct problem is logged once (then every 10 minutes).

A stable leader address (Tailscale `*.ts.net`, a Cloudflare named tunnel, an ngrok static domain) remains the most
reliable setup: the push needs followers to be reachable, and failover needs another working address.

Per-server settings are never synchronised: each server's own **public URL** (Endpoints page) and its sync state.

## Server cards (leader)

On the leader, **Cluster sync > Servers** shows one card per follower: name, host, address, version, last contact and
status (up to date, behind, paused, offline after 5 minutes without contact, never connected).

- **Add followers** adds one or many at once: one row per follower (name, optional address, optional notes), **Add
  row** for more (or press Enter). Each gets a card with **its own token**, all shown once with the leader address and a
  **Copy all** button. The optional address is where the follower is reachable, so the leader can announce address
  changes before it first connects. API: `POST /v1/sync/nodes/bulk` (`{"items": [{"label", "address", "notes"}]}`).
- Every card has **Edit** (name, address, notes, edited inside the card) and **Delete** (confirmed inside the card),
  plus Pause / Resume and New token.
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

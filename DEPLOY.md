# Deploying Dovetail on borant

Port **8021**. Not 8015: that is GrantRadar's, checked on the box on 28 Aug 2026.
Everything from 8010 to 8020 is taken; 8021 was the first free one.
`dovetail.borant.eu` already resolves.

## 1. The four ways a borant MCP deploy breaks

They are all handled in the code, and they are listed here because each one fails
quietly and none of them looks like the others.

1. **The session manager must run in a `lifespan`.** In a startup event the MCP
   transport answers 500 with no explanation. See `web.py`.
2. **`/mcp` has to be in Caddy's `@pubbliche` list.** A model client has no
   browser and no cookie, so if the Borant ID gate covers it, every call is a
   redirect to a login page the client cannot render.
3. **`PUBLIC_URL` must be the real public address.** The transport checks Host
   and Origin against it and refuses anything else.
4. **The trailing slash.** The app is mounted at `/mcp` with
   `streamable_http_path="/"`, so the client URL is `https://dovetail.borant.eu/mcp`
   and the mount handles the rest.

## 2. On the box

```bash
mkdir -p /opt/apps/dovetail && cd /opt/apps/dovetail
git clone https://github.com/that-ugly-cat/dovetail.git .
cp .env.example .env && nano .env      # JWT_SECRET, PUBLIC_URL, OPENALEX_API_KEY
docker compose up -d --build
docker compose logs -f app             # `alembic upgrade head` runs first
curl -s localhost:8021/healthz         # {"ok":true}
```

`BORANT_TRUSTED_PROXY` under Docker is the **bridge gateway**, not 127.0.0.1.
Read the real value off the running container rather than assuming:

```bash
docker inspect dovetail -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'
```

Get that wrong and gateway mode silently ignores every identity header, which
looks exactly like «Borant ID is broken».

## 3. Caddy

Same shape as the other apps. `noforge` strips any `X-Borant-*` a client tried to
send before the gate puts the real ones back, which is why the app's own
trusted-proxy check is the second layer and not the first.

```
dovetail.borant.eu {
    @pubbliche path / /healthz /static/* /mcp /mcp/* /login /logout
    handle @pubbliche {
        import noforge
        import nocookie
        reverse_proxy localhost:8021
    }
    handle {
        import borantid
        reverse_proxy localhost:8021
    }
}
```

Then `caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy`.

Note what `@pubbliche` means for `/mcp`: it is **outside** the Borant ID gate, so
it is protected by its own per-user API key and by nothing else. That middleware
is the only thing between the open internet and a surface that spends the
OpenAlex budget.

## 4. First users

The web UI has no sign-up. Create at least one admin, then hand out keys:

```bash
docker exec -it dovetail python -m dovetail.cli create-user \
    --email you@example.org --role admin
docker exec -it dovetail python -m dovetail.cli api-key \
    --email you@example.org --label "ono desktop"
```

The key is shown once. Only a hash is stored, so nobody — including whoever runs
the server — can print it again.

## 5. Switching Borant ID on

Set `AUTH_MODE=gateway`, set `BORANT_TRUSTED_PROXY`, restart.

People who already had a local account will arrive as a **new profile**: the app
refuses to match a gate subject to a local user by email, because that is how one
person ends up with another person's account. Link them once, by hand:

```bash
docker exec -it dovetail python map_borant.py                      # see both
docker exec -it dovetail python map_borant.py you@example.org sub-abc123
```

New people the gate vouches for arrive as **readers**, which is harmless by
construction: a reader cannot spend credits and cannot approve anything, so an
unknown subject costs one row and a read-only screen.

## 6. Before touching the database

The mounted folder is owned by root, so `cp` from `spit` fails. Back up from
inside the container, and with `sqlite3.backup` rather than `cp`, because the WAL
can be hundreds of kilobytes behind:

```bash
docker exec dovetail python -c "
import sqlite3, time
src = sqlite3.connect('/app/data/dovetail.db')
dst = sqlite3.connect(f'/app/data/dovetail.db.bak-{int(time.time())}')
src.backup(dst); dst.close(); src.close()"
```

The path inside the container is `/app/data/`, not `/data/`.

## 7. What is not deployed

`venue_history` is not implemented: it would read PaperTrail, which needs a key
server-side. That is a decision about what this box may reach, not a coding task,
and it is deliberately still open.

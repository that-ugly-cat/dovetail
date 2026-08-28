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

Validate on a **copy** and touch the live file only if it passes. On a box
serving two dozen sites that is the difference between a harmless mistake and
Caddy refusing to come back up:

```bash
sudo bash -c 'cp /etc/caddy/Caddyfile /tmp/cf.new   && cat /opt/apps/dovetail/caddy-block.txt >> /tmp/cf.new   && caddy validate --adapter caddyfile --config /tmp/cf.new   && cp /tmp/cf.new /etc/caddy/Caddyfile   && systemctl reload caddy && echo OK'
```

`--adapter caddyfile` is not optional: without it `validate` assumes JSON and
fails on the first `#` with a message about JSON that has nothing to do with what
is wrong.

Note what `@pubbliche` means for `/mcp`: it is **outside** the Borant ID gate, so
it is protected by its own per-user API key and by nothing else. That middleware
is the only thing between the open internet and a surface that spends the
OpenAlex budget.

**The block above did not change when the app moved to `/app`, and that is worth
checking rather than trusting.** `path /` matches the bare root exactly, so
everything under `/app` falls to the second `handle` and is gated — which is what
the old `/venues`, `/runs` and `/proposals` did too. The public list is the list
of paths where **no method needs to know who is asking**, and each of these still
qualifies: `/` is a front page that never consults the user, `/login` cannot
learn who is asking anyway, `/logout` only deletes a cookie, `/static/*` serves
files, and `/mcp` carries its own key.

Prove it from outside after deploying, because the failure is silent in the safe
direction and invisible in the dangerous one:

```bash
curl -sI https://dovetail.borant.eu/ | head -1              # 200, no gate
curl -sI https://dovetail.borant.eu/app | head -1           # 302 to id.borant.eu
curl -s  https://dovetail.borant.eu/app | grep -c borant    # the gate's page, not ours
```

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

## 7. The schema moves with the code

`docker compose up -d --build` runs `alembic upgrade head` before the app starts,
so an ordinary deploy carries its own migrations. The one added on 28 Aug 2026
puts `status`, `error_code`, `error_detail` and `finished_at` on `match_run`, and
stamps every existing row `done` — every run recorded before it came from the
CLI, where the caller waited for it.

It matters because a consultation started from the web answers **before** it has
finished: the sweep is a hundred-odd calls and the better part of a minute, so
the row is committed as `running`, the browser is sent to it, and the work
continues off the request. Without that column a run still going and a run whose
process died look identical, and they want opposite reactions.

If the app comes up before the migration has run, the symptom is `no such column:
match_run.status` on every page that lists runs. Check with:

```bash
docker exec dovetail python -m dovetail.cli init-db
```

## 8. Turning on stage 5a

Stage 5a asks a model whether a journal publishes work of the same *kind* as a
manuscript. It needs two things, and neither is a credential of this machine's.

Set `FERNET_KEY` in `.env` and restart. That is the server's half: it lets the
app store people's own Anthropic keys encrypted. Generate one with

```bash
docker exec dovetail python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The other half is each person storing their key at `/app/settings`. A judgement
bills the account of whoever pressed the button, and **this box holds no
Anthropic credential of its own** — the same question left open for
`venue_history`, settled the other way here because there is a place to put the
key that belongs to a person rather than to the server.

Losing `FERNET_KEY` means every stored key becomes unreadable and has to be
entered again. Nothing else breaks: stages 1 to 4 never touch it.

## 9. What is not deployed

`venue_history` is not implemented: it would read PaperTrail, which needs a key
server-side. That is a decision about what this box may reach, not a coding task,
and it is deliberately still open.

# Challenge 3: Multi-Container Service Registry & Chaos Lifecycle Daemon

## What You Must Build

Transform a hardcoded 3-tier web app into a **dynamic service-discovery architecture**
with a **registry daemon**, **heartbeat sidecars**, and **chaos resilience** —
then prove it survives container kills with zero dropped requests.

---

## Step 1: The Starting Context

The repo contains a functional but hardcoded 3-tier app:

```
app.py              — Web API (http.server) with hardcoded REDIS_HOST/POSTGRES_HOST
docker-compose.yml  — 3 services: api, redis, postgres
Dockerfile          — Builds app.py into a container
```

The API has these endpoints:
- `GET /health` — returns `{"status": "ok"}`
- `GET /items` — list all items (cache-first: Redis → Postgres)
- `GET /items/<id>` — get one item
- `POST /items` — create item (writes Postgres, invalidates Redis cache)

Currently, Redis and Postgres connections use hardcoded env vars. The app has no
way to handle Redis going down mid-request.

---

## Step 2: Files to Create/Modify

### Phase A: Deconstruct Hardcoding

**Modify `app.py`:**
- Replace `REDIS_HOST` / `REDIS_PORT` with `get_service_address("redis")`
- Replace `POSTGRES_HOST` / `POSTGRES_PORT` with `get_service_address("postgres")`
- `get_service_address(name)` queries the local registry daemon via HTTP:
  `GET http://registry:9000/services/{name}` → `{"host": "...", "port": ..., "status": "healthy"}`
- Cache the address for 5 seconds locally (no point hitting registry on every call)
- On connection failure, call `get_service_address(name)` again (bypass cache) to
  check if the service moved

### Phase B: Service Registry Daemon

**Create `registry.py`:**
- HTTP server on port 9000 (http.server, stdlib only)
- In-memory service table: `{name: {host, port, status, last_heartbeat, registered_at}}`
- Endpoints:
  - `POST /register` — body `{"name": "redis", "host": "...", "port": 6379}` → register or refresh
  - `GET /services/<name>` — returns `{"host": "...", "port": ..., "status": "healthy|degraded|offline"}`
  - `GET /services` — list all registered services
  - `DELETE /services/<name>` — manual deregistration
- Auto-deregistration: if no heartbeat for 10 seconds, mark status="offline"
- Purge thread: runs every 2 seconds, checks `last_heartbeat` age

### Phase C: Sidecar Heartbeat Scripts

**Create `sidecar.py`:**
- Runs alongside every app container (started before app.py)
- Reads `SERVICE_NAME`, `SERVICE_HOST`, `SERVICE_PORT` env vars
- Registers with registry daemon: `POST /register` with service info
- Sends heartbeat every 2 seconds: `POST /register` (same endpoint, updates last_heartbeat)
- On SIGTERM: send `DELETE /services/<name>` and exit cleanly
- Runs as a lightweight thread alongside app.py, both in the same container

**Modify `docker-compose.yml`:**
- Add `registry` service
- Add `SERVICE_NAME`, `SERVICE_HOST`, `SERVICE_PORT` env vars to `api` service
- Add a `depends_on: registry` to `api`
- Remove `depends_on: redis postgres` from `api` (registry handles discovery)
- Add sidecar invocation: change CMD to start sidecar + app together

### Phase D: In-Memory Fallback (Chaos Resilience)

**Create `fallback.py`:**
- `FallbackCache` class — thread-safe in-memory dict
- When Redis is unreachable (ConnectionError from RedisClient):
  1. Log warning to stderr
  2. Read from Postgres directly (bypass cache)
  3. Write response to `FallbackCache` with 30-second TTL
  4. Flag the registry that Redis is degraded (optional health signal)
- When Redis comes back: flush FallbackCache writes to Redis, resume normal path

### Phase E: Chaos Test Runner

**Create `chaos_test.py`:**
- Launches docker-compose (if not running)
- Waits for all services to register (poll `GET http://localhost:9000/services`)
- Phase 1: warmup — 100 sequential requests to populate data
- Phase 2: steady state — 200 concurrent requests, measure success rate
- Phase 3: CHAOS — `docker stop <redis_container>` mid-stream
- Phase 4: recovery — 200 more concurrent requests while Redis is down
- Phase 5: restore — `docker start <redis_container>`, wait for re-registration, 200 requests
- Metrics collected: total requests, successes, failures, latency p50/p99
- **Success criteria: zero dropped requests across all 5 phases**

### `test_registry.py`
Pytest suite:
1. **test_register_and_discover** — register a service, query it back
2. **test_heartbeat_keeps_alive** — register, wait 5 heartbeat cycles, verify still healthy
3. **test_auto_deregister_on_no_heartbeat** — register, stop heartbeats, wait 12 seconds, verify status=offline
4. **test_multiple_services** — register 3 services, list all, verify all present
5. **test_app_fallback_on_redis_down** — make Redis unreachable, send GET /items, verify response still works (reads Postgres directly)
6. **test_concurrent_registrations** — 10 concurrent registration requests, verify no corruption
7. **test_registry_daemon_restart** — register services, kill registry, restart, verify services can re-register

---

## Step 3: Architecture Diagram

```
┌─────────┐    heartbeat/2s     ┌──────────────┐
│ sidecar │ ──────────────────► │   registry   │
│  (api)  │ ◄── GET /services   │   :9000      │
└────┬────┘                     └──────────────┘
     │                                ▲
     │ app.py                         │ heartbeat
     │                                │
     ▼                                │
┌─────────┐                      ┌─────────┐
│  redis  │                      │postgres │
│  :6379  │                      │  :5432  │
└─────────┘                      └─────────┘
```

---

## Step 4: Constraints

- **Zero external Python packages** beyond stdlib (except `redis` and `psycopg2` for
  real connections — but the challenge uses simulated clients)
- Docker and docker-compose must be installed on the test machine
- All service communication via HTTP (no gRPC, no custom protocols)
- Sidecar must handle SIGTERM for clean shutdown
- Registry must be thread-safe (use `threading.Lock`)
- Chaos test must not use `time.sleep(5)` — use polling with timeouts

---

## Step 5: Running

```bash
# Start everything
docker-compose up -d

# Run chaos test
python3 chaos_test.py

# Run unit tests
python3 -m pytest test_registry.py -v
```

---

## Step 6: Success Criteria

| Criterion | Verification |
|-----------|-------------|
| Dynamic discovery | app.py uses get_service_address(), not hardcoded env vars |
| Heartbeat lifecycle | Services auto-register, stay alive, deregister on stop |
| Chaos survival | `docker stop redis` mid-workload → zero dropped requests |
| Fallback works | GET /items returns data even when Redis is down |
| Concurrent-safe | 10+ concurrent registrations with no data loss |
| Clean shutdown | SIGTERM triggers deregistration |

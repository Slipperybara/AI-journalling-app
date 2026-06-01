# Self-hosted Neo4j on a small VPS

Runbook for provisioning the cheapest viable Neo4j host for MindForge AI's
multi-tenant graph layer. Target: Hetzner CX22 (€4.51/mo, 4 GB RAM, 2 vCPU)
or Oracle Cloud Free Tier ARM box (free forever, 24 GB RAM).

The Postgres-as-source-of-truth invariant means the graph is fully
rebuildable from Postgres via `python -m app.graph_rebuild --all`, so this
machine is treated as cattle: nothing irreplaceable lives here.

## Provisioning checklist

1. **Spin up the VPS.** Ubuntu 24.04, SSH key auth, swap a domain (or
   subdomain) `neo4j.yourdomain.com` to point its A record at the VPS IP.

2. **Firewall**: open only 22 / 80 / 443. (Hetzner Cloud Firewall or
   `ufw allow 22,80,443/tcp`.)

3. **Install Docker** (`curl -fsSL https://get.docker.com | sh`).

4. **Clone the repo on the VPS** (or just copy `infra/neo4j/` over via
   `scp -r`).

5. **Create the env file** alongside `docker-compose.yml`:
   ```
   NEO4J_HOSTNAME=neo4j.yourdomain.com
   NEO4J_PASSWORD=<openssl rand -hex 32>
   ```
   Note the password and the URL `neo4j+s://neo4j.yourdomain.com` — both go
   into the Render env vars below.

6. **Start the stack**:
   ```
   cd infra/neo4j
   docker compose up -d
   docker compose logs -f caddy
   ```
   Caddy will request a Let's Encrypt cert on first start (~30s). Look for
   `certificate obtained successfully`. If port 80 isn't reachable the
   ACME challenge fails — recheck firewall.

7. **Smoke test from your laptop**:
   ```
   docker run --rm neo4j:5.26-community cypher-shell \
     -a neo4j+s://neo4j.yourdomain.com -u neo4j -p '<NEO4J_PASSWORD>' \
     'RETURN 1'
   ```

8. **On the Render backend service**, set:
   ```
   NEO4J_URI=neo4j+s://neo4j.yourdomain.com
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=<same as step 5>
   ```

## Maintenance

- **Updates**: `docker compose pull && docker compose up -d`. Neo4j minor
  upgrades within 5.x are drop-in; major upgrades need a dump/load.
- **Logs**: `docker compose logs --tail=200 neo4j`.
- **Backups**: skipped on purpose — see the "Disaster recovery" section of
  the multi-tenant deployment plan. Run `python -m app.graph_rebuild --all`
  from the backend host to rehydrate the graph from Postgres.
- **Disk usage**: `docker compose exec neo4j du -sh /data`. Should stay
  under ~1 GB for ≤10 users with a year of journaling.

## Cost

- Hetzner CX22: ~€5/mo
- Oracle Cloud Free Tier: $0/mo (the ARM "always-free" 24 GB box; needs
  account verification with a credit card)
- Domain: ~$1/mo (or free if you use a Cloudflare-fronted subdomain of an
  existing domain)

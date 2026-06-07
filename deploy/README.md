# MTI Brain Deployment

AWS CodeDeploy lifecycle hooks executed on the EC2 host as part of the CodePipeline → CodeDeploy flow. The pipeline ships the source bundle to `/opt/mti-brain` and CodeDeploy runs each script below in order. Docker images are built **on the EC2 host** (no ECR).

## Pipeline Flow

```
GitHub push (main)
   │
   ▼
CodePipeline (CodeStar GitHub connection)
   │
   ▼
CodeDeploy ── ships source ZIP to /opt/mti-brain
   │
   ▼
EC2 hooks (this folder)
   │
   ▼
docker compose build && docker compose up -d
```

## Lifecycle Scripts

Hooks are wired in the repo-root `appspec.yml`. Each script runs as the `ubuntu` user, logs to `/var/log/mti-brain-deploy.log`, and fails the deployment on a non-zero exit.

| Order | Hook | Script | Timeout | Purpose |
|-------|------|--------|---------|---------|
| 1 | `ApplicationStop` | `application_stop.sh` | 300 s | `docker compose down --remove-orphans` to release ports/volumes before the new bundle lands |
| 2 | `BeforeInstall` | `before_install.sh` | 300 s | Backs up `.env` to `/tmp/mti-brain.env.backup`, wipes `/opt/mti-brain` so CodeDeploy can lay down clean files |
| 3 | *CodeDeploy copies files* | — | — | Source from S3 artifact bucket → `/opt/mti-brain` |
| 4 | `AfterInstall` | `after_install.sh` | 600 s | Restores `.env` (or fetches it from SSM Parameter Store on first deploy), `chmod +x deploy/*.sh`, runs `docker compose build` |
| 5 | `ApplicationStart` | `application_start.sh` | 900 s | `docker compose up -d`, prunes dangling images to reclaim disk |
| 6 | `ValidateService` | `validate_service.sh` | 300 s | Polls `http://localhost:8000/health` and `http://localhost:3000` for up to ~2 minutes; dumps container logs and fails if either is unhealthy |

## EC2 Host Prerequisites

The instance must have these installed and running before the first deployment. A bootstrap script is provided in the root README; key requirements:

- **Docker Engine** + `docker-compose-plugin` (Compose v2)
- **AWS CLI v2** (used by `after_install.sh` to fetch `.env` from SSM)
- **CodeDeploy agent** (`codedeploy-agent.service`, enabled and running)
- **Instance profile** with the IAM policies:
  - `AmazonEC2RoleforAWSCodeDeploy` — pull deployment bundles from the artifact S3 bucket
  - `AmazonSSMReadOnlyAccess` — fetch the production `.env` from Parameter Store
- **Tag** matching the CodeDeploy deployment-group filter (e.g. `App=mti-brain`)
- **Security group** allowing inbound `80`, `443` (and `22` from your bastion / IP)
- **Disk** ≥ 30 GB gp3 (Docker layer cache + Postgres volume)

## Production .env Handling

`.env` is **not** in git. The hooks support two ways of getting it onto the host:

1. **One-time SCP** — copy your `.env` to `/opt/mti-brain/.env` after the first run; `before_install.sh` preserves it across all subsequent deployments.
2. **SSM Parameter Store** — store the entire `.env` body as a `SecureString` at `/mti-brain/prod/env`. `after_install.sh` fetches it on first deploy if no backup exists:
   ```bash
   aws ssm get-parameter \
     --name "/mti-brain/prod/env" \
     --with-decryption \
     --region us-east-1 \
     --query Parameter.Value --output text > /opt/mti-brain/.env
   ```

Update the parameter name / region in `after_install.sh` to match your AWS setup.

## Manual Operations

Run these on the EC2 host for ad-hoc debugging — they mirror what the hooks do:

```bash
# Tail deploy log
tail -f /var/log/mti-brain-deploy.log

# Re-run a single hook manually
sudo -u ubuntu /opt/mti-brain/deploy/application_start.sh

# Inspect running stack
cd /opt/mti-brain && docker compose ps && docker compose logs --tail=200

# Force rebuild without redeploying through CodePipeline
cd /opt/mti-brain && docker compose build --no-cache && docker compose up -d
```

## Rollback

CodeDeploy keeps the last `N` deployment bundles in its S3 artifact store. To roll back:

1. Console → **CodeDeploy → Applications → mti-brain → Deployments**
2. Pick a previous successful deployment → **Actions → Retry / Rollback**

Because images are built on the host (no immutable image tags), rollback re-runs `docker compose build` against the older source — which can take 3–8 minutes. For instant rollback, switch to ECR-backed images.

## Trade-offs (Build-on-Host vs ECR)

| | Build-on-host (current) | ECR |
|---|---|---|
| Setup complexity | Low — no registry, no auth | Higher — repo + IAM + push from CodeBuild |
| Cost | None beyond EC2 | ECR storage + transfer |
| Deploy time | 3–8 min (full rebuild) | ~30 s (pull + restart) |
| Resource contention | Build competes with running containers | None |
| Rollback | Re-build prior commit | Retag prior image |
| Multi-instance | Each host rebuilds | All hosts share image |

For a single-host environment this folder is sufficient. For multi-instance or fast rollback, add a CodeBuild stage that pushes to ECR and replace `docker compose build` here with `docker compose pull`.

## Environment Notes

The production `.env` must match the updated values from recent development:

- `CORS_ORIGINS` — include both the nginx-proxied origin and any direct frontend origin
- `NEXT_PUBLIC_API_URL` — in the Docker Compose / nginx setup this is typically empty (requests go through nginx on the same origin); see `frontend/.env.example`

## Files

```
deploy/
├── application_stop.sh    # docker compose down --remove-orphans
├── before_install.sh      # Preserve .env, wipe target dir
├── after_install.sh       # Restore .env (or fetch from SSM), docker compose build
├── application_start.sh   # docker compose up -d, prune dangling images
├── validate_service.sh    # Poll backend /health + frontend root for readiness
└── README.md              # This file
```

---

## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Backend (FastAPI) | [../backend/README.md](../backend/README.md) |
| Frontend (Next.js) | [../frontend/README.md](../frontend/README.md) |
| Database (PostgreSQL + PgBouncer) | [../database/README.md](../database/README.md) |

# Operations Guide

This guide covers running and maintaining DAWAM in production: backups, encryption key rotation, monitoring, and troubleshooting.

## Backups

A complete DAWAM backup consists of two parts: the PostgreSQL database and the file storage volume.

### Database Backups

Back up the PostgreSQL database using `pg_dump`. Use `exec -T` so Docker does not attach a
terminal, which would corrupt the dump. The examples use the default `dawam` user and
database; use your `POSTGRES_USER` and `POSTGRES_DB` if you changed them.

```bash
# Using Docker Compose
docker compose exec -T db pg_dump -U dawam -d dawam > backup.sql

# Or with a specific date
docker compose exec -T db pg_dump -U dawam -d dawam > dawam_$(date +%Y-%m-%d).sql
```

For external PostgreSQL:

```bash
pg_dump -h postgres.example.com -U dawam -d dawam > backup.sql
```

**Options:**

- Custom format (`-Fc`, compressed, restored with `pg_restore`):
  ```bash
  docker compose exec -T db pg_dump -Fc -U dawam -d dawam > backup.dump
  ```
- `--verbose` to see what's being backed up.
- `--exclude-table=...` to skip specific tables.

### File Storage Backups

Copy the file-data volume:

```bash
# Create a backup archive
docker run --rm \
  -v dawam_file-data:/source \
  -v $(pwd):/backup \
  busybox tar czf /backup/files.tar.gz -C /source .

# Or mount the volume and copy manually
docker run --rm -it \
  -v dawam_file-data:/files \
  busybox ls -la /files
```

For S3 storage, configure S3 bucket versioning and use your cloud provider's backup tools.

### Restore from Backup

**Database restore:** restore into an empty database (for example a new `db-data` volume),
with `app` and `worker` stopped (`docker compose stop app worker`), so their migrations do
not create tables first.

```bash
# From SQL dump
docker compose exec -T db psql -U dawam -d dawam < backup.sql

# From custom format dump (pg_restore reads it from standard input)
docker compose exec -T db pg_restore -U dawam -d dawam < backup.dump
```

Then start them again with `docker compose up -d`.

**File storage restore:**

```bash
# From tar archive
docker run --rm \
  -v dawam_file-data:/dest \
  -v $(pwd):/backup \
  busybox tar xzf /backup/files.tar.gz -C /dest
```

**Validation:**

After restore, verify with the readiness endpoint:

```bash
curl http://localhost:8000/readyz
```

## Encryption Key Rotation

There is no rotation tool today. Stored values are sealed with a single `v1` format and the one key in `DAWAM_ENCRYPTION_KEY`; they cannot be re-encrypted automatically.

**What is encrypted:** Connection passwords, LLM provider API keys, web search provider API keys (Admin > Web access), and the mail module's SMTP password and undelivered link URLs.

**Changing the key makes all of these unreadable.** Procedure that works today:

1. Plan downtime and take a database backup first.
2. Generate a new key and set `DAWAM_ENCRYPTION_KEY` in `.env`, then restart (`docker compose up -d`).
3. Re-enter the credential of every Connection, the key of every LLM provider and web search provider, and the SMTP password (if used). Undelivered links waiting in Admin > Email > Links to share cannot be read any more; send new invitations or reset links instead.
4. Verify: test each Connection and LLM provider, and check `GET /readyz`.

If you only lose or mislay the key, restoring the old key restores access. Keep it backed up separately.

## Monitoring & Logging

### Health Endpoints

DAWAM provides two HTTP endpoints for monitoring:

```bash
# Is the process alive?
curl http://localhost:8000/healthz

# Is the database ready? (migrations up-to-date?)
curl http://localhost:8000/readyz
```

`/healthz` returns `200 OK` while the process is up. `/readyz` returns `200 OK` when ready and `503` (`not_ready`) when the database is unreachable or migrations are pending.

### Logs

Logs are JSON lines on stdout. View them with:

```bash
docker compose logs -f app
docker compose logs -f worker
docker compose logs -f edge
```

Set `DAWAM_LOG_LEVEL` in `.env` to control verbosity: `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

### Metrics

**Not yet implemented.** DAWAM does not currently expose Prometheus metrics or other monitoring endpoints beyond health checks. Future versions may add `/metrics`.

## Database Maintenance

### Migrations

Database migrations run automatically when the app starts, controlled by `DAWAM_RUN_MIGRATIONS_ON_STARTUP` (default `true`). To upgrade, deploy the new image and restart; `GET /readyz` reports ready once migrations are at head.

### Vacuuming

PostgreSQL recommends regular vacuuming:

```bash
docker compose exec db vacuumdb -U dawam -d dawam
```

Or configure automatic vacuuming in PostgreSQL settings.

### Backups Schedule

Recommended backup frequency:
- **Database**: Daily or more often, depending on data change rate.
- **Files**: When important documents are added.

Example cron job (on the host):

```bash
0 2 * * * docker compose -f /path/to/compose.yaml exec -T db pg_dump -U dawam -d dawam > /backups/dawam_$(date +\%Y-\%m-\%d).sql
```

## Updates

To update DAWAM (take a backup first: migrations run when `app` starts):

1. **Fetch the latest code:**

   ```bash
   git pull origin main
   ```

2. **Rebuild images:**

   ```bash
   docker compose build
   ```

3. **Stop the current containers:**

   ```bash
   docker compose down
   ```

4. **Start the updated version:**

   ```bash
   docker compose up -d
   ```

   Migrations will run automatically.

5. **Verify:**

   ```bash
   curl http://localhost:8000/readyz
   ```

## Troubleshooting

### Database Is Full

Check disk usage:

```bash
docker exec dawam-db-1 du -sh /var/lib/postgresql/data
```

Options:
- Expand the volume in Docker Desktop or your orchestration platform.
- Archive old data and delete it.
- Run `vacuumdb` to reclaim space.

### Worker Process Is Stuck

Restart the worker:

```bash
docker compose restart worker
```

View worker logs:

```bash
docker compose logs -f worker
```

A running job whose worker sends no heartbeat (every `DAWAM_WORKER_HEARTBEAT_SECONDS`, default 10s) for `DAWAM_JOB_STALE_SECONDS` (default 60s) is failed as lost. Raise the stale time if the machine is under heavy load; it must stay above the heartbeat interval.

### Out of Memory

If containers are OOM-killed:

```bash
docker compose down
docker system prune -a  # clean up unused images
docker compose up
```

Alternatively, increase Docker's memory limit or reduce the number of worker replicas.

### Cannot Connect to LLM

Check the LLM provider configuration in the Admin console. Verify:
- Base URL is reachable from inside the container.
- The model name is correct (e.g., `qwen2.5:1.5b` for Ollama).
- API key (if needed) is set.

Or use **Test connection** on the model in Admin > Language models. Test connectivity from the app container (the image has Python but no `curl`):

```bash
docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/v1/models', timeout=5).read()[:500])"
```

### File Upload Fails

Check the storage backend:

```bash
# For local storage, verify the volume is mounted
docker compose exec app ls -la /var/lib/dawam/files

# For S3, check the app logs and the S3 settings in .env (the image must be built with the s3 extra)
docker compose logs app
```

Increase upload size limit if needed:

```bash
DAWAM_UPLOAD_MAX_MB=100
```

Note: The nginx reverse proxy also limits request size to 100 MB by default.

## Backup & Restore Checklist

- [ ] Database dumps stored securely (encrypted, offsite).
- [ ] Encryption key backed up and stored separately.
- [ ] File volume backups automated.
- [ ] Restore procedure tested (at least monthly).
- [ ] Staff trained on recovery steps.
- [ ] RTO/RPO defined and tested.

## Performance Tuning

### PostgreSQL Configuration

For larger deployments, tune PostgreSQL settings in `compose.yaml` by passing them to the server:

```yaml
db:
  command: ["postgres", "-c", "max_connections=200", "-c", "shared_buffers=256MB"]
```

### Worker Concurrency

Scale the worker by running multiple instances:

```bash
docker compose up -d --scale worker=3
```

Workers share the job queue safely. The all-in-one `deploy/docker-compose.yaml` runs
`DAWAM_WORKERS` replicas (default 2).

## Support & Resources

- **GitHub Issues**: [Report bugs](https://github.com/EslamAlaaZaki/dawam/issues)
- **Documentation**: See `docs/spec.md` for feature details.
- **Health Check**: Use `GET /readyz` to verify system readiness.

# Operations Guide

This guide covers running and maintaining DAWAM in production: backups, encryption key rotation, monitoring, and troubleshooting.

## Backups

A complete DAWAM backup consists of two parts: the PostgreSQL database and the file storage volume.

### Database Backups

Back up the PostgreSQL database using `pg_dump`:

```bash
# Using Docker Compose
docker compose exec db pg_dump -U dawam -d dawam > backup.sql

# Or with a specific date
docker compose exec db pg_dump -U dawam -d dawam > dawam_$(date +%Y-%m-%d).sql
```

For external PostgreSQL:

```bash
pg_dump -h postgres.example.com -U dawam -d dawam > backup.sql
```

**Options:**

- `--custom` format (faster restore, compressed):
  ```bash
  docker compose exec db pg_dump -Fc -U dawam -d dawam > backup.dump
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

**Database restore:**

```bash
# From SQL dump
docker compose exec -T db psql -U dawam -d dawam < backup.sql

# From custom format dump
docker compose exec -T db pg_restore -U dawam -d dawam backup.dump
```

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

**What is encrypted:** Connection passwords, LLM provider API keys, and the mail module's stored passwords and link URLs.

**Changing the key makes all of these unreadable.** Procedure that works today:

1. Plan downtime and take a database backup first.
2. Generate a new key and set `DAWAM_ENCRYPTION_KEY` in `.env`, then restart (`docker compose up -d`).
3. Re-enter the credential of every Connection and the key of every LLM provider in the admin console (and any mail credentials, if used).
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

Both return `200 OK` on success, `5xx` on failure.

### Logs

Logs are JSON lines on stdout and stderr. View them with:

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
0 2 * * * docker compose -f /path/to/compose.yaml exec db pg_dump -U dawam -d dawam > /backups/dawam_$(date +\%Y-\%m-\%d).sql
```

## Updates

To update DAWAM:

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
   docker compose up
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

A "job stale" message means the worker didn't report back within `DAWAM_JOB_STALE_SECONDS` (default: 60s). Adjust if the network is slow or the machine is under load.

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

Test connectivity from the app container:

```bash
docker compose exec app curl -v http://ollama:11434/v1/models
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

For larger deployments, tune PostgreSQL settings in `compose.yaml`:

```yaml
db:
  environment:
    POSTGRES_INITDB_ARGS: "-c max_connections=200 -c shared_buffers=256MB"
```

### Worker Concurrency

Scale the worker by running multiple instances:

```bash
docker compose up --scale worker=3
```

## Support & Resources

- **GitHub Issues**: [Report bugs](https://github.com/EslamAlaaZaki/dawam/issues)
- **Documentation**: See `docs/spec.md` for feature details.
- **Health Check**: Use `GET /readyz` to verify system readiness.

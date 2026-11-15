# Backup and restore

All the school's data is in one PostgreSQL database: accounts, students, results, fees,
payments and the audit log. Backing it up means saving one file. The backup and restore
below were run for real on a test copy: three students, two payments and an audit trail
were saved, the database was deleted and recreated empty, the file was restored, and the
user count, student count, payments, payment total, audit rows and migration revision were
identical afterwards. The next receipt number carried on from where it had stopped.

Commands are run from the repository root on the server. `dc` is the shortcut from
[DEPLOYMENT.md](DEPLOYMENT.md):

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'
```

## What a backup holds, and what it does not

- **Holds:** everything in the database, including password hashes, sign-in sessions and
  children's names and results. Treat every backup file as confidential: keep it readable
  by the administrator only (`chmod 600`), and encrypt it before it leaves the server.
- **Does not hold:** the `.env` file (database password, domain), which you must keep
  safely in a separate place, or the proxy's certificates, which are re-issued
  automatically.

## Back up

```bash
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > camdock-$(date +%F-%H%M).dump
chmod 600 camdock-*.dump
```

`-Fc` writes PostgreSQL's compressed custom format. The API does not need to be stopped:
`pg_dump` reads a consistent snapshot while people keep working.

Check the file is a real backup, not an empty one:

```bash
ls -l camdock-*.dump                        # a few tens of KB or more, never 0
dc exec -T db pg_restore --list < camdock-2026-11-09-1800.dump | head
```

The first bytes of a good file are `PGDMP`.

### Every day, automatically

Run the backup from cron (`crontab -e`), for example at 02:00, keeping 30 days:

```cron
0 2 * * * cd /srv/camdock && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > /srv/backups/camdock-$(date +\%F).dump && chmod 600 /srv/backups/camdock-$(date +\%F).dump && find /srv/backups -name 'camdock-*.dump' -mtime +30 -delete
```

(`\%` is needed because `%` has a special meaning in a crontab.) Then copy the folder to a
**different machine or storage account** each day, for example with `rsync -az` or
`rclone`. A backup on the same disk does not survive a failed disk or a stolen server.

A backup you have never restored is a guess. Do a [restore drill](#restore-drill-safe-to-do-any-time)
after setting this up and then once a term.

## Restore

Use this after data loss, a bad update, or when moving to a new server. It **replaces**
the live database with the backup's contents, so everything entered after the backup was
taken is lost. Tell the school which time the data goes back to.

```bash
# 1. stop people writing to it
dc stop api proxy

# 2. replace the database with an empty one
dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE \"$POSTGRES_DB\" WITH (FORCE)" \
  -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\""'

# 3. load the backup
dc exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < camdock-2026-11-09-1800.dump

# 4. start again and check
dc start api proxy
curl -s https://portal.school.example/api/v1/health/ready
```

`--exit-on-error` makes a damaged or partial file fail loudly instead of leaving half a
database. If step 3 fails, nothing is lost that was not already gone: fix the cause, then
repeat steps 2 and 3.

Notes:

- **Restoring to a new server:** install Docker, follow [DEPLOYMENT.md](DEPLOYMENT.md)
  steps 1 and 2, then do steps 2 and 3 above instead of running migrations. The backup
  already contains the tables and the migration revision.
- **A backup from an older version** restores fine. Afterwards run
  `dc run --rm api alembic upgrade head` to bring it up to date, then check
  `/health/ready`.
- **Signed-in users** whose sessions are in the backup stay signed in; others sign in again.
- The audit log cannot be edited or deleted by the application (a database trigger
  forbids it). A restore brings it back as it was.

## Restore drill (safe to do any time)

This proves the latest backup works **without touching the live database**. It restores
into a separate scratch database and compares a few numbers:

```bash
dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE camdock_drill"'
dc exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d camdock_drill --no-owner --exit-on-error' < camdock-2026-11-09-1800.dump

QUERY="select (select count(*) from students) students, (select count(*) from fee_payments) payments, (select coalesce(sum(amount),0) from fee_payments) paid, (select version_num from alembic_version) revision"
dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d camdock_drill -Atc \"$QUERY\""
dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"$QUERY\""   # live, for comparison

dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE camdock_drill"'
```

The two lines should match, apart from anything entered since the backup was taken.

## What a backup does not protect against

- A mistake that is already in the data by the time of the backup (for example a wrong
  amount entered yesterday). Keep several days of backups so you can look at an older state.
- Losing both the server and the off-server copy. Keep the off-server copy somewhere
  independent.
- Losing the `.env` file. A backup holds no database passwords, so on a new server you
  can choose a new `POSTGRES_PASSWORD` and still restore. Keep a copy of `.env` in a
  password manager anyway, since it also holds your domain and settings.

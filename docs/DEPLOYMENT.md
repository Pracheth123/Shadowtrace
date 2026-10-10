# Deployment: one EC2 host + private RDS PostgreSQL (demo)

Status (2026-10-10): **prepared and rehearsed locally; not deployed to AWS.**
No AWS account, CLI or credentials were available, and no domain was supplied.
Nothing below claims an AWS resource exists. See "What is verified" at the end.

## 1. Architecture

```
browser ──HTTPS/WSS──▶ Caddy (443, ACME cert) ──▶ 127.0.0.1:8000 uvicorn, ONE worker
                         │ serves client/dist                 │
                         │                                     ├─▶ files: /var/lib/shadowtrace (encrypted EBS)
                         │                                     └─▶ RDS PostgreSQL (private subnets, verify-full TLS)
EC2 public subnet (IGW route for Groq/Deepgram calls)   RDS: Single-AZ, encrypted, no public access
```

- One worker is deliberate: live sessions, reconnect tickets, admission limits,
  the session watchdog and evaluation tasks are in this process's memory.
  Moving durable data to RDS does **not** distribute that state; a second
  worker or host would break reconnects and limits.
- No NAT gateway (the app host has a public route; the DB has none), no load
  balancer, no Redis, no autoscaling, no Multi-AZ, no RDS Proxy.

## 2. Storage boundary (what is in PostgreSQL and what is not)

Only the **report index** is in PostgreSQL. Everything else is files under
`DATA_DIR` on the encrypted data volume. This is a deliberate first step, not
"all persistence is RDS".

| Record | Store | Location / table | Written by | Read by |
|---|---|---|---|---|
| Report index: evaluated sessions, per-dimension levels, gaps | **PostgreSQL** (SQLite in dev) | `session_reports`, `round_dimension_scores`, `report_gaps` | `services/evaluation.py` after a report is persisted | `services/history.py` (history, comparisons, recurring gaps, plan) |
| Schema version | PostgreSQL | `schema_migrations` | `python -m interview.storage migrate` | `/ready` |
| Guest identity (token hash) | file | `DATA_DIR/candidates/<id>/identity.json` | `candidates.py` | every authenticated route |
| Intake, source review, consent, claims, prep | files | `candidates/<id>/intakes/<intake>/…` | `services/intake.py` | setup, live session |
| Session meta, transcript, event log | files | `candidates/<id>/sessions/<sid>/meta.json`, `transcript.json`, `session.jsonl` | `server.py`, `session/runtime.py` | feedback, downloads, evaluation |
| Evaluation jobs and round results | files | `…/sessions/<sid>/meta.json` (`evaluation`), `evaluation/rounds/*.json` | `services/evaluation.py` | `/evaluation`, retry |
| Report and scorecard | files | `…/evaluation/report.json`, `scorecard.html` | `services/evaluation.py` | report, downloads; **source of the index** |
| Disputes and revisions | files | `…/sessions/<sid>/disputes.json`, `revisions/…` | `services/feedback.py` | report overlay, comparison exclusions |
| Practice records and comparisons | files | `candidates/<id>/practice/<pid>/…` | `services/practice.py` | practice page, history |
| Deletion tombstones | file | `DELETION_LEDGER_PATH` (production: `/var/lib/shadowtrace/ledger/`, outside `DATA_DIR`) | `erase_candidate` in `server.py` | `tools/ops/restore.py` |
| Operator stop | file | `DATA_DIR/OPERATOR_STOP` | operator, backup, restore | admission checks |
| Stage-9 longitudinal store (legacy tools) | SQLite file, only if present | `STORE_PATH` | `tools/roadmap.py` | erasure only (never created by the app) |

Consequences:

- The index is a projection of `report.json` files. `python -m interview.storage
  reconcile` reports index rows without files and report files without rows;
  `--apply` repairs both. History marks `report_missing` when a completed
  session's report file is gone, rather than hiding it.
- Ownership: index rows carry `candidate_id`; both backends refuse to record a
  session under a different candidate; reconcile takes ownership from the
  directory layout, never from the report body.
- Restart: stored records persist. An interview in progress during a host or
  service restart cannot resume (its runtime is in memory); its slot and
  socket are gone, and the candidate starts a new session. Evaluation jobs
  interrupted by a restart are reported "interrupted — retry"; completed
  rounds are kept.

## 3. Configuration

`/etc/shadowtrace/shadowtrace.env` (template: `deploy/env/shadowtrace.env.example`),
`root:shadowtrace 0640`. `/etc/shadowtrace/migrate.env` holds only the
migration role's `DATABASE_URL`, `root:root 0600`. Neither is in git, the
client build, logs or this document.

| Setting | Production value | Why |
|---|---|---|
| `DATABASE_URL` | app role, `sslmode=verify-full&sslrootcert=/etc/shadowtrace/rds-ca.pem` (quoted) | verify certificate and hostname; app role has row access only |
| `DB_AUTO_MIGRATE` | `0` | migrations run by the migration role, never by the app at startup |
| `DB_POOL_MAX_SIZE` / timeouts | 5 / connect 5 s / statement 5 s | bounded pool and queries |
| `DELETION_LEDGER_PATH` | `/var/lib/shadowtrace/ledger/deletion-ledger.jsonl` | survives any `DATA_DIR` restore |
| `ALLOWED_ORIGINS` | `https://<hostname>` | exact origin |
| `TRUSTED_PROXIES` | empty | uvicorn takes the client address from Caddy (`--forwarded-allow-ips 127.0.0.1`) |

Startup refuses: SQLite in production (unless `ALLOW_SQLITE_IN_PRODUCTION=1`),
PostgreSQL without `verify-full`, and an unreachable PostgreSQL (no SQLite
fallback is ever created). `/ready` returns 503 when the database is
unreachable, the schema is behind, the data directory or ledger is not
writable, or providers are missing. It makes no model calls.

Secrets: RDS generates the master password into Secrets Manager
(`ManageMasterUserPassword`). It is used once, interactively, to run
`deploy/sql/roles.sql` and set role passwords with `\password`. Rotation:
`ALTER ROLE shadowtrace_app PASSWORD …` (via `\password`), update the env
file, `systemctl restart shadowtrace`; rotate the master in the RDS console.
Provider keys are rotated in the providers' consoles and the env file.

## 4. Account checks before anything is created (operator)

In AWS Billing and Cost Management: plan (Free or Paid), remaining credit,
expiry and **eligible services for your actual offer**, existing resources and
usage. Create a cost budget with actual and forecast alerts; alerts can lag and
are not a spending cap. Do not change the plan as part of this deployment.

Choose one region for everything (stack, database, bucket, CLI). Mumbai
(`ap-south-1`) is a candidate for an India-focused demo; a region does not
guarantee faster Groq/Deepgram responses — measure from the host.

## 5. Seven-day cost worksheet (list price, before credits)

Generated from AWS's public price list by
`python tools/ops/aws_cost_estimate.py --region ap-south-1 --days 7 --instance <type> --db-class db.t4g.micro`
(files in `logs/aws_cost/`). On-demand USD list prices fetched 2026-10-10;
your bill depends on your plan and credits, which this cannot see.

| Item (ap-south-1, 7 days) | t3.micro | t4g.micro | t3.small |
|---|---|---|---|
| EC2 instance (168 h) | 1.88 | 0.94 | 3.76 |
| EBS gp3 root 16 GB + data 10 GB | 0.55 | 0.55 | 0.55 |
| Public IPv4 (Elastic IP, 168 h × $0.005) | 0.84 | 0.84 | 0.84 |
| RDS db.t4g.micro Single-AZ (168 h × $0.021) | 3.53 | 3.53 | 3.53 |
| RDS gp3 20 GB | 0.60 | 0.60 | 0.60 |
| RDS backups ≤ allocated storage | 0.00 | 0.00 | 0.00 |
| S3 backups (~1 GB, ~200 PUTs) | 0.01 | 0.01 | 0.01 |
| Secrets Manager (RDS master secret) | 0.09 | 0.09 | 0.09 |
| **Total** | **7.50** | **6.56** | **9.38** |

Not included (not in the stack): NAT gateway, load balancer, CloudWatch Logs,
Route 53, data transfer beyond the free allowance, Groq and Deepgram.
After teardown: the retained data-volume snapshot (~$0.27/month for 10 GB) and
the RDS final snapshot continue to bill until deleted.

Instance sizing: the API used **87.5 MB idle and 91.7 MB peak RSS** running four
parallel offline interviews with evaluation (`tools/ops/measure_memory.py`,
Windows, Python 3.12). Live providers and voice add buffers but not hundreds of
MB; `t3.micro` (1 GiB) leaves room for Caddy and the OS. `t4g` is Arm:
dependencies have aarch64 wheels and Caddy is pinned for arm64, but this has
not been run on Arm here — use the arm64 AMI parameter and test first.

## 6. Provision (CloudFormation)

`deploy/aws/shadowtrace-demo.yaml` — VPC, public subnet + two private DB
subnets (two AZs), IGW, security groups (80/443 public; 5432 from the app SG
only; SSH only if you pass your /32, otherwise SSM), encrypted RDS PostgreSQL
16 Single-AZ with RDS-managed master secret, encrypted data volume, Elastic
IP, IMDSv2, instance role (SSM + optional bucket prefix), optional private
encrypted S3 bucket (no versioning, lifecycle expiry). Validated with
`cfn-lint` 1.57.2 for `ap-south-1` and `us-east-1`.

```bash
aws rds describe-db-engine-versions --engine postgres --region ap-south-1 \
  --query 'DBEngineVersions[?starts_with(EngineVersion,`16.`)].EngineVersion'
aws cloudformation deploy --region ap-south-1 --stack-name shadowtrace-demo \
  --template-file deploy/aws/shadowtrace-demo.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides InstanceType=t3.micro DbEngineVersion=<from above> \
    DomainName=<your hostname> CreateBackupBucket=true
aws cloudformation describe-stacks --region ap-south-1 --stack-name shadowtrace-demo --query 'Stacks[0].Outputs'
```

Point the hostname's A record at `PublicIp`. Without a controlled hostname
there is no publicly trusted certificate, and an `http://<ip>` site is **not**
a usable mobile voice deployment (browsers require HTTPS for the microphone).
A free option if you have no domain: a subdomain from a dynamic-DNS provider
pointing at the Elastic IP (check its terms). No domain is purchased here.

## 7. First install (on the host, via `aws ssm start-session --target <InstanceId>`)

```bash
# Release (built on a workstation): deploy/scripts/build_release.sh  → copy the
# tarball and its .sha256 to /tmp (S3 or scp).
sudo install -d -m 0755 /etc/caddy
# 1. Database roles (master credentials from the RDS secret, typed, never saved)
psql "host=<DbEndpoint> port=5432 dbname=shadowtrace user=st_admin sslmode=verify-full sslrootcert=/etc/shadowtrace/rds-ca.pem" \
  -f /tmp/shadowtrace-<sha>/deploy/sql/roles.sql      # then \password shadowtrace_migrator ; \password shadowtrace_app
# 2. Runtime config
sudo install -m 0640 -o root -g shadowtrace deploy/env/shadowtrace.env.example /etc/shadowtrace/shadowtrace.env   # then edit
sudo install -m 0600 -o root -g root /dev/null /etc/shadowtrace/migrate.env   # DATABASE_URL='…shadowtrace_migrator…verify-full…'
# 3. Units and Caddy
sudo cp deploy/systemd/{shadowtrace.service,shadowtrace-backup.service,shadowtrace-backup.timer,caddy.service} /etc/systemd/system/
sudo cp deploy/journald/shadowtrace.conf /etc/systemd/journald.conf.d/   # bounded journal
sudo sed "s/interview.example.org/<hostname>/" deploy/caddy/Caddyfile > /etc/caddy/Caddyfile
sudo systemctl daemon-reload && sudo systemctl enable shadowtrace caddy shadowtrace-backup.timer
# 4. Release: venv from locks, migrations (migration role), switch, restart, wait for /ready
sudo deploy/scripts/install_release.sh /tmp/shadowtrace-<sha>.tar.gz
sudo systemctl start caddy
curl -fsS https://<hostname>/ready
```

## 8. Operate

| Task | Command |
|---|---|
| New release | `sudo deploy/scripts/install_release.sh /tmp/shadowtrace-<sha>.tar.gz [--maintenance]` — rolls back automatically if `/ready` fails |
| Code rollback | `sudo deploy/scripts/rollback.sh` |
| Pause / drain / resume | `deploy/scripts/maintenance.sh on \| drain \| status \| off` |
| Backup now | `sudo systemctl start shadowtrace-backup` (daily timer at 02:30) |
| Index status / reconcile | `sudo -u shadowtrace env $(…) .venv/bin/python -m interview.storage status \| reconcile [--apply]` |
| Logs | `journalctl -u shadowtrace -n 200` (bounded: 200 MB / 14 days); Caddy: `/var/log/caddy/` (20 MiB × 10) |

**Rollback and the database.** Migrations are additive (create tables/columns,
never drop), so the previous release runs on the newer schema and keeps the
data written since. Returning the database to an earlier state is a restore
(§9), planned against the data written since — never a switch to an old SQLite
file.

**Migrating existing SQLite data** (maintenance window):
1. `maintenance.sh drain` (no new work; live sessions and evaluations finish).
2. Back up (`backup.py`) — the source SQLite file is never modified or deleted.
3. `python -m interview.storage import-sqlite <DATA_DIR>/reports.sqlite` (dry run: counts, conflicts),
   then `--apply` with the migration role. It is restartable (per-session replace) and validates
   row counts and representative reads.
4. Switch `DATABASE_URL`, restart, `reconcile`, check `/ready`, `maintenance.sh off`.

## 9. Backup and restore

`backup.sh` → `tools/ops/backup.py --quiesce-url`: sets `OPERATOR_STOP`, waits
until no live sessions or evaluation jobs remain, copies `DATA_DIR` (SQLite via
the online backup API), exports the PostgreSQL index in one REPEATABLE READ
transaction, checksums everything in `manifest.json`, resumes, prunes to 7,
optionally copies to the private bucket. The ledger is copied separately. RDS
automated backups (7 days) are a second, independent layer.

Restore — **always into an isolated database and directory first**:

```bash
sudo systemctl stop shadowtrace
python tools/ops/restore.py ARCHIVE --verify-only
DATABASE_URL='<migration-role URL of a NEW, empty database>' \
DELETION_LEDGER_PATH=/var/lib/shadowtrace/ledger/deletion-ledger.jsonl \
python tools/ops/restore.py ARCHIVE --target /var/lib/shadowtrace/restore-check
```

What it does: verifies checksums; replaces the target index with the
snapshot; removes every candidate in the **union** of the archive's ledger and
the live ledger from both files and database (so deletions made after the
backup stay deleted); reconciles index ↔ report files; writes `OPERATOR_STOP`
into the restored directory. Keep the service in maintenance until you have
checked the summary (`reconcile_after.consistent: true`), then switch paths or
URLs and remove `OPERATOR_STOP`. An existing target directory is moved aside,
never deleted.

Retention vs. backups: an archive holds data a candidate later deletes until
it expires (local `--keep 7`, bucket lifecycle `BackupRetentionDays`); every
restore reapplies the ledger. The bucket is unversioned so deleted archives
leave no noncurrent copies. RDS snapshots follow the RDS retention window.

## 10. Teardown and cleanup inventory

```bash
deploy/scripts/maintenance.sh drain && sudo systemctl start shadowtrace-backup   # final backup, copy off-host
aws cloudformation update-stack … DbDeletionProtection=false   # if it was enabled
aws s3 rm s3://<BackupBucketName> --recursive && aws s3 rb s3://<BackupBucketName>   # bucket is Retain
aws cloudformation delete-stack --region ap-south-1 --stack-name shadowtrace-demo
```

Then check for residual billable items in the region: the RDS final snapshot
and data-volume snapshot (both created by `DeletionPolicy: Snapshot`), any
manual snapshots, unattached EBS volumes, Elastic IPs, the S3 bucket (and any
object versions if versioning was ever enabled), Secrets Manager secrets
scheduled for deletion, CloudWatch log groups (none are created by the stack),
and the budget.

## 11. Acceptance after a real deployment (pending)

1. `https://<hostname>/ready` → `ready: true`, `report_store_backend: postgres`.
2. Browser journey: setup → source review → interview → feedback → dispute →
   practice → comparison → history → transcript and scorecard downloads →
   Delete my data. (`tools/ui-test` can target it with `APP_URL` adapted; the
   offline runner itself uses mocks and is not run against production.)
3. Restart the service; history and reports persist.
4. Backup, delete a test guest, restore into an isolated database; the guest stays deleted.
5. Separately, with real keys: a live voice interview on desktop and on a phone
   over HTTPS; measure latency with `tools/latency_report.py`.

## What is verified (2026-10-10)

| | Code | Tested locally | Deployed to AWS | Externally verified |
|---|---|---|---|---|
| PostgreSQL report index, migrations, pool, timeouts, readiness | ✓ | ✓ real PostgreSQL 16.4 (Windows 3.12; Linux 3.12 container) | — | — |
| Full suite on both backends | ✓ | ✓ | — | — |
| Backup → later deletion → isolated restore → reconcile | ✓ | ✓ (pytest, real PostgreSQL) | — | — |
| CloudFormation template | ✓ | ✓ cfn-lint only (not launched) | — | — |
| Release, install, rollback, maintenance, backup scripts | ✓ | see rehearsal below | — | — |
| HTTPS/WSS via Caddy | ✓ | see rehearsal below (Caddy internal CA, not a public certificate) | — | — |
| Cost worksheet | ✓ | ✓ public list prices | — | account plan/credits unknown |
| Live voice, mobile HTTPS, latency | — | — | — | pending (no keys, devices, or deployment) |

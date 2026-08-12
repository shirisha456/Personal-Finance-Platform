# Deploying Personal Finance Platform to a single EC2 instance

A single box behind nginx, running Postgres, Redis, Redpanda, all four
app services (core-api, web, enrichment-service, anomaly-service,
notification-service), and the full observability stack. This is
explicitly **not highly-available** — losing the instance or its EBS
volume means restoring from backup, not automatic failover. See
[ADR-0011](../docs/adr/0011-terraform-written-not-applied.md) and
[ADR-0012](../docs/adr/0012-single-ec2-instance-sizing.md) for why this
tradeoff, and this instance size, were chosen.

## First-time deploy

1. Provision the instance:
   ```bash
   cd infra/terraform/envs/single-ec2
   cp terraform.tfvars.example terraform.tfvars   # set alert_email at minimum
   terraform init
   terraform apply
   ```
2. Connect — no SSH key, no open port 22, SSM Session Manager only:
   ```bash
   $(terraform output -raw connect_command)
   ```
3. Inside the session:
   ```bash
   sudo su - ubuntu
   cd /opt/meridian
   git clone https://github.com/shirisha456/Personal-Finance-Platform.git .
   cp deploy/secrets.env.example /opt/meridian/secrets.env
   nano /opt/meridian/secrets.env   # fill in every value — see that file's own comments
   chmod 600 /opt/meridian/secrets.env
   docker compose -f deploy/docker-compose.prod.yml up -d --build
   ```
4. Verify: `docker compose -f deploy/docker-compose.prod.yml ps` should
   show every service `Up (healthy)` within about a minute. The app is
   live at `terraform output url` (plain HTTP until you enable HTTPS
   below).

## Enabling HTTPS

If you have a domain, point an A record at the instance's Elastic IP
(`terraform output public_ip`) — either by hand, or by setting
`domain_name` in `terraform.tfvars` and re-applying (creates/updates a
Route 53 record). Wait for DNS to propagate, then:

```bash
deploy/scripts/enable-https.sh yourdomain.example.com you@example.com
```

Without a domain, the deployment stays HTTP-only — a browser-trusted
certificate for a bare IP address isn't possible under the CA/Browser
Forum's baseline requirements. Fine for a private portfolio demo; not
fine for anyone entering real financial-account credentials.

## Rollback

```bash
git checkout <previous-sha>
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

Database migrations don't auto-rollback. If the previous version needs
an older schema, run `alembic downgrade -1` inside the `core-api`
container first — check whether the migration you're rolling back past
is purely additive (safe either way) before doing this.

## Backups

- **Logical** (fast, small, good for "I fat-fingered a delete" or a bad
  migration): `deploy/scripts/backup.sh`, intended to run nightly via
  cron. Writes a gzipped `pg_dump` to `/opt/meridian/backups/` and
  prunes anything older than 14 days. **This writes to the same EBS
  volume it's backing up — there's no off-box copy by default.** Adding
  `aws s3 cp` (with an S3 bucket and IAM permission for it) is a
  deliberate, documented future scope addition, not done here.
- **Physical** (whole-volume, for total instance loss): an EBS snapshot,
  **not automated by this Terraform**. Either attach an AWS Data
  Lifecycle Manager (DLM) policy to the volume, or snapshot by hand:
  ```bash
  aws ec2 create-snapshot --volume-id <vol-id> --description "meridian manual backup"
  ```
- Redpanda's own data lives in its own named volume with a short
  retention window (`observability`/broker config, not durability —
  Postgres already durably has every event's *effect* by the time a
  consumer processes it; Redpanda is the event log, not the source of
  truth).

Restore a logical backup:
```bash
deploy/scripts/restore.sh /opt/meridian/backups/meridian-<timestamp>.sql.gz
```
(Interactive confirmation required — this is destructive.)

## Total-loss recovery

1. `terraform apply` from `infra/terraform/envs/single-ec2` —
   re-provisions the instance with the *same* Elastic IP (it's a
   Terraform-managed resource independent of the instance's lifecycle).
2. Attach the latest EBS snapshot, or restore Postgres from the latest
   `backup.sh` dump once the new instance is up.
3. Recreate `/opt/meridian/secrets.env` by hand — it was never in the
   EBS snapshot's git history and never will be; keep a copy in a
   password manager.

## Full cleanup

```bash
cd infra/terraform/envs/single-ec2
terraform destroy
```

Destroys the instance, its root EBS volume, the Elastic IP, the
security group, the IAM role, the budget alarm, and the Route 53
zone/record if Terraform created one. **DLM snapshots (if you set one
up) and their accumulated storage cost are not destroyed by this** —
clean those up separately:
```bash
aws dlm get-lifecycle-policies
aws ec2 describe-snapshots --owner-ids self
aws ec2 delete-snapshot --snapshot-id <snap-id>
```

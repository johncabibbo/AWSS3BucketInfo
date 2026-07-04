# awsS3BucketInfo

Retrieves the size and object count of every AWS S3 bucket in an account —
broken down **by storage class** (STANDARD, GLACIER, etc.) — and logs the
results to a MySQL database. Each run is tracked as a *batch*, and optional
start/end/error notifications can be posted to a DocInfo Manager server‑job log.

- **Script:** `awsS3BucketInfo.py`
- **Config:** `awsS3BucketInfoConfig.json` (a sanitized **sample** — fill in your own values)
- **Version:** 1.3

---

## What it does

1. Connects to AWS S3 and lists all buckets visible to the credentials/profile.
2. For each bucket, paginates every object and sums `Size` + object count,
   grouped by `StorageClass`. Buckets that return `AccessDenied`,
   `NoSuchBucket`, or `AllAccessDisabled` are skipped gracefully.
3. Opens a new **batch** row (`S3BucketBatch`), auto‑adds any new bucket names to
   `S3Bucket`, and writes **one `S3BucketLog` row per storage class per bucket**.
4. Prints a human‑readable summary (largest buckets first) and a grand total.
5. Closes the batch (`execEndDate`) and — if configured — posts a serverJobLog
   `end` (or `error`) event to DocInfo Manager.

---

## Requirements

- **Python** 3.x
- **Python packages:**
  ```bash
  pip install boto3 pymysql
  ```
- **AWS credentials** reachable by boto3 — any one of:
  - an IAM role (when running on EC2/ECS),
  - `~/.aws/credentials` (default profile, or a named profile — see `aws.profile`),
  - standard AWS environment variables.
  The credentials need `s3:ListAllMyBuckets` and `s3:ListBucket` on the buckets
  you want measured.
- **A MySQL database** you can reach, with the three tables below created.

---

## Setup

1. **Install dependencies** (see Requirements).

2. **Create the database tables.** The exact `CREATE TABLE` statements are kept in
   the `_sql_reference` section of `awsS3BucketInfoConfig.json`. Run them once
   against your database:
   - `S3Bucket` — one row per bucket name (auto‑populated by the script).
   - `S3BucketBatch` — one row per run (start = `createdDate`, end = `execEndDate`).
   - `S3BucketLog` — the per‑bucket / per‑storage‑class measurements.

3. **Fill in the config.** Open `awsS3BucketInfoConfig.json` and replace every
   `YOUR_…` placeholder with your real values (see the table below). This file
   ships as a **sample** with no real credentials.

---

## Configuration reference

All settings live in `awsS3BucketInfoConfig.json`, which sits next to the script
by default.

| Section | Key | Sample value | Description |
|---|---|---|---|
| `database` | `host` | `YOUR_DB_HOST_OR_IP` | MySQL host name or IP. |
| | `port` | `3306` | MySQL port. |
| | `name` | `YOUR_DATABASE_NAME` | Database (schema) name. |
| | `username` | `YOUR_DB_USERNAME` | MySQL user. |
| | `password` | `YOUR_DB_PASSWORD` | MySQL password. |
| `aws` | `profile` | `""` | Named AWS CLI/credentials profile. Leave blank to use the default credential chain (IAM role, default profile, env vars). |
| | `region` | `us-east-1` | AWS region for the S3 client. |
| `docInfoApi` | `url` | `https://your-docinfo-host.example.com` | Base URL of your DocInfo Manager install. Leave blank to disable job‑log notifications. |
| | `sharedSecret` | `YOUR_SHARED_SECRET` | Shared secret expected by `xhr/serverJobLogAdd.php`. |
| | `serverJobId` | `0` | The server‑job ID to log against. `0` (or a blank URL/secret) disables notifications. |
| `logging` | `logFile` | `~/Documents/log/awsS3BucketInfo.log` | Path to the append‑only log file (`~` is expanded). The parent directory is created if missing. |
| | `printToConsole` | `true` | Also echo log lines to stdout. |

> **DocInfo Manager integration is optional.** If `docInfoApi.url`,
> `sharedSecret`, or `serverJobId` is empty/zero, the script simply skips the
> notification calls — it never fails because of them.

---

## Usage

Run with the config file that sits beside the script:

```bash
python3 awsS3BucketInfo.py
```

Or point at a specific config file:

```bash
python3 awsS3BucketInfo.py --config /path/to/awsS3BucketInfoConfig.json
```

### Scheduling (cron example)

Run daily at 2:00 AM:

```cron
0 2 * * * /usr/bin/python3 /path/to/awsS3BucketInfo/awsS3BucketInfo.py >> /path/to/awsS3BucketInfo/cron.out 2>&1
```

---

## Output

- **Console / log file:** a per‑bucket sizing line (with the storage‑class
  breakdown), followed by a summary table sorted largest‑first and a grand total.
- **Database:** a new `S3BucketBatch` row plus one `S3BucketLog` row per storage
  class per bucket, all linked by `s3BucketBatchId`.
- **Exit code:** `0` on success, `1` if any error occurred during the run
  (the batch is still closed and an `error` event is still logged).

---

## How storage‑class tracking works (v1.3)

Rather than storing a single total per bucket, the script groups every object by
its `StorageClass` and writes a separate `S3BucketLog` row for each class it
finds in a bucket. This lets you report on STANDARD vs. GLACIER vs. other tiers
over time. (Requires the `storageClass` and `objectCount` columns on
`S3BucketLog`.)

---

## Files

| File | Purpose |
|---|---|
| `awsS3BucketInfo.py` | The script. |
| `awsS3BucketInfoConfig.json` | Sample configuration — replace placeholders before use. |
| `README.md` | This documentation. |

---

## Notes & troubleshooting

- **`AWS credentials not found`** — configure `~/.aws/credentials`, set an AWS
  profile in `aws.profile`, or run under an IAM role.
- **A bucket is missing from the results** — the credentials likely lack
  `s3:ListBucket` on it (access errors are skipped, not fatal).
- **Large buckets are slow** — sizing paginates every object key, so buckets with
  many millions of objects take time and API calls. Consider scheduling off‑peak.
- **Nothing shows up in DocInfo Manager** — that integration is optional and only
  runs when `url` + `sharedSecret` + a non‑zero `serverJobId` are all set.

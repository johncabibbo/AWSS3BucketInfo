# AWS S3 Bucket Info

**Collect S3 bucket sizes and object counts by storage class, and log them to MySQL for tracking over time.**

`awsS3BucketInfo.py` walks your AWS S3 account, measures the size and object count of every bucket **broken down by storage class** (STANDARD, GLACIER, etc.), and writes the results to a MySQL database. It records each run as a batch, logs one row per storage class per bucket, and automatically registers any new buckets it discovers.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Alias Setup — Run From Anywhere](#alias-setup--run-from-anywhere)
6. [Configuration](#configuration)
7. [Usage & Examples](#usage--examples)
8. [Troubleshooting](#troubleshooting)
9. [License / Copyright](#license--copyright)

---

## Overview

Run it on a schedule (cron) to build a historical record of how much data lives in each S3 bucket and each storage class. Because results land in MySQL, you can chart growth, spot runaway buckets, and feed dashboards. It talks to a companion logging endpoint (`serverJobLogAdd.php`) so runs can be tracked alongside your other server jobs.

---

## Features

- **Per-storage-class breakdown** — size and object count for STANDARD, GLACIER, and every other class, per bucket.
- **MySQL logging** — writes to three tables: `S3Bucket` (registry), `S3BucketBatch` (one row per run), `S3BucketLog` (one row per storage class per bucket per batch).
- **Auto-registration** — new buckets are added to `S3Bucket` automatically.
- **Job logging** — POSTs start / end / error events to a `serverJobLogAdd.php` endpoint.
- **Config-driven** — AWS credentials, region, MySQL connection, and the SQL schema reference all live in one JSON file.

---

## Requirements

| Requirement | Notes |
|-------------|-------|
| **Python 3.8+** | Cross-platform (macOS, Linux, Windows). |
| **boto3** | AWS SDK for Python. |
| **pymysql** | MySQL client library. |
| **AWS credentials** | IAM user/role with `s3:ListAllMyBuckets` and `s3:ListBucket`. |
| **MySQL/MariaDB** | With the `S3Bucket`, `S3BucketBatch`, and `S3BucketLog` tables (see `_sql_reference` in the config). |

Install the Python dependencies:

```bash
pip install boto3 pymysql
```

---

## Installation

1. **Get the files:**

   ```bash
   git clone <REPOSITORY_URL> AWSS3BucketInfo
   cd AWSS3BucketInfo
   ```

2. **Install dependencies:** `pip install boto3 pymysql`

3. **Create the database tables** using the `_sql_reference` block inside `awsS3BucketInfoConfig.json`.

4. **Edit the config** (see [Configuration](#configuration)), then run:

   ```bash
   python3 awsS3BucketInfo.py
   ```

---

## Alias Setup — Run From Anywhere

Launch it from any directory by typing `s3info`.

### macOS / Linux (zsh or bash)

Add to `~/.zshrc` (default on modern macOS) or `~/.bashrc`:

```bash
alias s3info='python3 ~/path/to/AWSS3BucketInfo/awsS3BucketInfo.py'
```

Reload your shell and run:

```bash
source ~/.zshrc
s3info
```

### Windows (PowerShell)

Add a function to your PowerShell `$PROFILE`:

```powershell
function s3info { python "C:\path\to\AWSS3BucketInfo\awsS3BucketInfo.py" @args }
```

Open a new PowerShell window and run `s3info`. Replace the paths with the folder where you placed the release.

---

## Configuration

Edit **`awsS3BucketInfoConfig.json`**. It holds your AWS settings, MySQL connection, the job-logging endpoint, and a `_sql_reference` block documenting the required tables.

| Section | Purpose |
|---------|---------|
| AWS settings | Region and (optionally) credentials/profile. Prefer standard AWS credential resolution over hard-coding keys. |
| MySQL connection | Host, port, database, user, password for the logging database. |
| Job logging | `serverJobLogAdd.php` URL and job id (e.g. job 24). |
| `_sql_reference` | `CREATE TABLE` reference for `S3Bucket`, `S3BucketBatch`, `S3BucketLog`. |

> **Never commit real secrets.** Use an AWS profile / IAM role and a restricted MySQL user where possible.

---

## Usage & Examples

```bash
# Use the default config next to the script
python3 awsS3BucketInfo.py

# Point at an explicit config file
python3 awsS3BucketInfo.py --config ~/path/to/awsS3BucketInfoConfig.json
```

**Cron example — nightly at 2:30 AM:**

```cron
30 2 * * * /usr/bin/python3 ~/path/to/AWSS3BucketInfo/awsS3BucketInfo.py >> ~/Documents/log/awsS3BucketInfo.log 2>&1
```

Each run inserts one `S3BucketBatch` row and, for every bucket, one `S3BucketLog` row per storage class.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `NoCredentialsError` / access denied | Configure AWS credentials (env vars, `~/.aws/credentials`, or an IAM role) with S3 list permissions. |
| `pymysql.err.OperationalError` | Check MySQL host/port/user/password and that the tables exist. |
| Unknown column errors | Recreate the tables from the current `_sql_reference` — schema changed in v1.3 (added `storageClass`, `objectCount`). |
| Job-log POST failures | Non-fatal by design; verify the `serverJobLogAdd.php` URL and job id if you rely on it. |

---

## License / Copyright

---
**Version:** 1.3
**Author:** Cloud Box 9 Inc.
**Maintainer / Owner:** Cloud Box 9 Inc.
**Last Updated:** Jul 5, 2026

Copyright © 2026 Cloud Box 9 Inc. All rights reserved.

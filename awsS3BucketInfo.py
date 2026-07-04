#!/usr/bin/env python3
#
# Filename:     awsS3BucketInfo.py
# Project:      AWS Tools
# Version:      1.3
# Description:  Retrieves S3 bucket sizes and object counts by storage class,
#               then logs results to a MySQL database. Tracks execution via
#               S3BucketBatch and logs per-bucket/per-class data to S3BucketLog.
#               Adds new buckets to S3Bucket automatically.
# Maintainer:   Cloud Box 9 Inc.
# Last Modified Date: 2026-05-06
#
# Usage:
#   python3 awsS3BucketInfo.py
#   python3 awsS3BucketInfo.py --config /path/to/awsS3BucketInfoConfig.json
#
# Requirements:
#   pip install boto3 pymysql
#
# Database Tables Required:
#   See _sql_reference section in awsS3BucketInfoConfig.json
#
# -----------------------------------------------------------------------------
# Revision History:
# -----------------------------------------------------------------------------
# v1.3 (2026-05-06)
#   • Track size and object count per storage class (STANDARD, GLACIER, etc.)
#   • One S3BucketLog row per storage class per bucket per batch (was one total)
#   • Requires S3BucketLog.storageClass and S3BucketLog.objectCount columns
# v1.2 (2026-03-22)
#   • Added serverJobLog API calls (job 24) on start, end, and error events
#     via https://docinfo-host.example.com/xhr/serverJobLogAdd.php
# v1.1 (2026-03-22)
#   • Fixed column names to match actual DB schema:
#     S3Bucket: S3bucketId, S3bucketName
#     S3BucketBatch: S3BucketBatchId, execEndDate only (no start/status/notes)
#     S3BucketLog: S3BucketLogId, S3BucketBatchId, size (no objectCount/logDate)
# v1.0 (2026-03-22)
#   • Initial version — boto3 S3 size retrieval, MySQL logging via
#     S3Bucket, S3BucketBatch, and S3BucketLog tables
# -----------------------------------------------------------------------------

import argparse
import json
import os
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import boto3
import pymysql
from botocore.exceptions import ClientError, NoCredentialsError


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCRIPT_NAME    = 'awsS3BucketInfo.py'
SCRIPT_VERSION = '1.3'
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'awsS3BucketInfoConfig.json')


def load_config(config_path: str) -> dict:
    path = os.path.expanduser(config_path)
    if not os.path.exists(path):
        die(f'Config file not found: {path}')
    with open(path, 'r') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def die(msg: str):
    print(f'[ERROR] {msg}', file=sys.stderr)
    sys.exit(1)


def log(msg: str, log_file=None, console: bool = True):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    if console:
        print(line)
    if log_file:
        log_file.write(line + '\n')
        log_file.flush()


def bytes_to_human(size: int) -> str:
    """Convert bytes to human-readable string (B / KiB / MiB / GiB / TiB)."""
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if size < 1024:
            return f'{size:.1f} {unit}' if unit != 'B' else f'{size} {unit}'
        size /= 1024
    return f'{size:.1f} PiB'


# ---------------------------------------------------------------------------
# DocInfo Manager — serverJobLog API
# ---------------------------------------------------------------------------

def job_log(config: dict, event_type: str, text: str,
            text_full: str = '', summary_data: dict = None,
            log_fh=None, console: bool = True):
    """
    POST a serverJobLog entry to DocInfo Manager via the shared-secret API.
    Silently logs a warning if the call fails — never raises.

    event_type: 'start' | 'end' | 'error' | 'warning' | 'info'
    summary_data (end only): {'summary': str, 'warnings': int, 'errors': int}
    """
    api_cfg = config.get('docInfoApi', {})
    url     = api_cfg.get('url', '').rstrip('/')
    secret  = api_cfg.get('sharedSecret', '')
    job_id  = api_cfg.get('serverJobId', 0)

    if not url or not secret or not job_id:
        return  # API not configured — skip silently

    endpoint = f'{url}/xhr/serverJobLogAdd.php'

    payload = {
        'serverJobId':       str(job_id),
        'eventType':         event_type,
        'serverJobText':     text[:2000],
        'serverJobTextFull': text_full or text,
        'sharedSecret':      secret,
    }
    if summary_data and event_type == 'end':
        payload['summaryData'] = json.dumps(summary_data)

    try:
        data = urllib.parse.urlencode(payload).encode('utf-8')
        req  = urllib.request.Request(endpoint, data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode('utf-8')
            result = json.loads(body)
            if result.get('success') != '1':
                log(f'[WARN] serverJobLog API: {result.get("msg", "unknown error")}',
                    log_fh, console)
    except Exception as e:
        log(f'[WARN] serverJobLog API call failed: {e}', log_fh, console)


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

def get_s3_client(config: dict):
    aws_cfg = config.get('aws', {})
    kwargs = {'region_name': aws_cfg.get('region', 'us-east-1')}
    profile = aws_cfg.get('profile', '').strip()
    if profile:
        session = boto3.Session(profile_name=profile, **{'region_name': kwargs['region_name']})
        return session.client('s3')
    return boto3.client('s3', **kwargs)


def list_buckets(s3_client) -> list:
    """Return list of bucket names."""
    response = s3_client.list_buckets()
    return [b['Name'] for b in response.get('Buckets', [])]


def get_bucket_size_by_class(s3_client, bucket_name: str) -> dict:
    """
    Return {storageClass: (size_bytes, object_count)} by paginating list_objects_v2.
    Returns {} on access error (e.g. cross-account or permission denied).
    """
    paginator = s3_client.get_paginator('list_objects_v2')
    by_class: dict = {}

    try:
        for page in paginator.paginate(Bucket=bucket_name):
            for obj in page.get('Contents', []):
                sc = obj.get('StorageClass', 'STANDARD')
                if sc not in by_class:
                    by_class[sc] = [0, 0]
                by_class[sc][0] += obj['Size']
                by_class[sc][1] += 1
    except ClientError as e:
        code = e.response['Error']['Code']
        if code in ('AccessDenied', 'NoSuchBucket', 'AllAccessDisabled'):
            return {}
        raise

    return {k: (v[0], v[1]) for k, v in by_class.items()}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def db_connect(config: dict):
    db_cfg = config['database']
    return pymysql.connect(
        host     = db_cfg['host'],
        port     = int(db_cfg.get('port', 3306)),
        database = db_cfg['name'],
        user     = db_cfg['username'],
        password = db_cfg['password'],
        charset  = 'utf8mb4',
        autocommit = False,
        connect_timeout = 10,
    )


def ensure_bucket(cursor, bucket_name: str) -> int:
    """
    Return S3bucketId for bucket_name.
    Inserts a new row into S3Bucket if it doesn't exist.
    """
    cursor.execute(
        'SELECT S3bucketId FROM S3Bucket WHERE S3bucketName = %s',
        (bucket_name,)
    )
    row = cursor.fetchone()
    if row:
        return row[0]

    cursor.execute(
        'INSERT INTO S3Bucket (S3bucketName) VALUES (%s)',
        (bucket_name,)
    )
    return cursor.lastrowid


def insert_batch(cursor) -> int:
    """Insert a new S3BucketBatch row and return its ID. Start time = createdDate."""
    cursor.execute(
        'INSERT INTO S3BucketBatch () VALUES ()'
    )
    return cursor.lastrowid


def update_batch(cursor, batch_id: int):
    """Set execEndDate to now when the run completes."""
    cursor.execute(
        'UPDATE S3BucketBatch SET execEndDate = NOW() WHERE S3BucketBatchId = %s',
        (batch_id,)
    )


def insert_bucket_log(cursor, batch_id: int, bucket_id: int,
                       storage_class: str, size_bytes: int, object_count: int):
    cursor.execute(
        """INSERT INTO S3BucketLog (S3BucketBatchId, s3BucketId, storageClass, size, objectCount)
           VALUES (%s, %s, %s, %s, %s)""",
        (batch_id, bucket_id, storage_class, size_bytes, object_count)
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=f'{SCRIPT_NAME} v{SCRIPT_VERSION}')
    parser.add_argument('--config', default=DEFAULT_CONFIG,
                        help='Path to JSON config file')
    args = parser.parse_args()

    config  = load_config(args.config)
    log_cfg = config.get('logging', {})
    console = log_cfg.get('printToConsole', True)

    # Open log file — default to ~/Documents/log/awsS3BucketInfo.log
    default_log = os.path.join('~', 'Documents', 'log', 'awsS3BucketInfo.log')
    log_path    = os.path.expanduser(log_cfg.get('logFile', '') or default_log)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_fh = open(log_path, 'a')

    def _log(msg):
        log(msg, log_fh, console)

    _log('=' * 60)
    _log(f'START  {SCRIPT_NAME} v{SCRIPT_VERSION}')
    _log('=' * 60)

    def _job_log(event_type, text, text_full='', summary_data=None):
        job_log(config, event_type, text, text_full, summary_data, log_fh, console)

    conn   = None
    cursor = None
    batch_id     = None
    total_bytes  = 0
    bucket_count = 0
    had_error    = False

    # --- Notify: job started ---------------------------------------------
    _job_log('start', f'{SCRIPT_NAME} started')

    try:
        # --- AWS ---------------------------------------------------------
        _log('Connecting to AWS S3...')
        try:
            s3 = get_s3_client(config)
            buckets = list_buckets(s3)
        except NoCredentialsError:
            die('AWS credentials not found. Configure ~/.aws/credentials or IAM role.')

        _log(f'Found {len(buckets)} bucket(s)')

        # --- Database ----------------------------------------------------
        _log('Connecting to database...')
        conn   = db_connect(config)
        cursor = conn.cursor()

        # Insert batch record
        batch_id = insert_batch(cursor)
        conn.commit()
        _log(f'Batch started  (s3BucketBatchId={batch_id})')

        # --- Process each bucket -----------------------------------------
        results = []  # (bucket_name, total_bytes, total_objects)

        for bucket_name in buckets:
            _log(f'  Sizing: {bucket_name} ...')
            class_data = get_bucket_size_by_class(s3, bucket_name)

            bucket_bytes   = sum(v[0] for v in class_data.values())
            bucket_objects = sum(v[1] for v in class_data.values())

            human     = bytes_to_human(bucket_bytes)
            class_str = ', '.join(
                f'{sc}:{bytes_to_human(sb)}' for sc, (sb, _) in sorted(class_data.items())
            )
            _log(f'    {bucket_name:<40}  {human:>12}  ({bucket_objects:,} objects)  [{class_str}]')

            # Ensure bucket exists in S3Bucket table
            bucket_id = ensure_bucket(cursor, bucket_name)

            # Insert one row per storage class
            for storage_class, (class_bytes, class_objects) in class_data.items():
                insert_bucket_log(cursor, batch_id, bucket_id,
                                  storage_class, class_bytes, class_objects)

            total_bytes  += bucket_bytes
            bucket_count += 1
            results.append((bucket_name, bucket_bytes, bucket_objects))

        conn.commit()

        # --- Summary -----------------------------------------------------
        _log('')
        _log('-' * 60)
        _log(f'{"BUCKET":<40}  {"SIZE":>12}  OBJECTS')
        _log('-' * 60)
        for name, sb, oc in sorted(results, key=lambda x: x[1], reverse=True):
            _log(f'{name:<40}  {bytes_to_human(sb):>12}  {oc:,}')
        _log('-' * 60)
        _log(f'{"TOTAL":<40}  {bytes_to_human(total_bytes):>12}  '
             f'({total_bytes:,} bytes)')
        _log('-' * 60)

    except Exception as e:
        had_error = True
        err_msg   = str(e)
        _log(f'[ERROR] {err_msg}')
        _log(traceback.format_exc())
        _job_log('error', f'{SCRIPT_NAME} error: {err_msg[:200]}',
                 traceback.format_exc())

    finally:
        # Update batch execEndDate regardless of success/failure
        if conn and cursor and batch_id:
            try:
                update_batch(cursor, batch_id)
                conn.commit()
                _log(f'Batch closed   (S3BucketBatchId={batch_id}, '
                     f'buckets={bucket_count}, total={bytes_to_human(total_bytes)})')
            except Exception as e:
                _log(f'[WARN] Could not update batch record: {e}')

        if cursor:
            cursor.close()
        if conn:
            conn.close()

        # --- Notify: job ended -------------------------------------------
        summary_line = (f'{bucket_count} buckets sized, '
                        f'total {bytes_to_human(total_bytes)} ({total_bytes:,} bytes)')
        if had_error:
            _job_log('error', f'{SCRIPT_NAME} completed with errors. {summary_line}')
        else:
            _job_log('end',
                     f'{SCRIPT_NAME} completed successfully. {summary_line}',
                     summary_line,
                     {'summary': summary_line, 'warnings': 0, 'errors': 0})

        _log(f'END  {SCRIPT_NAME}')
        _log('=' * 60)

        if log_fh:
            log_fh.close()

    sys.exit(1 if had_error else 0)


if __name__ == '__main__':
    main()

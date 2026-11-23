#!/usr/bin/env python3
"""CLI interface for Milestone 4: Excel/CSV batch research."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from harvey.batch_research import (
    create_batch_from_file,
    export_batch_results,
    mask_email,
    mask_phone,
    run_batch,
)
from harvey.state import StateManager


def _get_state() -> StateManager:
    db_path = os.environ.get("HARVEY_DB_PATH")
    if db_path:
        return StateManager(db_path=db_path)
    return StateManager()


async def cmd_create(args: argparse.Namespace) -> None:
    input_path = Path(args.file)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "rb") as f:
        content_bytes = f.read()

    st = _get_state()
    res = await create_batch_from_file(
        content_bytes=content_bytes,
        filename=input_path.name,
        state=st,
        max_domains=args.max_domains,
    )

    print("\n=======================================================")
    print("                BATCH VALIDATION PREVIEW               ")
    print("=======================================================")
    print(f"Batch ID:        {res['batch_id']}")
    print(f"Source File:     {res['source_filename']}")
    print(f"Source SHA256:   {res['source_sha256']}")
    print(f"Total Rows:      {res['total_rows']}")
    print(f"Valid Rows:      {res['valid_rows']}")
    print(f"Unique Domains:  {res['unique_domains']}")
    print(f"Max Domains Cap: {res['max_domains']} (capped: {res['capped_domains']}, skipped: {res['skipped_domains']})")
    print("-------------------------------------------------------")
    print("Mapped Columns:")
    for k, v in res["column_mapping"].items():
        print(f"  {k} -> {v}")
    print("-------------------------------------------------------")
    print("Sample Validated Rows (PII Masked):")
    for r in res["preview"]:
        print(f"  Row {r['row_number']}: {r['name']} | {r['email_masked']} | {r['phone_masked']} | {r['domain']} | {r['row_status']} {r['error']}")
    print("=======================================================\n")
    print(f"To run batch: python scripts/import_batch.py run {res['batch_id']} --max-domains {args.max_domains}")


async def cmd_run(args: argparse.Namespace) -> None:
    st = _get_state()
    batch = await st.get_import_batch(args.batch_id)
    if not batch:
        print(f"Error: Batch '{args.batch_id}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"\nStarting batch run: {args.batch_id} (resume={args.resume}, force={args.force})")
    res = await run_batch(
        batch_id=args.batch_id,
        state=st,
        max_domains=args.max_domains,
        resume=args.resume,
        force=args.force,
    )

    print("\n=======================================================")
    print("                  BATCH RUN COMPLETED                  ")
    print("=======================================================")
    print(f"Batch ID:          {res['batch_id']}")
    print(f"Final Status:      {res['status']}")
    print(f"Domains Processed: {res['total_domains_processed']} (Completed: {res['completed_domains']}, Failed: {res['failed_domains']})")
    print("-------------------------------------------------------")
    for d in res["domains"]:
        dom = d.get("domain")
        status = d.get("status")
        run_id = d.get("run_id", "")
        if status == "completed":
            print(f"  [COMPLETED] {dom} (run_id: {run_id}) | Web: {d.get('website_status')} | Pages: {d.get('pages_scanned')} | DMs: {d.get('decision_makers_count')}")
        else:
            print(f"  [FAILED]    {dom} | Error: {d.get('error')}")
    print("=======================================================\n")


async def cmd_status(args: argparse.Namespace) -> None:
    st = _get_state()
    batch = await st.get_import_batch(args.batch_id)
    if not batch:
        print(f"Error: Batch '{args.batch_id}' not found.", file=sys.stderr)
        sys.exit(1)

    domains = await st.get_import_domains(args.batch_id)
    print("\n=======================================================")
    print(f"Batch ID:        {batch['id']}")
    print(f"Status:          {batch['status']}")
    print(f"Source File:     {batch['source_filename']}")
    print(f"Total Rows:      {batch['total_rows']}")
    print(f"Unique Domains:  {batch['unique_domains']}")
    print("-------------------------------------------------------")
    for d in domains:
        print(f"  {d['domain']}: status={d['status']}, run_id={d['run_id'] or 'none'}, error={d['error'] or 'none'}")
    print("=======================================================\n")


async def cmd_export(args: argparse.Namespace) -> None:
    st = _get_state()
    res = await export_batch_results(
        batch_id=args.batch_id,
        state=st,
        export_dir=args.output_dir,
    )
    print("\n=======================================================")
    print("                  EXPORT COMPLETED                     ")
    print("=======================================================")
    print(f"Contacts Results CSV: {res['contacts_results']}")
    print(f"Decision Makers CSV:  {res['decision_makers']}")
    print("=======================================================\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvey Excel/CSV Batch Research CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = subparsers.add_parser("create", help="Validate and create a new research batch from file")
    p_create.add_argument("file", help="Path to .csv or .xlsx file")
    p_create.add_argument("--max-domains", type=int, default=5, help="Maximum unique domains to process (default: 5)")

    # run
    p_run = subparsers.add_parser("run", help="Run batch research")
    p_run.add_argument("batch_id", help="Batch ID to process")
    p_run.add_argument("--max-domains", type=int, default=5, help="Maximum unique domains to process (default: 5)")
    p_run.add_argument("--resume", action="store_true", help="Resume pending/failed domains, skip completed")
    p_run.add_argument("--force", action="store_true", help="Force re-run on completed domains")

    # status
    p_status = subparsers.add_parser("status", help="Get batch status and domain progress")
    p_status.add_argument("batch_id", help="Batch ID")

    # export
    p_export = subparsers.add_parser("export", help="Export batch results to CSV")
    p_export.add_argument("batch_id", help="Batch ID")
    p_export.add_argument("--output-dir", default="data/exports", help="Output directory (default: data/exports)")

    args = parser.parse_args()

    if args.command == "create":
        asyncio.run(cmd_create(args))
    elif args.command == "run":
        asyncio.run(cmd_run(args))
    elif args.command == "status":
        asyncio.run(cmd_status(args))
    elif args.command == "export":
        asyncio.run(cmd_export(args))


if __name__ == "__main__":
    main()

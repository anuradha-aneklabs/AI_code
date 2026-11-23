"""Excel/CSV Batch Research Engine (Milestone 4).

Processes contact lists (CSV/Excel), deduplicates per company domain, executes
sequential end-to-end research runs (email validation -> website verification ->
page fetching -> narrative extraction -> decision-maker discovery), commits
progressively after each domain, and exports comprehensive results.
"""

from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harvey.company_narrative import extract_company_narrative, is_valid_service_name
from harvey.decision_makers import find_decision_makers
from harvey.entity_cross_check import check_entity_match
from harvey.single_domain_research import (
    load_free_email_providers,
    run_single_email_research,
    validate_and_normalize_email,
)
from harvey.state import StateManager

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def mask_email(email: str) -> str:
    """Mask email for privacy: first 2 letters of local part + ***@domain."""
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        masked_local = local + "***"
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


def mask_phone(phone: str) -> str:
    """Mask phone number for privacy: show only last 2 digits."""
    clean = re.sub(r"\s+", " ", str(phone or "")).strip()
    if not clean:
        return ""
    digits = re.sub(r"\D", "", clean)
    if len(digits) <= 2:
        return "***" + digits
    return f"***-***-**{digits[-2:]}"


def load_import_columns_config(config_path: str | Path | None = None) -> dict[str, list[str]]:
    """Load column header alias mapping from config."""
    p = Path(config_path or "config/import_columns.json")
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load import columns config from %s: %s", p, e)
    return {
        "email": ["email", "e-mail", "email address", "e-mail address", "contact email", "work email", "email_address", "mail"],
        "name": ["name", "full name", "full_name", "contact name", "contact_name", "person", "lead name", "first and last name"],
        "first_name": ["first name", "firstname", "first_name", "first"],
        "middle_name": ["middle name", "middlename", "middle_initial", "middleinitial", "middle", "mi"],
        "last_name": ["last name", "lastname", "last_name", "last", "surname"],
        "suffix": ["suffix", "name suffix"],
        "company_name": ["entityname", "company", "organization", "organisation", "licensee", "business name", "company name", "entity_name", "business_name"],
        "phone": ["phone", "mobile", "contact number", "phone number", "phone_number", "telephone", "cell"],
    }


_EMAIL_NEGATIVE_RE = re.compile(r"(valid|status|verified|score|flag)", re.IGNORECASE)


def _norm_header(s: str) -> str:
    """Normalize header string: lowercase and remove spaces, hyphens, and underscores."""
    return re.sub(r"[\s_\-]+", "", s.lower().strip())


def parse_import_file(
    content_bytes: bytes,
    filename: str,
    config: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Parse a CSV or Excel file and detect mapped columns.
    
    Returns (rows, column_mapping).
    Raises ValueError on missing email column or unsupported file format.
    """
    col_cfg = config or load_import_columns_config()
    ext = Path(filename).suffix.lower()

    if ext == ".xlsx":
        try:
            import openpyxl
        except ImportError:
            raise ValueError(
                "Excel (.xlsx) support requires openpyxl, which is not installed. Please upload a .csv file."
            )
        wb = openpyxl.load_workbook(filename=io.BytesIO(content_bytes), data_only=True)
        sheet = wb.active
        raw_rows = list(sheet.iter_rows(values_only=True))
        if not raw_rows:
            return [], {}
        headers = [str(c or "").strip() for c in raw_rows[0]]
        data_rows = raw_rows[1:]
    elif ext in (".csv", ".txt") or not ext:
        # Standard library CSV with BOM/encoding tolerance
        text_content = ""
        for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                text_content = content_bytes.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if not text_content:
            text_content = content_bytes.decode("utf-8", errors="replace")

        reader = csv.reader(io.StringIO(text_content))
        raw_rows = list(reader)
        if not raw_rows:
            return [], {}
        headers = [str(c or "").strip() for c in raw_rows[0]]
        data_rows = raw_rows[1:]
    else:
        raise ValueError(f"Unsupported file format '{ext}'. Supported formats: .csv, .xlsx")

    # Map headers to canonical field names (email, company_name, first_name, middle_name, last_name, suffix, name, phone)
    col_mapping: dict[str, int] = {}
    normalized_headers = [_norm_header(h) for h in headers]

    role_priority = [
        "email",
        "company_name",
        "first_name",
        "middle_name",
        "last_name",
        "suffix",
        "name",
        "phone",
    ]

    # Pass 1: exact normalized alias matches
    for canonical in role_priority:
        if canonical not in col_cfg:
            continue
        aliases = col_cfg[canonical]
        for alias in aliases:
            norm_alias = _norm_header(alias)
            for idx, nh in enumerate(normalized_headers):
                if nh == norm_alias and idx not in col_mapping.values():
                    if canonical == "email" and _EMAIL_NEGATIVE_RE.search(headers[idx]):
                        continue
                    col_mapping[canonical] = idx
                    break
            if canonical in col_mapping:
                break

    # Pass 2: token matching for unmapped roles
    for canonical in role_priority:
        if canonical in col_mapping or canonical not in col_cfg:
            continue
        aliases = col_cfg[canonical]
        for alias in aliases:
            alias_tokens = set(re.findall(r"[a-z0-9]+", alias.lower()))
            if not alias_tokens:
                continue
            for idx, h in enumerate(headers):
                if idx in col_mapping.values():
                    continue
                header_tokens = set(re.findall(r"[a-z0-9]+", h.lower()))
                if alias_tokens.issubset(header_tokens):
                    if canonical == "email" and _EMAIL_NEGATIVE_RE.search(h):
                        continue
                    col_mapping[canonical] = idx
                    break
            if canonical in col_mapping:
                break

    if "email" not in col_mapping:
        raise ValueError(
            f"Missing required email column. Found headers: {headers}. "
            f"Expected one of: {col_cfg.get('email', [])}"
        )

    parsed_rows: list[dict[str, Any]] = []
    physical_row_number = 1  # 1 is header row

    for row_vals in data_rows:
        physical_row_number += 1
        # Check if entire row is blank
        str_vals = [str(v or "").strip() for v in row_vals]
        if not any(str_vals):
            continue

        def _get_val(col_name: str) -> str:
            if col_name in col_mapping and col_mapping[col_name] < len(row_vals):
                val = row_vals[col_mapping[col_name]]
                return str(val or "").strip()
            return ""

        # Compose name: if single 'name' column exists and has value, use it.
        # Otherwise combine first_name, middle_name, last_name, suffix.
        raw_name = ""
        if "name" in col_mapping and _get_val("name"):
            raw_name = _get_val("name")
        else:
            name_parts = [
                _get_val(comp)
                for comp in ("first_name", "middle_name", "last_name", "suffix")
                if _get_val(comp)
            ]
            raw_name = " ".join(name_parts).strip()

        company_hint = _get_val("company_name")

        parsed_rows.append({
            "row_number": physical_row_number,
            "raw_email": _get_val("email"),
            "raw_name": raw_name,
            "raw_phone": _get_val("phone"),
            "company_name_hint": company_hint,
        })

    canonical_headers = {k: headers[idx] for k, idx in col_mapping.items()}
    return parsed_rows, canonical_headers


async def create_batch_from_file(
    content_bytes: bytes,
    filename: str,
    state: StateManager,
    max_domains: int = 5,
    free_providers_file: str | None = None,
) -> dict[str, Any]:
    """Parse file, validate contacts, classify domains, and record batch in SQLite."""
    await state.init_db()
    col_cfg = load_import_columns_config()
    parsed_rows, col_mapping = parse_import_file(content_bytes, filename, col_cfg)

    file_sha256 = hashlib.sha256(content_bytes).hexdigest()
    batch_id = _new_id()
    free_mail_domains = load_free_email_providers(free_providers_file)

    validated_rows: list[dict[str, Any]] = []
    seen_domains: dict[str, str] = {}  # domain -> representative email
    unique_domains_list: list[str] = []

    for r in parsed_rows:
        raw_email = r["raw_email"]
        raw_name = r["raw_name"]
        raw_phone = r["raw_phone"]
        company_hint = r.get("company_name_hint", "")
        row_num = r["row_number"]

        if not raw_email:
            validated_rows.append({
                "id": _new_id(),
                "batch_id": batch_id,
                "row_number": row_num,
                "raw_name": raw_name,
                "raw_email": "",
                "raw_phone": raw_phone,
                "company_name_hint": company_hint,
                "normalized_email": "",
                "domain": "",
                "row_status": "missing_email",
                "error": "Missing email address",
            })
            continue

        val_res = validate_and_normalize_email(raw_email, free_mail_domains)
        if not val_res.valid_syntax:
            validated_rows.append({
                "id": _new_id(),
                "batch_id": batch_id,
                "row_number": row_num,
                "raw_name": raw_name,
                "raw_email": raw_email,
                "raw_phone": raw_phone,
                "company_name_hint": company_hint,
                "normalized_email": val_res.normalized_email,
                "domain": "",
                "row_status": "invalid_email",
                "error": val_res.error or "Invalid email syntax",
            })
            continue

        if val_res.is_free_mail:
            validated_rows.append({
                "id": _new_id(),
                "batch_id": batch_id,
                "row_number": row_num,
                "raw_name": raw_name,
                "raw_email": raw_email,
                "raw_phone": raw_phone,
                "company_name_hint": company_hint,
                "normalized_email": val_res.normalized_email,
                "domain": val_res.domain,
                "row_status": "free_mail",
                "error": "Consumer/free email provider - domain research skipped",
            })
            continue

        domain = val_res.domain
        if domain in seen_domains:
            # Duplicate domain contact: shares research run
            validated_rows.append({
                "id": _new_id(),
                "batch_id": batch_id,
                "row_number": row_num,
                "raw_name": raw_name,
                "raw_email": raw_email,
                "raw_phone": raw_phone,
                "company_name_hint": company_hint,
                "normalized_email": val_res.normalized_email,
                "domain": domain,
                "row_status": "duplicate_domain",
                "error": f"Shares research run with {seen_domains[domain]}",
            })
        else:
            seen_domains[domain] = val_res.normalized_email
            unique_domains_list.append(domain)
            domain_idx = len(unique_domains_list)

            if domain_idx > max_domains:
                validated_rows.append({
                    "id": _new_id(),
                    "batch_id": batch_id,
                    "row_number": row_num,
                    "raw_name": raw_name,
                    "raw_email": raw_email,
                    "raw_phone": raw_phone,
                    "company_name_hint": company_hint,
                    "normalized_email": val_res.normalized_email,
                    "domain": domain,
                    "row_status": "skipped_limit",
                    "error": f"Domain #{domain_idx} exceeds batch cap of {max_domains} domains",
                })
            else:
                validated_rows.append({
                    "id": _new_id(),
                    "batch_id": batch_id,
                    "row_number": row_num,
                    "raw_name": raw_name,
                    "raw_email": raw_email,
                    "raw_phone": raw_phone,
                    "company_name_hint": company_hint,
                    "normalized_email": val_res.normalized_email,
                    "domain": domain,
                    "row_status": "valid",
                    "error": "",
                })

    valid_rows_count = sum(1 for r in validated_rows if r["row_status"] in ("valid", "duplicate_domain"))
    total_unique_domains = len(unique_domains_list)

    # Create batch in SQLite
    await state.create_import_batch(
        batch_id=batch_id,
        source_filename=filename,
        source_sha256=file_sha256,
        total_rows=len(validated_rows),
        valid_rows=valid_rows_count,
        unique_domains=total_unique_domains,
        max_domains=max_domains,
        status="pending",
    )

    # Bulk insert rows
    await state.save_import_rows_bulk(validated_rows)

    # Insert domain records
    for idx, domain in enumerate(unique_domains_list, start=1):
        rep_email = seen_domains[domain]
        dom_status = "pending" if idx <= max_domains else "skipped"
        dom_err = "" if idx <= max_domains else f"Exceeded max domains cap ({max_domains})"
        await state.save_import_domain(
            domain_id=_new_id(),
            batch_id=batch_id,
            domain=domain,
            representative_email=rep_email,
            status=dom_status,
            error=dom_err,
        )

    # Prepare privacy-safe preview
    preview_rows = []
    for r in validated_rows[:5]:
        preview_rows.append({
            "row_number": r["row_number"],
            "name": r["raw_name"],
            "company_name_hint": r.get("company_name_hint", ""),
            "email_masked": mask_email(r["raw_email"]),
            "phone_masked": mask_phone(r["raw_phone"]),
            "domain": r["domain"],
            "row_status": r["row_status"],
            "error": r["error"],
        })

    return {
        "batch_id": batch_id,
        "source_filename": filename,
        "source_sha256": file_sha256,
        "total_rows": len(validated_rows),
        "valid_rows": valid_rows_count,
        "unique_domains": total_unique_domains,
        "max_domains": max_domains,
        "capped_domains": min(total_unique_domains, max_domains),
        "skipped_domains": max(0, total_unique_domains - max_domains),
        "column_mapping": col_mapping,
        "preview": preview_rows,
    }


async def run_batch(
    batch_id: str,
    state: StateManager,
    max_domains: int | None = None,
    resume: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Execute batch research pipeline sequentially domain by domain.
    
    Progressively commits status to SQLite after each domain.
    """
    await state.init_db()
    batch = await state.get_import_batch(batch_id)
    if not batch:
        raise ValueError(f"Import batch '{batch_id}' not found.")

    domains = await state.get_import_domains(batch_id)
    if not domains:
        raise ValueError(f"No domains found for batch '{batch_id}'.")

    effective_max = max_domains or batch["max_domains"] or 5
    started_at = _utcnow()
    await state.update_import_batch_status(batch_id, status="running", started_at=started_at)

    domains_to_process = []
    for d in domains:
        if d["status"] == "skipped" and not force:
            continue
        if resume:
            if d["status"] == "completed" and not force:
                continue
            domains_to_process.append(d)
        else:
            if d["status"] == "completed" and not force:
                continue
            domains_to_process.append(d)

    # Respect domain cap
    domains_to_process = domains_to_process[:effective_max]

    completed_count = 0
    failed_count = 0
    results_summary: list[dict[str, Any]] = []

    for d in domains_to_process:
        domain = d["domain"]
        rep_email = d["representative_email"]
        dom_start = _utcnow()

        await state.update_import_domain_status(
            batch_id,
            domain,
            status="running",
            started_at=dom_start,
        )

        try:
            # 1. Run single email research (M1 & M1.1: email val, website detect/verify, page crawl)
            res_output = await run_single_email_research(
                email=rep_email,
                state=state,
            )
            run_id = res_output.get("id") or res_output.get("run_id", "")

            # 2. Extract company narrative (M2: overview, services, vision, mission)
            narrative = await extract_company_narrative(run_id, state=state)

            # 3. Discover decision makers (M3: evidence-first, officer context validation)
            people = await find_decision_makers(run_id, state=state)

            # Gather summary details
            website = await state.get_company_website(run_id)
            pages = await state.get_source_pages(run_id)

            web_status = website.get("verification_status", "unavailable") if website else "unavailable"
            web_url = website.get("final_url", "") if website else ""

            # Narrative status
            overview_item = narrative.get("overview") if isinstance(narrative, dict) else None
            overview_status = overview_item.get("status", "unavailable") if isinstance(overview_item, dict) else "unavailable"

            # Decision makers summary
            active_people = [p for p in people if p.get("verification_status") != "unavailable" and not p.get("excluded_reason")]
            unavailable_people = [p for p in people if p.get("verification_status") == "unavailable"]

            # Entity cross-check against hint(s) from import_rows
            all_rows = await state.get_import_rows(batch_id)
            domain_hints = list(dict.fromkeys(
                r.get("company_name_hint", "").strip()
                for r in all_rows
                if r.get("domain") == domain and r.get("company_name_hint", "").strip()
            ))

            entity_check = "no_hint"
            entity_check_evidence = ""
            if domain_hints:
                for hint in domain_hints:
                    ec_stat, ec_ev = check_entity_match(hint, pages=pages, website=website)
                    if ec_stat == "match":
                        entity_check = "match"
                        entity_check_evidence = ec_ev
                        break
                if entity_check != "match":
                    entity_check = "no_match"
                    entity_check_evidence = ""

            stage_summary = {
                "website_status": web_status,
                "website_url": web_url,
                "pages_scanned": len(pages),
                "overview_status": overview_status,
                "decision_makers_found": len(active_people),
                "has_unavailable_row": len(unavailable_people) > 0,
                "entity_check": entity_check,
                "entity_check_evidence": entity_check_evidence,
            }

            dom_end = _utcnow()
            dom_status = "completed"
            completed_count += 1

            await state.update_import_domain_status(
                batch_id,
                domain,
                status=dom_status,
                run_id=run_id,
                entity_check=entity_check,
                entity_check_evidence=entity_check_evidence,
                stage_summary_json=json.dumps(stage_summary),
                error="",
                finished_at=dom_end,
            )

            results_summary.append({
                "domain": domain,
                "status": dom_status,
                "run_id": run_id,
                "website_status": web_status,
                "website_url": web_url,
                "pages_scanned": len(pages),
                "decision_makers_count": len(active_people),
                "entity_check": entity_check,
                "entity_check_evidence": entity_check_evidence,
            })

        except Exception as e:
            logger.exception("Error processing domain %s in batch %s: %s", domain, batch_id, e)
            dom_end = _utcnow()
            dom_status = "failed"
            failed_count += 1

            await state.update_import_domain_status(
                batch_id,
                domain,
                status=dom_status,
                error=str(e),
                finished_at=dom_end,
            )

            results_summary.append({
                "domain": domain,
                "status": dom_status,
                "run_id": "",
                "error": str(e),
            })

    finished_at = _utcnow()
    total_processed = completed_count + failed_count
    if failed_count == 0:
        batch_final_status = "completed"
    elif completed_count > 0:
        batch_final_status = "partial"
    else:
        batch_final_status = "failed"

    await state.update_import_batch_status(
        batch_id,
        status=batch_final_status,
        finished_at=finished_at,
        error_summary="" if failed_count == 0 else f"{failed_count} domain(s) failed out of {total_processed}",
    )

    return {
        "batch_id": batch_id,
        "status": batch_final_status,
        "completed_domains": completed_count,
        "failed_domains": failed_count,
        "total_domains_processed": total_processed,
        "domains": results_summary,
        "finished_at": finished_at,
    }


async def export_batch_results(
    batch_id: str,
    state: StateManager,
    export_dir: str | Path = "data/exports",
) -> dict[str, str]:
    """Export batch results to CSV files in data/exports/.
    
    Produces:
    1. contacts_results_<batch_id>.csv
    2. decision_makers_<batch_id>.csv
    """
    await state.init_db()
    batch = await state.get_import_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch '{batch_id}' not found.")

    rows = await state.get_import_rows(batch_id)
    domains = await state.get_import_domains(batch_id)
    domain_map = {d["domain"]: d for d in domains}

    out_dir = Path(export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    contacts_csv_path = out_dir / f"contacts_results_{batch_id}.csv"
    decision_makers_csv_path = out_dir / f"decision_makers_{batch_id}.csv"

    # Pre-fetch domain research runs
    runs_by_domain: dict[str, dict[str, Any]] = {}
    for d in domains:
        run_id = d.get("run_id")
        if run_id:
            run_data = await state.get_research_run_full(run_id)
            if run_data:
                runs_by_domain[d["domain"]] = run_data

    # 1. Export contacts_results.csv
    # Columns: row_number, name, (company_name_hint if present), email, phone, row_status, domain, (entity_check if present),
    # website status and URL, overview + its status, up to 5 services, vision, mission, decision makers, source URLs, run_id, error
    has_company_hints = any(r.get("company_name_hint") for r in rows)

    if has_company_hints:
        contact_fieldnames = [
            "row_number",
            "name",
            "company_name_hint",
            "email",
            "phone",
            "row_status",
            "domain",
            "entity_check",
            "website_status",
            "website_url",
            "overview",
            "overview_status",
            "services",
            "vision",
            "mission",
            "decision_makers",
            "source_urls",
            "run_id",
            "error",
        ]
    else:
        contact_fieldnames = [
            "row_number",
            "name",
            "email",
            "phone",
            "row_status",
            "domain",
            "website_status",
            "website_url",
            "overview",
            "overview_status",
            "services",
            "vision",
            "mission",
            "decision_makers",
            "source_urls",
            "run_id",
            "error",
        ]

    with open(contacts_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=contact_fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()

        for r in rows:
            dom = r.get("domain", "")
            r_stat = r.get("row_status", "")
            run_data = runs_by_domain.get(dom)
            company_hint = r.get("company_name_hint", "")
            d_rec = domain_map.get(dom, {})
            dom_entity_check = d_rec.get("entity_check", "no_hint")
            dom_entity_ev = d_rec.get("entity_check_evidence", "")

            if r_stat == "free_mail":
                free_msg = "free-mail address; website not researched; web search is a later milestone"
                run_id = ""
                web_status = "unavailable"
                web_url = f"unavailable: {free_msg}"
                overview_stat = "unavailable"
                overview_val = f"unavailable: {free_msg}"
                services_val = f"unavailable: {free_msg}"
                vision_val = f"unavailable: {free_msg}"
                mission_val = f"unavailable: {free_msg}"
                dm_val = f"unavailable: {free_msg}"
                source_urls_val = ""
                row_err = r.get("error", "")
                entity_check_val = f"unavailable: {free_msg}"
            elif run_data:
                run_id = run_data["id"]
                website = run_data.get("website") or {}
                findings_raw = run_data.get("findings") or []
                findings_list: list[dict[str, Any]] = []
                if isinstance(findings_raw, list):
                    findings_list = [f for f in findings_raw if isinstance(f, dict)]
                elif isinstance(findings_raw, dict):
                    for k, v in findings_raw.items():
                        if isinstance(v, list):
                            findings_list.extend([x for x in v if isinstance(x, dict)])
                        elif isinstance(v, dict):
                            item = dict(v)
                            if "finding_type" not in item:
                                item["finding_type"] = k
                            findings_list.append(item)

                by_type: dict[str, list[dict[str, Any]]] = {}
                for f in findings_list:
                    ftype = f.get("finding_type", "")
                    by_type.setdefault(ftype, []).append(f)

                people = run_data.get("people") or []
                pages = run_data.get("pages") or []

                web_status = website.get("verification_status", "unavailable")
                web_url = website.get("final_url", "")
                if web_status == "unavailable" and not web_url:
                    web_url = f"unavailable: {website.get('reason', 'Website unreachable or DNS failed')}"

                # Entity check display
                if dom_entity_check == "match":
                    entity_check_val = f"match: {dom_entity_ev}" if dom_entity_ev else "match"
                elif dom_entity_check == "no_match":
                    entity_check_val = "no_match: domain may belong to a filer, consultant or parent organisation"
                else:
                    entity_check_val = "no_hint"

                # 1. Overview extraction
                ov_items = by_type.get("overview") or []
                if ov_items:
                    ov_item = ov_items[0]
                    ov_val = (ov_item.get("value_text") or ov_item.get("finding_value") or "").strip()
                    ov_stat = ov_item.get("verification_status") or ov_item.get("status") or "unavailable"
                    if ov_stat == "unavailable" or not ov_val or ov_val == "unavailable":
                        reason = ov_item.get("reason") or "Overview not found on scanned pages"
                        overview_val = f"unavailable: {reason}"
                        overview_stat = "unavailable"
                    else:
                        overview_val = ov_val
                        overview_stat = ov_stat
                else:
                    overview_val = "unavailable: Overview not found on scanned pages"
                    overview_stat = "unavailable"

                # 2. Vision extraction
                vis_items = by_type.get("vision") or []
                if vis_items:
                    v_item = vis_items[0]
                    v_val = (v_item.get("value_text") or v_item.get("finding_value") or "").strip()
                    v_stat = v_item.get("verification_status") or v_item.get("status") or "unavailable"
                    if v_stat == "unavailable" or not v_val or v_val == "unavailable":
                        reason = v_item.get("reason") or "Vision statement not found on scanned pages"
                        vision_val = f"unavailable: {reason}"
                    else:
                        vision_val = f"{v_val} [{v_stat}]" if not v_val.endswith(f"[{v_stat}]") else v_val
                else:
                    vision_val = "unavailable: Vision statement not found on scanned pages"

                # 3. Mission extraction
                mis_items = by_type.get("mission") or []
                if mis_items:
                    m_item = mis_items[0]
                    m_val = (m_item.get("value_text") or m_item.get("finding_value") or "").strip()
                    m_stat = m_item.get("verification_status") or m_item.get("status") or "unavailable"
                    if m_stat == "unavailable" or not m_val or m_val == "unavailable":
                        reason = m_item.get("reason") or "Mission statement not found on scanned pages"
                        mission_val = f"unavailable: {reason}"
                    else:
                        mission_val = f"{m_val} [{m_stat}]" if not m_val.endswith(f"[{m_stat}]") else m_val
                else:
                    mission_val = "unavailable: Mission statement not found on scanned pages"

                # 4. Services (product_service) extraction
                svc_items = [
                    f for f in (by_type.get("product_service", []) + by_type.get("services", []))
                    if f.get("verification_status") != "unavailable" and f.get("status") != "unavailable"
                ]
                extracted_services: list[str] = []
                for s in svc_items:
                    raw_val = s.get("value_text") or s.get("finding_value") or ""
                    s_stat = s.get("verification_status") or s.get("status") or "verified"
                    if not raw_val or raw_val == "unavailable":
                        continue
                    if isinstance(raw_val, str) and raw_val.startswith("["):
                        try:
                            parsed = json.loads(raw_val)
                            if isinstance(parsed, list):
                                for p in parsed:
                                    p_str = str(p).strip()
                                    if p_str and is_valid_service_name(p_str)[0]:
                                        formatted_p = f"{p_str} [{s_stat}]" if not p_str.endswith(f"[{s_stat}]") else p_str
                                        extracted_services.append(formatted_p)
                                continue
                        except Exception:
                            pass
                    raw_str = str(raw_val).strip()
                    if raw_str and is_valid_service_name(raw_str)[0]:
                        formatted_s = f"{raw_str} [{s_stat}]" if not raw_str.endswith(f"[{s_stat}]") else raw_str
                        extracted_services.append(formatted_s)

                # Deduplicate by similarity threshold
                unique_export_services: list[str] = []
                for es in extracted_services:
                    core_name = re.sub(r"\s*\[[^\]]+\]$", "", es).strip().lower()
                    core_norm = re.sub(r"[^a-z0-9]", "", core_name)
                    if not any(
                        difflib.SequenceMatcher(
                            None,
                            core_norm,
                            re.sub(r"[^a-z0-9]", "", re.sub(r"\s*\[[^\]]+\]$", "", u).strip().lower())
                        ).ratio() > 0.7
                        for u in unique_export_services
                    ):
                        unique_export_services.append(es)

                if unique_export_services:
                    services_val = "; ".join(unique_export_services[:5])
                else:
                    unavail_svc = [
                        f for f in (by_type.get("product_service", []) + by_type.get("services", []))
                        if f.get("verification_status") == "unavailable" or f.get("status") == "unavailable" or f.get("reason")
                    ]
                    reason = unavail_svc[0].get("reason") if unavail_svc else ""
                    services_val = f"unavailable: {reason or 'no clear service list found'}"

                # Decision makers: format as "Name - Title [status]"
                # Excluded rows never appear!
                active_people = [
                    p for p in people
                    if not p.get("excluded_reason") and p.get("verification_status") != "unavailable"
                ]
                if active_people:
                    dm_parts = "; ".join(f"{p['full_name']} - {p['title']} [{p['verification_status']}]" for p in active_people)
                    if dom_entity_check == "no_match":
                        dm_val = f"[WARNING: domain may belong to a filer, consultant or parent organisation] {dm_parts}"
                    else:
                        dm_val = dm_parts
                else:
                    unavail_p = [p for p in people if p.get("verification_status") == "unavailable"]
                    if unavail_p:
                        dm_val = f"unavailable: {unavail_p[0].get('reason', 'No decision makers found')}"
                    else:
                        dm_val = "unavailable: no leadership found"

                source_urls_val = "; ".join(p.get("url", "") for p in pages if p.get("url"))
                row_err = r.get("error", "")

            else:
                run_id = ""
                web_status = "unavailable"
                web_url = f"unavailable: {r_stat}"
                overview_stat = "unavailable"
                overview_val = f"unavailable: {r_stat}"
                services_val = f"unavailable: {r_stat}"
                vision_val = f"unavailable: {r_stat}"
                mission_val = f"unavailable: {r_stat}"
                dm_val = f"unavailable: {r_stat}"
                source_urls_val = ""
                row_err = r.get("error", "")
                entity_check_val = "unavailable"

            out_row = {
                "row_number": r["row_number"],
                "name": r["raw_name"],
                "email": r["raw_email"],
                "phone": r["raw_phone"],
                "row_status": r_stat,
                "domain": dom,
                "website_status": web_status,
                "website_url": web_url,
                "overview": overview_val,
                "overview_status": overview_stat,
                "services": services_val,
                "vision": vision_val,
                "mission": mission_val,
                "decision_makers": dm_val,
                "source_urls": source_urls_val,
                "run_id": run_id,
                "error": row_err,
            }
            if has_company_hints:
                out_row["company_name_hint"] = company_hint
                out_row["entity_check"] = entity_check_val

            writer.writerow(out_row)

    # 2. Export decision_makers.csv
    # Columns: domain, name, title, role_category, status, source_url, evidence up to 300 chars
    dm_fieldnames = [
        "domain",
        "name",
        "title",
        "role_category",
        "status",
        "source_url",
        "evidence",
    ]

    with open(decision_makers_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=dm_fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()

        for d in domains:
            dom = d["domain"]
            dom_entity_check = d.get("entity_check", "no_hint")
            run_data = runs_by_domain.get(dom)
            if not run_data:
                writer.writerow({
                    "domain": dom,
                    "name": "unavailable",
                    "title": "unavailable",
                    "role_category": "unavailable",
                    "status": "unavailable",
                    "source_url": "",
                    "evidence": f"unavailable: {d.get('status', 'not_run')}",
                })
                continue

            people = run_data.get("people") or []
            # Excluded rows NEVER appear!
            clean_people = [p for p in people if not p.get("excluded_reason")]

            if not clean_people:
                writer.writerow({
                    "domain": dom,
                    "name": "unavailable",
                    "title": "unavailable",
                    "role_category": "unavailable",
                    "status": "unavailable",
                    "source_url": "",
                    "evidence": "unavailable: no verified officers found",
                })
            else:
                for p in clean_people:
                    ev = p.get("evidence_text", "")
                    if dom_entity_check == "no_match":
                        ev = f"[domain may belong to a filer, consultant or parent organisation] {ev}"
                    if len(ev) > 300:
                        ev = ev[:297] + "..."
                    writer.writerow({
                        "domain": dom,
                        "name": p.get("full_name", ""),
                        "title": p.get("title", ""),
                        "role_category": p.get("role_category", ""),
                        "status": p.get("verification_status", ""),
                        "source_url": p.get("source_url", ""),
                        "evidence": ev,
                    })

    # Also create canonical symlinks/copies without batch_id for convenience
    std_contacts = out_dir / "contacts_results.csv"
    std_dms = out_dir / "decision_makers.csv"
    try:
        import shutil
        shutil.copyfile(contacts_csv_path, std_contacts)
        shutil.copyfile(decision_makers_csv_path, std_dms)
    except Exception:
        pass

    return {
        "contacts_results": str(contacts_csv_path),
        "decision_makers": str(decision_makers_csv_path),
    }

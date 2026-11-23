#!/usr/bin/env python3
"""Generate a self-contained HTML demo report for a batch research run.

Standard library only. Read-only on the SQLite database.
Writes: data/exports/demo_report_<batch_id>.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email
    local, _, domain = email.partition("@")
    prefix = local[:2] if len(local) >= 2 else local[:1]
    return f"{prefix}***@{domain}"


def mask_phone(phone: str) -> str:
    clean = re.sub(r"[^\d+]", "", phone or "")
    if len(clean) >= 4:
        return f"***-***-**{clean[-2:]}"
    return "***-**" if clean else ""


def generate_html_report(
    db_path: str,
    batch_id: str,
    output_path: str | Path | None = None,
    unmask: bool = False,
) -> str:
    """Read batch data from SQLite and render a self-contained HTML report."""
    db_p = Path(db_path).resolve()
    if not db_p.exists():
        raise FileNotFoundError(f"Database not found: {db_p}")

    # Standard library read-only URI connection
    conn = sqlite3.connect(f"file:{db_p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT * FROM import_batches WHERE id = ?", (batch_id,))
        batch_row = cur.fetchone()
        if not batch_row:
            raise ValueError(f"Batch '{batch_id}' not found in database.")

        batch = dict(batch_row)

        cur.execute(
            "SELECT * FROM import_domains WHERE batch_id = ? ORDER BY created_at ASC, id ASC",
            (batch_id,),
        )
        domain_rows = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT * FROM import_rows WHERE batch_id = ? ORDER BY row_number ASC",
            (batch_id,),
        )
        import_rows = [dict(r) for r in cur.fetchall()]

        # Pre-fetch research data for each domain
        run_ids = [d["run_id"] for d in domain_rows if d.get("run_id")]

        websites_by_run: dict[str, dict[str, Any]] = {}
        findings_by_run: dict[str, list[dict[str, Any]]] = {}
        people_by_run: dict[str, list[dict[str, Any]]] = {}

        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            cur.execute(
                f"SELECT * FROM company_websites WHERE run_id IN ({placeholders})",
                run_ids,
            )
            for r in cur.fetchall():
                websites_by_run[r["run_id"]] = dict(r)

            cur.execute(
                f"SELECT * FROM research_findings WHERE run_id IN ({placeholders}) ORDER BY created_at ASC",
                run_ids,
            )
            for r in cur.fetchall():
                findings_by_run.setdefault(r["run_id"], []).append(dict(r))

            cur.execute(
                f"SELECT * FROM research_people WHERE run_id IN ({placeholders}) ORDER BY created_at ASC",
                run_ids,
            )
            for r in cur.fetchall():
                people_by_run.setdefault(r["run_id"], []).append(dict(r))

    finally:
        conn.close()

    # Map import rows by domain
    rows_by_domain: dict[str, list[dict[str, Any]]] = {}
    free_mail_rows: list[dict[str, Any]] = []
    for r in import_rows:
        dom = r.get("domain", "")
        if r.get("row_status") == "free_mail":
            free_mail_rows.append(r)
        if dom:
            rows_by_domain.setdefault(dom, []).append(r)

    # Prepare HTML output path
    if output_path is None:
        out_file = Path("data/exports") / f"demo_report_{batch_id}.html"
    else:
        out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Render HTML template
    cards_html: list[str] = []

    for d in domain_rows:
        dom = d["domain"]
        run_id = d.get("run_id", "")
        dom_status = d.get("status", "pending")
        entity_check = d.get("entity_check", "no_hint")
        entity_evidence = d.get("entity_check_evidence", "")

        website = websites_by_run.get(run_id, {})
        web_status = website.get("verification_status", "unavailable") if website else "unavailable"
        web_url = website.get("final_url", "") if website else ""

        findings = findings_by_run.get(run_id, [])
        people = people_by_run.get(run_id, [])
        dom_import_rows = rows_by_domain.get(dom, [])

        # Filter clean people (no excluded)
        clean_people = [p for p in people if not p.get("excluded_reason") and p.get("verification_status") != "unavailable"]
        unavail_people = [p for p in people if p.get("verification_status") == "unavailable"]

        # Organize findings by type
        by_type: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            by_type.setdefault(f.get("finding_type", ""), []).append(f)

        # Overview
        ov_list = by_type.get("overview", [])
        if ov_list:
            ov_val = ov_list[0].get("value_text", "")
            ov_stat = ov_list[0].get("verification_status", "unavailable")
            ov_reason = ov_list[0].get("reason", "")
        else:
            ov_val = ""
            ov_stat = "unavailable"
            ov_reason = "No overview section detected on scanned pages"

        # Vision
        vis_list = by_type.get("vision", [])
        if vis_list:
            vis_val = vis_list[0].get("value_text", "")
            vis_stat = vis_list[0].get("verification_status", "unavailable")
            vis_reason = vis_list[0].get("reason", "")
        else:
            vis_val = ""
            vis_stat = "unavailable"
            vis_reason = "No vision statement found on scanned pages"

        # Mission
        mis_list = by_type.get("mission", [])
        if mis_list:
            mis_val = mis_list[0].get("value_text", "")
            mis_stat = mis_list[0].get("verification_status", "unavailable")
            mis_reason = mis_list[0].get("reason", "")
        else:
            mis_val = ""
            mis_stat = "unavailable"
            mis_reason = "No mission statement found on scanned pages"

        # Services
        svc_items = [
            f for f in (by_type.get("product_service", []) + by_type.get("services", []))
            if f.get("verification_status") != "unavailable"
        ]
        svc_unavail = [
            f for f in (by_type.get("product_service", []) + by_type.get("services", []))
            if f.get("verification_status") == "unavailable" or f.get("reason")
        ]
        svc_reason = svc_unavail[0].get("reason", "no clear service list found") if svc_unavail else "no clear service list found"

        # Contact cards
        contacts_badges: list[str] = []
        for cr in dom_import_rows:
            c_name = cr.get("raw_name", "") if unmask else ("[NAME]" if cr.get("raw_name") else "[COMPANY ONLY]")
            c_email = cr.get("raw_email", "") if unmask else mask_email(cr.get("raw_email", ""))
            c_phone = cr.get("raw_phone", "") if unmask else mask_phone(cr.get("raw_phone", ""))
            c_hint = cr.get("company_name_hint", "")
            hint_html = f"<span class='hint-badge'>{html.escape(c_hint)}</span>" if c_hint else ""
            contacts_badges.append(
                f"<div class='contact-pill'><strong>Row {cr['row_number']}:</strong> {html.escape(c_name)} &bull; {html.escape(c_email)} &bull; {html.escape(c_phone)} {hint_html}</div>"
            )

        # Entity check badge
        if entity_check == "match":
            ec_class = "badge-success"
            ec_text = "Entity Match"
            ec_detail = f"<div class='ec-detail match-text'>&check; Matched website content: <em>{html.escape(entity_evidence[:200])}</em></div>"
        elif entity_check == "no_match":
            ec_class = "badge-warning"
            ec_text = "No Entity Match"
            ec_detail = "<div class='ec-detail warn-text'>&excl; Warning: Domain may belong to a filer, consultant or parent organisation. Contacts/leadership may not represent the licensee.</div>"
        else:
            ec_class = "badge-secondary"
            ec_text = "No Hint Given"
            ec_detail = ""

        # Web status badge
        web_class = "badge-success" if web_status == "verified" else "badge-danger"

        # Build people table
        people_html = ""
        if clean_people:
            p_rows = []
            for p in clean_people:
                p_name = p.get("full_name", "") if unmask else "[NAME]"
                p_title = p.get("title", "")
                p_stat = p.get("verification_status", "")
                p_src = p.get("source_url", "")
                p_ev = p.get("evidence_text", "")
                src_link = f"<a href='{html.escape(p_src)}' target='_blank' rel='noopener'>source</a>" if p_src else "&mdash;"
                p_rows.append(
                    f"<tr><td><strong>{html.escape(p_name)}</strong></td><td>{html.escape(p_title)}</td>"
                    f"<td><span class='badge badge-info'>{html.escape(p_stat)}</span></td><td>{src_link}</td>"
                    f"<td class='ev-cell'>{html.escape(p_ev[:280])}{'...' if len(p_ev)>280 else ''}</td></tr>"
                )
            people_html = (
                f"<table class='data-table'><thead><tr><th>Name</th><th>Title</th><th>Status</th><th>Source</th><th>Evidence Context</th></tr></thead>"
                f"<tbody>{''.join(p_rows)}</tbody></table>"
            )
        else:
            unavail_reason = unavail_people[0].get("reason", "no verified officers found") if unavail_people else "No leadership detected"
            people_html = f"<div class='unavail-box'>&empty; Decision Makers Unavailable: {html.escape(unavail_reason)}</div>"

        # Services list
        if svc_items:
            svc_chips = []
            for s in svc_items[:10]:
                val = s.get("value_text", "")
                st = s.get("verification_status", "verified")
                svc_chips.append(f"<span class='chip'>{html.escape(val)} <small>[{html.escape(st)}]</small></span>")
            services_html = f"<div class='chips-wrapper'>{''.join(svc_chips)}</div>"
        else:
            services_html = f"<div class='unavail-box'>&empty; Services Unavailable: {html.escape(svc_reason)}</div>"

        # Overview HTML
        if ov_val and ov_stat != "unavailable":
            ov_html = f"<p class='narrative-text'>{html.escape(ov_val)} <span class='badge badge-info'>{html.escape(ov_stat)}</span></p>"
        else:
            ov_html = f"<div class='unavail-box'>&empty; Overview Unavailable: {html.escape(ov_reason)}</div>"

        # Vision & Mission HTML
        vm_parts = []
        if vis_val and vis_stat != "unavailable":
            vm_parts.append(f"<div><strong>Vision:</strong> {html.escape(vis_val)} <span class='badge badge-info'>{html.escape(vis_stat)}</span></div>")
        else:
            vm_parts.append(f"<div class='unavail-sub'>&empty; Vision Unavailable: {html.escape(vis_reason)}</div>")

        if mis_val and mis_stat != "unavailable":
            vm_parts.append(f"<div><strong>Mission:</strong> {html.escape(mis_val)} <span class='badge badge-info'>{html.escape(mis_stat)}</span></div>")
        else:
            vm_parts.append(f"<div class='unavail-sub'>&empty; Mission Unavailable: {html.escape(mis_reason)}</div>")
        vm_html = "".join(vm_parts)

        card = f"""
        <div class="domain-card">
            <div class="card-header">
                <div class="header-main">
                    <h2><span class="domain-title">{html.escape(dom)}</span></h2>
                    <div class="header-badges">
                        <span class="badge {web_class}">Web: {html.escape(web_status)}</span>
                        <span class="badge {ec_class}">{html.escape(ec_text)}</span>
                        <span class="badge badge-secondary">Status: {html.escape(dom_status)}</span>
                    </div>
                </div>
                {f'<div class="web-url"><a href="{html.escape(web_url)}" target="_blank" rel="noopener">{html.escape(web_url)}</a></div>' if web_url else ''}
                {ec_detail}
            </div>

            <div class="card-section">
                <h4>Associated Source Contacts ({len(dom_import_rows)})</h4>
                <div class="contacts-list">{''.join(contacts_badges)}</div>
            </div>

            <div class="card-section">
                <h4>Company Narrative & Overview</h4>
                {ov_html}
            </div>

            <div class="card-section">
                <h4>Core Services & Offerings</h4>
                {services_html}
            </div>

            <div class="card-section">
                <h4>Vision & Mission</h4>
                <div class="vm-box">{vm_html}</div>
            </div>

            <div class="card-section">
                <h4>Discovered Decision Makers ({len(clean_people)})</h4>
                {people_html}
            </div>
        </div>
        """
        cards_html.append(card)

    # Free-mail rows card if any
    if free_mail_rows:
        fm_items = []
        for r in free_mail_rows:
            c_name = r.get("raw_name", "") if unmask else ("[NAME]" if r.get("raw_name") else "[COMPANY ONLY]")
            c_email = r.get("raw_email", "") if unmask else mask_email(r.get("raw_email", ""))
            c_phone = r.get("raw_phone", "") if unmask else mask_phone(r.get("raw_phone", ""))
            c_hint = r.get("company_name_hint", "")
            fm_items.append(
                f"<div class='contact-pill'><strong>Row {r['row_number']}:</strong> {html.escape(c_name)} &bull; {html.escape(c_email)} &bull; {html.escape(c_phone)} "
                f"<span class='hint-badge'>{html.escape(c_hint)}</span> &mdash; <em>free-mail address; website not researched; web search is a later milestone</em></div>"
            )
        fm_card = f"""
        <div class="domain-card skipped-card">
            <div class="card-header">
                <h2>Free-Mail Provider Rows (Skipped Research: {len(free_mail_rows)})</h2>
                <span class="badge badge-secondary">Skipped</span>
            </div>
            <div class="card-section">
                <p>These contacts use consumer/free email services (e.g. Gmail, Yahoo). Domain research was bypassed by policy.</p>
                <div class="contacts-list">{''.join(fm_items)}</div>
            </div>
        </div>
        """
        cards_html.append(fm_card)

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Harvey Research Batch Demo Report - {html.escape(batch_id)}</title>
    <style>
        :root {{
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --border-color: #e2e8f0;
            --primary-color: #2563eb;
            --text-main: #0f172a;
            --text-muted: #64748b;
            --success: #16a34a;
            --warning: #d97706;
            --danger: #dc2626;
            --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg-color);
            color: var(--text-main);
            font-family: var(--font-family);
            line-height: 1.5;
            padding: 24px;
        }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header-banner {{
            background: linear-gradient(135deg, #1e293b, #0f172a);
            color: #fff;
            padding: 28px 32px;
            border-radius: 12px;
            margin-bottom: 24px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }}
        .header-banner h1 {{ font-size: 26px; margin-bottom: 8px; font-weight: 700; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px;
            margin-top: 20px;
        }}
        .stat-box {{
            background: rgba(255,255,255,0.08);
            padding: 12px 16px;
            border-radius: 8px;
        }}
        .stat-box .label {{ font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; }}
        .stat-box .val {{ font-size: 20px; font-weight: 700; color: #f8fafc; margin-top: 4px; }}
        .domain-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        .domain-card.skipped-card {{ background: #fafafa; border-style: dashed; }}
        .card-header {{ border-bottom: 1px solid var(--border-color); padding-bottom: 16px; margin-bottom: 16px; }}
        .header-main {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }}
        .domain-title {{ font-size: 20px; color: #1e293b; font-weight: 700; }}
        .header-badges {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
        .badge {{
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 600;
            border-radius: 9999px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .badge-success {{ background: #dcfce7; color: var(--success); }}
        .badge-warning {{ background: #fef3c7; color: var(--warning); }}
        .badge-danger {{ background: #fee2e2; color: var(--danger); }}
        .badge-info {{ background: #e0e7ff; color: #4338ca; }}
        .badge-secondary {{ background: #f1f5f9; color: #475569; }}
        .web-url {{ margin-top: 6px; font-size: 13px; }}
        .web-url a {{ color: var(--primary-color); text-decoration: none; }}
        .web-url a:hover {{ text-decoration: underline; }}
        .ec-detail {{ margin-top: 10px; font-size: 13px; padding: 8px 12px; border-radius: 6px; }}
        .match-text {{ background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }}
        .warn-text {{ background: #fffbeb; color: #92400e; border: 1px solid #fde68a; font-weight: 500; }}
        .card-section {{ margin-top: 16px; }}
        .card-section h4 {{ font-size: 14px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px; letter-spacing: 0.5px; }}
        .contacts-list {{ display: flex; flex-direction: column; gap: 6px; }}
        .contact-pill {{ font-size: 13px; background: #f8fafc; padding: 6px 10px; border-radius: 6px; border: 1px solid #e2e8f0; }}
        .hint-badge {{ background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; padding: 2px 6px; border-radius: 4px; font-size: 12px; font-weight: 600; margin-left: 6px; }}
        .narrative-text {{ font-size: 14px; color: #334155; line-height: 1.6; }}
        .chips-wrapper {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }}
        .chip {{ background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; padding: 4px 10px; border-radius: 16px; font-size: 13px; font-weight: 500; }}
        .vm-box {{ display: flex; flex-direction: column; gap: 6px; font-size: 14px; background: #f8fafc; padding: 12px; border-radius: 6px; border: 1px solid #e2e8f0; }}
        .unavail-box {{ background: #f8fafc; color: #64748b; padding: 10px 14px; border-radius: 6px; font-size: 13px; font-style: italic; border: 1px dashed #cbd5e1; }}
        .unavail-sub {{ color: #94a3b8; font-size: 13px; font-style: italic; }}
        .data-table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }}
        .data-table th, .data-table td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
        .data-table th {{ background: #f8fafc; color: #475569; font-weight: 600; }}
        .data-table tr:hover {{ background: #f8fafc; }}
        .ev-cell {{ font-size: 12px; color: #64748b; max-width: 380px; line-height: 1.4; }}
        .footer {{ text-align: center; color: var(--text-muted); font-size: 12px; margin-top: 32px; padding-bottom: 24px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header-banner">
            <h1>Harvey Batch Research Report</h1>
            <p>Source File: <strong>{html.escape(batch.get('source_filename', ''))}</strong> &bull; Batch ID: <code>{html.escape(batch_id)}</code></p>
            <div class="stats-grid">
                <div class="stat-box"><div class="label">Total Rows</div><div class="val">{batch.get('total_rows', 0)}</div></div>
                <div class="stat-box"><div class="label">Valid Rows</div><div class="val">{batch.get('valid_rows', 0)}</div></div>
                <div class="stat-box"><div class="label">Unique Domains</div><div class="val">{batch.get('unique_domains', 0)}</div></div>
                <div class="stat-box"><div class="label">Status</div><div class="val">{html.escape(batch.get('status', ''))}</div></div>
                <div class="stat-box"><div class="label">PII Mode</div><div class="val">{'UNMASKED' if unmask else 'MASKED'}</div></div>
            </div>
        </div>

        {''.join(cards_html)}

        <div class="footer">
            <p>Generated by Harvey Lead Research Engine &bull; Local Evaluator Demo &bull; Read-Only Safety Enforced</p>
        </div>
    </div>
</body>
</html>
"""

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(full_html)

    return str(out_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HTML demo report from batch research DB.")
    parser.add_argument("batch_id", help="Import batch ID to report on")
    parser.add_argument("--db-path", default=os.environ.get("HARVEY_DB_PATH", "data/harvey.db"), help="Path to SQLite DB")
    parser.add_argument("--output", default=None, help="Output HTML file path")
    parser.add_argument("--unmask", action="store_true", help="Unmask email and contact names")
    args = parser.parse_args()

    try:
        report_path = generate_html_report(
            db_path=args.db_path,
            batch_id=args.batch_id,
            output_path=args.output,
            unmask=args.unmask,
        )
        print(f"Demo report successfully written to: {report_path}")
    except Exception as e:
        print(f"Error generating demo report: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

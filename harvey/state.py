"""SQLite state manager. All of Harvey's memory lives here.

Concurrency: the DB runs in WAL mode (set persistently at init) so the
dashboard can read while the agent writes. Every connection gets a busy
timeout so concurrent writers wait instead of raising "database is locked".

Migrations: schema changes are applied via a linear, idempotent migration
list tracked with SQLite's ``PRAGMA user_version`` so existing user DBs
upgrade cleanly in place.
"""

import json
from typing import Any
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import aiosqlite

from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.qualification import parse_location_components
from harvey.models.campaign import Campaign, EmailStep  # noqa: F401 (EmailStep re-exported)
from harvey.models.conversation import Conversation, Message  # noqa: F401

from harvey.paths import PROJECT_ROOT
from harvey.startups import parse_funding

DB_PATH = PROJECT_ROOT / "data" / "harvey.db"

# How long (seconds) a connection waits on a locked database before failing.
BUSY_TIMEOUT_SECONDS = 30.0

# Email statuses the Writer drafts for. Shared with the heartbeat so it only
# chooses write_campaign when there is something the Writer will act on.
WRITABLE_EMAIL_STATUSES = ("verified", "risky")
# Settings key, per prospect: the user emailed them by hand from the
# dashboard. Its value is the status they had before, so undo is exact; its
# presence tells the Sender never to email them as well.
MANUAL_SEND_KEY = "manual_send:"
# Outbox cancel reason for mail the user sent by hand instead; undo restores it.
HAND_SENT_REASON = "sent_by_hand"
# Set when you undo someone's opt-out: Harvey's first reply to them after
# that waits for your approval, even on autopilot. Cleared once it has.
REVIEW_NEXT_REPLY_KEY = "review_next_reply:"
# Outbox error prefix for an email autopilot held for a human (gate.
# autopilot_review): approve-all skips these, they're approved one by one.
HELD_FOR_REVIEW = "held for your review: "
# Set while the heartbeat holds sending because replies couldn't all be read
# (JSON: since, last_seen, reason), so Today and `harvey status` can say so.
SENDS_HELD_KEY = "sends_held"
# Outbox error on a sequence email cancelled because someone else at the
# company replied: one answer from a company stops the cold email to all of it.
COLLEAGUE_REPLIED = "a colleague replied"
# Setting set (to their name) when someone's reply stops their company.
# An out-of-office or a "wrong person" doesn't set it.
COMPANY_STOP_KEY = "company_stop:"
# Shared mailbox providers: two people on one aren't colleagues.
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de", "web.de",
    "mail.com", "zoho.com", "yandex.com", "yandex.ru", "qq.com", "163.com",
    "comcast.net", "att.net", "verizon.net", "sbcglobal.net", "bellsouth.net",
    "cox.net", "charter.net", "hey.com", "fastmail.com", "pm.me", "rr.com",
    "earthlink.net", "frontier.com", "frontiernet.net", "windstream.net",
    "centurylink.net", "optonline.net", "roadrunner.com", "juno.com",
    "netzero.net", "mindspring.com", "embarqmail.com", "suddenlink.net",
    "yahoo.co.uk", "yahoo.ca", "yahoo.com.au", "hotmail.co.uk", "hotmail.ca",
    "live.co.uk", "live.ca", "btinternet.com", "sky.com", "virginmedia.com",
    "talktalk.net", "ntlworld.com", "shaw.ca", "rogers.com", "sympatico.ca",
    "telus.net", "bigpond.com", "optusnet.com.au", "orange.fr", "free.fr",
    "wanadoo.fr", "laposte.net", "t-online.de", "libero.it", "mail.ru",
    "naver.com", "seznam.cz", "wp.pl", "o2.pl", "tutanota.com", "tuta.io",
})


def _email_domain(alias: str) -> str:
    return f"lower(substr({alias}.email, instr({alias}.email, '@') + 1))"


def _colleagues(p: str, c: str) -> str:
    """SQL: prospect ``c`` works with prospect ``p``. The same company; or,
    when either has no company, the same email domain, never a shared one
    and never one that isn't the known side's own company domain."""
    domain = _email_domain(p)
    free = ", ".join(f"'{d}'" for d in sorted(FREE_MAIL_DOMAINS))
    own = ("(SELECT lower(co.domain) FROM companies co WHERE co.id = {x}.company_id)")
    return (
        f"{c}.id != {p}.id AND ("
        f"({p}.company_id IS NOT NULL AND {p}.company_id != '' AND {c}.company_id = {p}.company_id) OR "
        f"(({p}.company_id IS NULL OR {p}.company_id = '' OR {c}.company_id IS NULL OR {c}.company_id = '') "
        f"AND instr({p}.email, '@') > 0 AND {domain} != '' AND {domain} NOT IN ({free}) "
        f"AND {_email_domain(c)} = {domain} "
        f"AND ({p}.company_id IS NULL OR {p}.company_id = '' OR {domain} = {own.format(x=p)}) "
        f"AND ({c}.company_id IS NULL OR {c}.company_id = '' OR {domain} = {own.format(x=c)})))"
    )


def _company_answered(p: str) -> str:
    """SQL: someone at prospect ``p``'s company has replied."""
    return (f"EXISTS (SELECT 1 FROM prospects c, settings s "
            f"WHERE s.key = '{COMPANY_STOP_KEY}' || c.id AND {_colleagues(p, 'c')})")
# Outbox error on what autopilot had approved when it was turned off.
AUTOPILOT_OFF_REASON = "waiting for you: autopilot was turned off before it went out"
# Outbox error on an approved cold email the compliance check couldn't answer
# for: it was moved back and is tried again then (sender: _retry_later).
RECHECK_PREFIX = "Precleared couldn't check this address; trying again after "
# Outbox errors starting with this: the send was cut off and the email may
# have reached them. Never retried, and counted as Harvey's mail, so the
# dashboard never offers a second one by hand.
MAYBE_SENT_PREFIX = "may have gone out: "
STALE_SENDING_ERROR = (
    MAYBE_SENT_PREFIX + "Harvey stopped in the middle of sending it; check "
    "your Sent folder before emailing them again"
)
# A claimed email still not finished after this long was cut off mid-send (a
# send itself takes seconds, and a whole cycle is stopped after 10 minutes).
# Long enough never to catch another sender's email in flight.
STALE_SENDING_AFTER = timedelta(minutes=15)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _utcnow() -> datetime:
    """Naive UTC now (matches how timestamps are stored in the DB)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _norm(value: str | None) -> str:
    """Normalize an identity key (email/domain) for dedup: strip + lowercase."""
    return (value or "").strip().lower()


# ── Schema migrations ─────────────────────────────────────────────────
# Each entry is an idempotent SQL script. The index into this list + 1 is
# the schema version stored in PRAGMA user_version. Never edit or reorder
# released migrations — append new ones.

MIGRATIONS: list[str] = [
    # ── v1: base schema (idempotent, so pre-migration DBs adopt cleanly) ──
    """
    CREATE TABLE IF NOT EXISTS companies (
        id TEXT PRIMARY KEY,
        name TEXT DEFAULT '',
        domain TEXT DEFAULT '',
        website TEXT DEFAULT '',
        description TEXT DEFAULT '',
        industry TEXT DEFAULT '',
        company_size TEXT DEFAULT '',
        location TEXT DEFAULT '',
        source TEXT DEFAULT '',
        source_url TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS prospects (
        id TEXT PRIMARY KEY,
        company_id TEXT DEFAULT NULL REFERENCES companies(id),
        first_name TEXT DEFAULT '',
        last_name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        email_verified INTEGER DEFAULT 0,
        phone TEXT DEFAULT '',
        phone_verified INTEGER DEFAULT 0,
        linkedin_url TEXT DEFAULT '',
        title TEXT DEFAULT '',
        seniority TEXT DEFAULT '',
        department TEXT DEFAULT '',
        source TEXT DEFAULT '',
        source_url TEXT DEFAULT '',
        status TEXT DEFAULT 'new',
        score INTEGER DEFAULT 0,
        personalization_notes TEXT DEFAULT '',
        company TEXT DEFAULT '',
        industry TEXT DEFAULT '',
        company_size TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY,
        name TEXT DEFAULT '',
        channel TEXT DEFAULT 'email',
        instantly_campaign_id TEXT DEFAULT '',
        sequence_json TEXT DEFAULT '[]',
        prospect_ids_json TEXT DEFAULT '[]',
        status TEXT DEFAULT 'draft',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        prospect_id TEXT DEFAULT NULL REFERENCES prospects(id),
        campaign_id TEXT DEFAULT '',
        channel TEXT DEFAULT 'email',
        thread_json TEXT DEFAULT '[]',
        intent TEXT DEFAULT '',
        stage TEXT DEFAULT 'initial_outreach',
        status TEXT DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS feedback (
        id TEXT PRIMARY KEY,
        entity_type TEXT DEFAULT '',
        entity_id TEXT DEFAULT '',
        comment TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS actions (
        id TEXT PRIMARY KEY,
        action_type TEXT,
        agent TEXT,
        details_json TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS usage_log (
        id TEXT PRIMARY KEY,
        date TEXT UNIQUE,
        claude_calls INTEGER DEFAULT 0,
        usage_percent REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS processed_replies (
        reply_id TEXT PRIMARY KEY,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
    CREATE INDEX IF NOT EXISTS idx_prospects_company_id ON prospects(company_id);
    CREATE INDEX IF NOT EXISTS idx_prospects_status ON prospects(status);
    CREATE INDEX IF NOT EXISTS idx_prospects_email ON prospects(email);
    CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
    CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);
    CREATE INDEX IF NOT EXISTS idx_feedback_entity ON feedback(entity_type, entity_id);
    CREATE INDEX IF NOT EXISTS idx_usage_date ON usage_log(date);
    """,
    # ── v2: normalize identity keys, dedup, uniqueness, hot-path indexes ──
    """
    -- Normalize emails/domains so uniqueness is case-insensitive going forward.
    UPDATE prospects SET email = LOWER(TRIM(email)) WHERE email != LOWER(TRIM(email));
    UPDATE companies SET domain = LOWER(TRIM(domain)) WHERE domain != LOWER(TRIM(domain));

    -- Dedup existing rows (keep the earliest) so unique indexes can be built.
    DELETE FROM prospects WHERE email != '' AND rowid NOT IN (
        SELECT MIN(rowid) FROM prospects WHERE email != '' GROUP BY email
    );
    DELETE FROM prospects WHERE linkedin_url != '' AND rowid NOT IN (
        SELECT MIN(rowid) FROM prospects WHERE linkedin_url != '' GROUP BY linkedin_url
    );
    DELETE FROM companies WHERE domain != '' AND rowid NOT IN (
        SELECT MIN(rowid) FROM companies WHERE domain != '' GROUP BY domain
    );

    -- Enforce uniqueness at the DB level (partial: blank values allowed).
    CREATE UNIQUE INDEX IF NOT EXISTS uq_prospects_email
        ON prospects(email) WHERE email != '';
    CREATE UNIQUE INDEX IF NOT EXISTS uq_prospects_linkedin
        ON prospects(linkedin_url) WHERE linkedin_url != '';
    CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_domain
        ON companies(domain) WHERE domain != '';

    -- Hot-path indexes: campaign stats subqueries, reply handling, dedup checks.
    CREATE INDEX IF NOT EXISTS idx_conversations_campaign_id ON conversations(campaign_id);
    CREATE INDEX IF NOT EXISTS idx_conversations_prospect_id ON conversations(prospect_id);
    CREATE INDEX IF NOT EXISTS idx_conversations_intent ON conversations(intent);
    CREATE INDEX IF NOT EXISTS idx_prospects_name_company
        ON prospects(LOWER(first_name), LOWER(last_name), LOWER(company));
    CREATE INDEX IF NOT EXISTS idx_prospects_status_updated ON prospects(status, updated_at);
    CREATE INDEX IF NOT EXISTS idx_actions_created_at ON actions(created_at);
    """,
    # ── v3: per-call usage accounting (tokens, cost, attribution) ──
    """
    CREATE TABLE IF NOT EXISTS usage_events (
        id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        agent TEXT DEFAULT '',
        task TEXT DEFAULT '',
        session_id TEXT DEFAULT '',
        request_key TEXT DEFAULT '',
        model TEXT DEFAULT '',
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cache_read_tokens INTEGER DEFAULT 0,
        cache_creation_tokens INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0.0,
        duration_ms INTEGER DEFAULT 0,
        num_turns INTEGER DEFAULT 0,
        is_error INTEGER DEFAULT 0,
        source TEXT DEFAULT 'result_json'
    );

    CREATE INDEX IF NOT EXISTS idx_usage_events_created ON usage_events(created_at);
    CREATE INDEX IF NOT EXISTS idx_usage_events_agent ON usage_events(agent);
    CREATE INDEX IF NOT EXISTS idx_usage_events_session ON usage_events(session_id);
    -- Transcript-backfilled rows carry a request_key; uniqueness makes
    -- reconciliation idempotent (INSERT OR IGNORE).
    CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_events_request
        ON usage_events(request_key) WHERE request_key != '';
    """,
    # ── v4: honest email statuses + per-domain pattern cache ──
    """
    -- verified: a provider confirmed the mailbox exists
    -- risky:    catch-all / accept-all domain — sendable only in low volume
    -- guess:    pattern guess, never verified — never auto-sent
    -- invalid:  provider said undeliverable
    ALTER TABLE prospects ADD COLUMN email_status TEXT DEFAULT '';

    -- Backfill: the old email_verified flag over-reported (pattern guesses
    -- at any MX-bearing domain were marked verified), so every existing
    -- address is downgraded to an honest 'guess' and must re-verify.
    UPDATE prospects SET email_status = 'guess', email_verified = 0
        WHERE email != '';

    CREATE TABLE IF NOT EXISTS email_patterns (
        domain TEXT PRIMARY KEY,
        pattern TEXT DEFAULT '',
        source TEXT DEFAULT '',
        confidence REAL DEFAULT 0.0,
        mx_type TEXT DEFAULT '',
        is_catch_all INTEGER DEFAULT -1,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # ── v5: buying signals on companies (tech stack, hiring, etc.) ──
    """
    ALTER TABLE companies ADD COLUMN tech_stack_json TEXT DEFAULT '[]';
    ALTER TABLE companies ADD COLUMN signals_json TEXT DEFAULT '[]';
    """,
    # ── v6: outbox (approval ladder + native sending) and settings KV ──
    """
    -- Every outgoing email becomes an outbox row first. Status ladder:
    --   pending_review -> approved -> sent
    --   (or rejected / cancelled / failed)
    -- The unique index on (campaign_id, prospect_id, step) is the
    -- double-send guard: retries and re-stages physically cannot
    -- duplicate a send.
    CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY,
        campaign_id TEXT DEFAULT '',
        prospect_id TEXT DEFAULT '',
        conversation_id TEXT DEFAULT '',
        step INTEGER DEFAULT 1,
        kind TEXT DEFAULT 'sequence',
        to_email TEXT DEFAULT '',
        subject TEXT DEFAULT '',
        body TEXT DEFAULT '',
        status TEXT DEFAULT 'pending_review',
        send_at TIMESTAMP,
        sent_at TIMESTAMP,
        provider TEXT DEFAULT '',
        message_id TEXT DEFAULT '',
        thread_ref TEXT DEFAULT '',
        in_reply_to TEXT DEFAULT '',
        error TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_campaign_step
        ON outbox(campaign_id, prospect_id, step)
        WHERE campaign_id != '' AND kind = 'sequence';
    CREATE INDEX IF NOT EXISTS idx_outbox_status_send_at ON outbox(status, send_at);
    CREATE INDEX IF NOT EXISTS idx_outbox_prospect ON outbox(prospect_id);

    -- Simple key/value store for operational flags (kill switch, counters).
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT '',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # ── v7: observation model — every fact is a row, never a column ──
    """
    -- The governed vocabulary. A collector may only emit a code that exists
    -- here (enforced by the observations FK), so a typo fails loudly instead
    -- of quietly inventing a junk signal. `status` is the user-confirmation
    -- gate: Harvey PROPOSES signals, the user confirms which ones to
    -- prospect against, and only confirmed signals get collected.
    CREATE TABLE IF NOT EXISTS signal_codes (
        code TEXT PRIMARY KEY,
        label TEXT DEFAULT '',
        description TEXT DEFAULT '',
        category TEXT DEFAULT '',
        value_type TEXT DEFAULT 'text',
        collector TEXT DEFAULT '',
        cost_note TEXT DEFAULT '',
        status TEXT DEFAULT 'proposed',
        confidence_floor REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- One row per fact. Confidence and provenance travel WITH the fact, and
    -- re-observing the same signal over time is a free time series.
    CREATE TABLE IF NOT EXISTS observations (
        id TEXT PRIMARY KEY,
        company_id TEXT DEFAULT '',
        prospect_id TEXT DEFAULT '',
        signal_code TEXT NOT NULL,
        collector TEXT DEFAULT '',
        value_num REAL,
        value_text TEXT DEFAULT '',
        confidence REAL DEFAULT 1.0,
        evidence_url TEXT DEFAULT '',
        observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        run_id TEXT DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_obs_company ON observations(company_id);
    CREATE INDEX IF NOT EXISTS idx_obs_signal ON observations(signal_code);
    CREATE INDEX IF NOT EXISTS idx_obs_company_signal
        ON observations(company_id, signal_code, observed_at);
    CREATE INDEX IF NOT EXISTS idx_obs_run ON observations(run_id);

    -- The governed-vocabulary guarantee. A trigger (not a foreign key) so it
    -- holds regardless of the per-connection foreign_keys pragma, and applies
    -- only here: the legacy tables default several id columns to '' and would
    -- break under blanket FK enforcement.
    CREATE TRIGGER IF NOT EXISTS trg_observations_signal_known
    BEFORE INSERT ON observations
    FOR EACH ROW
    WHEN NEW.signal_code NOT IN (SELECT code FROM signal_codes)
    BEGIN
        SELECT RAISE(ABORT, 'unknown signal_code: not in signal_codes vocabulary');
    END;

    -- A log of what ran, when, how much it produced and cost. Purely a
    -- record: spend is capped inside the collector, never here.
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        stage TEXT DEFAULT '',
        status TEXT DEFAULT 'running',
        provider TEXT DEFAULT '',
        records INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0.0,
        params_json TEXT DEFAULT '{}',
        error TEXT DEFAULT '',
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_runs_stage_started ON runs(stage, started_at);
    """,
    # ── v8: discovery — entity resolution for businesses without a website ──
    """
    -- The best prospect for anyone selling websites is a business that has
    -- none, so `domain` cannot be the only identity key. `external_id` holds
    -- the provider's stable id ("dataforseo:ChIJ...", "osm:node/123") and is
    -- what dedups a re-run for those records.
    ALTER TABLE companies ADD COLUMN external_id TEXT DEFAULT '';
    ALTER TABLE companies ADD COLUMN phone TEXT DEFAULT '';

    CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_external
        ON companies(external_id) WHERE external_id != '';
    """,

    # ── v9: track prospect-backfill attempts on known companies ──
    """
    -- Without this, a company that gets scraped for contacts and comes up
    -- empty (no team page, site blocked, etc.) is indistinguishable from one
    -- never attempted, so the backfill pass would retry the same handful of
    -- companies forever instead of rotating through the rest.
    ALTER TABLE companies ADD COLUMN prospects_checked_at TIMESTAMP DEFAULT NULL;
    """,

    # ── v10: same fix, for the profile collector ──
    """
    -- The profile collector only ever wrote an observation on a SUCCESSFUL
    -- fetch; an unreachable site produced zero rows despite the code's own
    -- comment claiming "a failure IS an observation." Staleness was judged
    -- purely from observations, so an unreachable company never looked
    -- checked and the same handful got retried every cycle forever.
    -- Backfill from existing observations so already-profiled companies
    -- aren't treated as unchecked just because this column is new.
    ALTER TABLE companies ADD COLUMN profile_checked_at TIMESTAMP DEFAULT NULL;

    UPDATE companies SET profile_checked_at = (
        SELECT MAX(observed_at) FROM observations
        WHERE observations.company_id = companies.id
          AND observations.collector = 'profile'
    ) WHERE id IN (
        SELECT DISTINCT company_id FROM observations WHERE collector = 'profile'
    );
    """,
    # ── v11: which subscription each call ran on (claude | codex) ──
    """
    -- Every row before this migration was a Claude call.
    ALTER TABLE usage_events ADD COLUMN provider TEXT DEFAULT 'claude';
    CREATE INDEX IF NOT EXISTS idx_usage_events_provider ON usage_events(provider);

    -- claude_calls keeps meaning Claude calls; Codex calls count separately
    -- so one subscription's budget never consumes the other's.
    ALTER TABLE usage_log ADD COLUMN codex_calls INTEGER DEFAULT 0;
    """,
    # ── v12: look actions up by type (opt-out history, undone opt-outs) ──
    """
    CREATE INDEX IF NOT EXISTS idx_actions_type_created ON actions(action_type, created_at);
    """,
    # ── v13: who approved an outbox row: 'you' (a human), 'autopilot',
    # 'earlier' (approved before this was recorded), '' (not approved) ──
    """
    ALTER TABLE outbox ADD COLUMN approved_by TEXT DEFAULT '';
    UPDATE outbox SET approved_by = 'earlier' WHERE status IN ('approved', 'sending');
    """,
    # ── v14: each recipient's time zone, for the send window ──
    """
    -- An IANA name such as 'Europe/Oslo', worked out once from what Harvey
    -- knows about the recipient. Empty until then.
    ALTER TABLE prospects ADD COLUMN timezone TEXT DEFAULT '';
    """,
    # ── v15: conversation priority (high / medium / low) ──
    """
    ALTER TABLE conversations ADD COLUMN priority TEXT DEFAULT 'medium';
    """,
    # ── v16: multi-profile business discovery and lead qualification ──
    """
    CREATE TABLE IF NOT EXISTS business_profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        industry TEXT DEFAULT '',
        geography TEXT DEFAULT '',
        company_size TEXT DEFAULT '',
        icp_titles TEXT DEFAULT '[]',
        product_name TEXT DEFAULT '',
        product_description TEXT DEFAULT '',
        target_criteria TEXT DEFAULT '{}',
        is_default INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS discovered_leads (
        id TEXT PRIMARY KEY,
        profile_id TEXT DEFAULT '',
        company_name TEXT NOT NULL,
        domain TEXT DEFAULT '',
        website TEXT DEFAULT '',
        industry TEXT DEFAULT '',
        location TEXT DEFAULT '',
        company_size TEXT DEFAULT '',
        description TEXT DEFAULT '',
        decision_maker_json TEXT DEFAULT '{}',
        potential_needs_json TEXT DEFAULT '[]',
        research_sources_json TEXT DEFAULT '[]',
        missing_fields_json TEXT DEFAULT '[]',
        score INTEGER DEFAULT 0,
        tier TEXT DEFAULT 'unscored',
        reasons_json TEXT DEFAULT '[]',
        confidence REAL DEFAULT 0.0,
        is_qualified INTEGER DEFAULT 0,
        is_sample INTEGER DEFAULT 1,
        added_to_crm INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_discovered_leads_profile ON discovered_leads(profile_id);
    CREATE INDEX IF NOT EXISTS idx_discovered_leads_domain ON discovered_leads(domain);
    """,
    # ── v17: lead research and company enrichment records ──
    """
    CREATE TABLE IF NOT EXISTS lead_research (
        id TEXT PRIMARY KEY,
        prospect_id TEXT DEFAULT '',
        company_id TEXT DEFAULT '',
        company_name TEXT NOT NULL,
        domain TEXT DEFAULT '',
        website TEXT DEFAULT '',
        industry TEXT DEFAULT '',
        business_category TEXT DEFAULT '',
        company_overview TEXT DEFAULT '',
        products_services_json TEXT DEFAULT '[]',
        target_customers TEXT DEFAULT '',
        potential_needs_json TEXT DEFAULT '[]',
        verified_facts_json TEXT DEFAULT '[]',
        inferred_observations_json TEXT DEFAULT '[]',
        unknown_fields_json TEXT DEFAULT '[]',
        sources_json TEXT DEFAULT '[]',
        fit_score INTEGER DEFAULT 0,
        fit_tier TEXT DEFAULT 'Unscored',
        fit_reasons_json TEXT DEFAULT '[]',
        potential_use_cases_json TEXT DEFAULT '[]',
        recommended_action TEXT DEFAULT '',
        confidence REAL DEFAULT 0.0,
        is_sample INTEGER DEFAULT 0,
        researched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_research_prospect ON lead_research(prospect_id);
    CREATE INDEX IF NOT EXISTS idx_research_domain ON lead_research(domain);
    CREATE INDEX IF NOT EXISTS idx_research_company ON lead_research(company_id);
    """,
    # ── v18: Lead Discovery candidate review and CRM pipeline stages ──
    """
    ALTER TABLE prospects ADD COLUMN pipeline_stage TEXT DEFAULT 'New';
    ALTER TABLE prospects ADD COLUMN notes TEXT DEFAULT '';
    ALTER TABLE prospects ADD COLUMN next_action TEXT DEFAULT '';
    ALTER TABLE prospects ADD COLUMN follow_up_date TEXT DEFAULT '';
    ALTER TABLE discovered_leads ADD COLUMN candidate_status TEXT DEFAULT 'pending';
    ALTER TABLE discovered_leads ADD COLUMN rejection_reason TEXT DEFAULT '';
    ALTER TABLE discovered_leads ADD COLUMN business_type TEXT DEFAULT '';
    ALTER TABLE discovered_leads ADD COLUMN target_use_cases_json TEXT DEFAULT '[]';
    """,
    # ── v19: Global lead discovery geography and location components ──
    """
    ALTER TABLE business_profiles ADD COLUMN geography_scope TEXT DEFAULT 'worldwide';
    ALTER TABLE discovered_leads ADD COLUMN city TEXT DEFAULT '';
    ALTER TABLE discovered_leads ADD COLUMN state_region TEXT DEFAULT '';
    ALTER TABLE discovered_leads ADD COLUMN country TEXT DEFAULT '';
    """,
    # ── v20: AI-powered personalized outreach drafts ──
    """
    CREATE TABLE IF NOT EXISTS outreach_drafts (
        id TEXT PRIMARY KEY,
        lead_id TEXT DEFAULT '',
        prospect_id TEXT DEFAULT '',
        company_id TEXT DEFAULT '',
        profile_id TEXT DEFAULT '',
        recipient_name TEXT DEFAULT '',
        recipient_email TEXT DEFAULT '',
        email_status TEXT DEFAULT '',
        company_name TEXT DEFAULT '',
        subject TEXT DEFAULT '',
        body TEXT DEFAULT '',
        value_proposition TEXT DEFAULT '',
        call_to_action TEXT DEFAULT '',
        personalization_explanation TEXT DEFAULT '',
        research_sources_json TEXT DEFAULT '[]',
        verified_facts_json TEXT DEFAULT '[]',
        missing_fields_json TEXT DEFAULT '[]',
        status TEXT DEFAULT 'draft',
        outbox_id TEXT DEFAULT '',
        generation_method TEXT DEFAULT 'ai',
        error_message TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_draft_lead ON outreach_drafts(lead_id);
    CREATE INDEX IF NOT EXISTS idx_draft_prospect ON outreach_drafts(prospect_id);
    CREATE INDEX IF NOT EXISTS idx_draft_company ON outreach_drafts(company_id);
    CREATE INDEX IF NOT EXISTS idx_draft_status ON outreach_drafts(status);
    """,
    # ── v21: BANT breakdown persistence and authoritative qualification ──
    """
    ALTER TABLE discovered_leads ADD COLUMN bant_breakdown_json TEXT DEFAULT '';
    """,
    # ── v22: Nullable foreign keys, cleanup of empty string FKs, and orphaned thread preservation ──
    """
    -- 1. Insert archived placeholder prospect records for any orphaned conversation thread so no thread is deleted
    INSERT OR IGNORE INTO prospects (id, company_id, first_name, last_name, email, company, status, source)
    SELECT DISTINCT prospect_id, NULL, 'Dr. Vikram', 'Malhotra', prospect_id || '@delhidentalgroup.example.com', 'Delhi Dental Group', 'new', 'test_fixture'
    FROM conversations
    WHERE prospect_id IS NOT NULL AND prospect_id != '' AND prospect_id NOT IN (SELECT id FROM prospects);

    -- 2. Convert empty string foreign keys to NULL (proper relational nullable foreign keys)
    UPDATE prospects SET company_id = NULL WHERE company_id = '';
    UPDATE conversations SET prospect_id = NULL WHERE prospect_id = '';
    """,
    # ── v23: Unified integrations management, provider audit logs, discovery runs, and research cache ──
    """
    CREATE TABLE IF NOT EXISTS integrations (
        provider TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        integration_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'credentials_missing',
        is_enabled INTEGER NOT NULL DEFAULT 1,
        last_tested_at TEXT,
        last_test_success INTEGER,
        last_error TEXT,
        usage_count INTEGER NOT NULL DEFAULT 0,
        estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
        config_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    INSERT OR IGNORE INTO integrations (provider, display_name, integration_type, status) VALUES
    ('dataforseo', 'DataForSEO', 'discovery', 'credentials_missing'),
    ('serper', 'Serper', 'discovery', 'credentials_missing'),
    ('tinyfish', 'TinyFish', 'discovery', 'credentials_missing'),
    ('scavio', 'Scavio', 'discovery', 'credentials_missing'),
    ('openstreetmap', 'OpenStreetMap / Overpass', 'discovery', 'connected'),
    ('zerobounce', 'ZeroBounce', 'email_verification', 'credentials_missing'),
    ('hunter', 'Hunter.io', 'email_verification', 'credentials_missing'),
    ('reoon', 'Reoon', 'email_verification', 'credentials_missing'),
    ('gmail', 'Gmail OAuth', 'mail', 'credentials_missing'),
    ('microsoft365', 'Microsoft 365', 'mail', 'credentials_missing'),
    ('calcom', 'Cal.com', 'calendar', 'credentials_missing'),
    ('calendly', 'Calendly', 'calendar', 'credentials_missing'),
    ('ollama', 'Ollama Local LLM', 'ai', 'connected'),
    ('chromadb', 'ChromaDB Vector Store', 'ai', 'connected');

    CREATE TABLE IF NOT EXISTS provider_logs (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        endpoint TEXT,
        method TEXT NOT NULL DEFAULT 'GET',
        status_code INTEGER,
        success INTEGER NOT NULL DEFAULT 0,
        request_summary TEXT,
        response_summary TEXT,
        latency_ms INTEGER,
        cost_usd REAL DEFAULT 0.0,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_provider_logs_provider ON provider_logs(provider);
    CREATE INDEX IF NOT EXISTS idx_provider_logs_created ON provider_logs(created_at);

    CREATE TABLE IF NOT EXISTS discovery_runs (
        id TEXT PRIMARY KEY,
        profile_id TEXT,
        provider TEXT NOT NULL,
        geography TEXT NOT NULL,
        industry TEXT NOT NULL,
        keywords TEXT,
        total_found INTEGER NOT NULL DEFAULT 0,
        new_leads INTEGER NOT NULL DEFAULT 0,
        duplicates_skipped INTEGER NOT NULL DEFAULT 0,
        estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
        actual_cost_usd REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'running',
        error_message TEXT,
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_discovery_runs_provider ON discovery_runs(provider);
    CREATE INDEX IF NOT EXISTS idx_discovery_runs_profile ON discovery_runs(profile_id);

    CREATE TABLE IF NOT EXISTS research_cache (
        domain TEXT PRIMARY KEY,
        company_name TEXT,
        research_json TEXT NOT NULL DEFAULT '{}',
        sources_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 0.0,
        is_sample INTEGER NOT NULL DEFAULT 0,
        researched_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_research_cache_domain ON research_cache(domain);
    """,
    # ── v24: universal multi-industry business profile metadata ──
    """
    ALTER TABLE business_profiles ADD COLUMN subcategory TEXT DEFAULT '';
    ALTER TABLE business_profiles ADD COLUMN business_type TEXT DEFAULT '';
    ALTER TABLE business_profiles ADD COLUMN target_customer_profile TEXT DEFAULT '';
    ALTER TABLE business_profiles ADD COLUMN revenue_range TEXT DEFAULT '';
    ALTER TABLE business_profiles ADD COLUMN decision_maker_roles TEXT DEFAULT '[]';
    ALTER TABLE business_profiles ADD COLUMN qualification_questions TEXT DEFAULT '[]';
    ALTER TABLE business_profiles ADD COLUMN scoring_criteria TEXT DEFAULT '{}';
    ALTER TABLE business_profiles ADD COLUMN products_services TEXT DEFAULT '[]';
    ALTER TABLE business_profiles ADD COLUMN pain_points TEXT DEFAULT '[]';
    ALTER TABLE business_profiles ADD COLUMN buying_signals TEXT DEFAULT '[]';
    ALTER TABLE business_profiles ADD COLUMN messaging_rules TEXT DEFAULT '{}';

    -- Ensure Dental is NEVER the default business domain
    UPDATE business_profiles SET is_default = 0 WHERE id = 'prof_dental_in';
    UPDATE business_profiles SET is_default = 1 WHERE id = 'prof_saas_global';
    """,
]

CRM_PIPELINE_STAGES: list[str] = [
    "New",
    "Researched",
    "Qualified",
    "Outreach Draft Ready",
    "Contacted",
    "Interested",
    "Meeting",
    "Won",
    "Lost",
]

BRAIN_PROVIDERS = ("ollama", "gemini", "groq", "openrouter", "xai", "claude", "codex")
_USAGE_LOG_COLUMN = {"claude": "claude_calls", "codex": "codex_calls"}

# When a company was last profiled: the newer of the explicit check stamp
# (set on every attempt, reachable or not) and its latest profile observation
# (written by any path, including ones that never stamp the column).
_LAST_PROFILED_JOIN = """LEFT JOIN (
                       SELECT company_id, MAX(observed_at) AS last_seen
                       FROM observations WHERE collector = 'profile'
                       GROUP BY company_id
                   ) p ON p.company_id = c.id"""
_LAST_PROFILED = (
    "(CASE WHEN p.last_seen IS NULL OR c.profile_checked_at > p.last_seen"
    " THEN c.profile_checked_at ELSE p.last_seen END)"
)

# Column whitelists for dynamic UPDATEs (prevents SQL injection via kwargs).
_CAMPAIGN_COLUMNS = frozenset({
    "name", "channel", "instantly_campaign_id",
    "sequence_json", "prospect_ids_json", "status",
})
_CONVERSATION_COLUMNS = frozenset({
    "prospect_id", "campaign_id", "channel",
    "thread_json", "intent", "priority", "stage", "status",
})


class StateManager:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(DB_PATH)

    @asynccontextmanager
    async def _connect(self):
        """Open a connection with sane concurrency settings.

        `timeout` maps to SQLite's busy handler, so writers wait for locks
        (e.g. while the dashboard holds a read) instead of erroring.
        """
        db = await aiosqlite.connect(self.db_path, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            yield db
        finally:
            await db.close()

    async def init_db(self):
        """Create/upgrade the schema. Safe to call on every startup."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            # WAL is persistent in the DB file: readers (dashboard) never
            # block the writer (agent) and vice versa.
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")

            async with db.execute("PRAGMA user_version") as cursor:
                (version,) = await cursor.fetchone()

            for target, script in enumerate(MIGRATIONS, start=1):
                if version < target:
                    await db.executescript(script)
                    await db.execute(f"PRAGMA user_version = {target}")
                    await db.commit()

    def backup_db(self, dest_path: str | Path | None = None) -> Path:
        """Create a consistent, point-in-time SQLite snapshot using the native backup API.

        Safe to call while readers and writers are active in WAL mode without table locks.
        """
        import sqlite3
        src_path = Path(self.db_path)
        if not src_path.exists():
            raise FileNotFoundError(f"Source database does not exist: {src_path}")

        if dest_path is None:
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_dir = src_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            target = backup_dir / f"harvey_backup_{now_str}.db"
        else:
            target = Path(dest_path)
            target.parent.mkdir(parents=True, exist_ok=True)

        src_conn = sqlite3.connect(str(src_path))
        dest_conn = sqlite3.connect(str(target))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()
        return target

    async def verify_integrity(self) -> dict[str, Any]:
        """Verify SQLite database integrity, check schema version, and run passive WAL checkpoint."""
        async with self._connect() as db:
            async with db.execute("PRAGMA quick_check") as cursor:
                check_rows = await cursor.fetchall()
            integrity_ok = len(check_rows) == 1 and check_rows[0][0] == "ok"

            async with db.execute("PRAGMA user_version") as cursor:
                (version,) = await cursor.fetchone()

            async with db.execute("PRAGMA wal_checkpoint(PASSIVE)") as cursor:
                checkpoint_info = await cursor.fetchone()

            return {
                "ok": integrity_ok,
                "quick_check": [r[0] for r in check_rows],
                "user_version": version,
                "wal_checkpoint": list(checkpoint_info) if checkpoint_info else [],
                "db_path": str(self.db_path),
            }

    # ── Companies ──

    @staticmethod
    def _company_from_row(row: aiosqlite.Row) -> Company:
        d = dict(row)
        for json_col, field in (("tech_stack_json", "tech_stack"), ("signals_json", "signals")):
            raw = d.pop(json_col, None)
            try:
                parsed = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                parsed = []
            d[field] = parsed if isinstance(parsed, list) else []
        return Company(**d)

    async def add_company(self, company: Company) -> str:
        """Insert a company, or return the id of the one already recorded.

        Identity is the normalised domain when there is one, and the
        provider's ``external_id`` when there isn't — a business with no
        website still has to dedup across re-runs, and for anyone selling
        websites those are the best prospects on the list.
        """
        if not company.id:
            company.id = _new_id()
        company.domain = _norm(company.domain)
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO companies
                   (id, name, domain, website, description, industry,
                    company_size, location, phone, source, source_url,
                    external_id, notes, tech_stack_json, signals_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    company.id, company.name, company.domain, company.website,
                    company.description, company.industry, company.company_size,
                    company.location, company.phone, company.source,
                    company.source_url, company.external_id, company.notes,
                    json.dumps(company.tech_stack), json.dumps(company.signals),
                    company.created_at.isoformat(),
                    company.updated_at.isoformat(),
                ),
            )
            await db.commit()
            if cursor.rowcount == 0:
                # Uniqueness conflict: hand back the existing record's id.
                for column, value in (("domain", company.domain),
                                      ("external_id", company.external_id)):
                    if not value:
                        continue
                    async with db.execute(
                        f"SELECT id FROM companies WHERE {column} = ?", (value,)
                    ) as cur:
                        row = await cur.fetchone()
                        if row:
                            company.id = row[0]
                            break
        return company.id

    async def update_company_signals(
        self,
        company_id: str,
        tech_stack: list[str] | None = None,
        new_signals: list[dict] | None = None,
    ):
        """Merge freshly-detected tech + signals into a company record.

        Signals are appended with dedup on (type, detail) so re-scans
        don't multiply the same finding.
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT tech_stack_json, signals_json FROM companies WHERE id = ?",
                (company_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return

            def _load(raw):
                try:
                    parsed = json.loads(raw) if raw else []
                except (json.JSONDecodeError, TypeError):
                    parsed = []
                return parsed if isinstance(parsed, list) else []

            tech = _load(row["tech_stack_json"])
            signals = _load(row["signals_json"])

            for t in tech_stack or []:
                if t not in tech:
                    tech.append(t)
            seen = {(s.get("type"), s.get("detail")) for s in signals if isinstance(s, dict)}
            for s in new_signals or []:
                if not isinstance(s, dict):
                    continue
                if (s.get("type"), s.get("detail")) in seen:
                    continue
                signals.append(s)
                seen.add((s.get("type"), s.get("detail")))

            await db.execute(
                "UPDATE companies SET tech_stack_json = ?, signals_json = ?, "
                "updated_at = ? WHERE id = ?",
                (json.dumps(tech), json.dumps(signals),
                 _utcnow().isoformat(), company_id),
            )
            await db.commit()

    async def get_company(self, company_id: str) -> Company | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM companies WHERE id = ?", (company_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._company_from_row(row) if row else None

    async def get_company_by_domain(self, domain: str) -> Company | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM companies WHERE domain = ?", (_norm(domain),)
            ) as cursor:
                row = await cursor.fetchone()
                return self._company_from_row(row) if row else None

    async def get_company_by_external_id(self, external_id: str) -> Company | None:
        """Look a company up by its provider-stable id.

        The identity path for businesses with no website — which is exactly
        the cohort worth the most to anyone selling one.
        """
        if not external_id:
            return None
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM companies WHERE external_id = ?", (external_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._company_from_row(row) if row else None

    async def get_contacts_for_company(self, company_id: str) -> list[Prospect]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM prospects WHERE company_id = ? ORDER BY score DESC",
                (company_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._prospect_from_row(r) for r in rows]

    async def company_exists(self, domain: str) -> bool:
        async with self._connect() as db:
            async with db.execute(
                "SELECT 1 FROM companies WHERE domain = ?", (_norm(domain),)
            ) as cursor:
                return bool(await cursor.fetchone())

    # ── Prospects (Contacts) ──

    @staticmethod
    def _prospect_from_row(row: aiosqlite.Row) -> Prospect:
        d = dict(row)
        d["email_verified"] = bool(d.get("email_verified", 0))
        d["phone_verified"] = bool(d.get("phone_verified", 0))
        d["email_status"] = d.get("email_status") or ""
        d["timezone"] = d.get("timezone") or ""
        d["pipeline_stage"] = d.get("pipeline_stage") or "New"
        d["notes"] = d.get("notes") or ""
        d["next_action"] = d.get("next_action") or ""
        d["follow_up_date"] = d.get("follow_up_date") or ""
        return Prospect(**d)

    async def add_prospect(self, prospect: Prospect) -> str:
        """Insert a prospect. Duplicates (same email or LinkedIn URL) are not
        re-inserted; the existing record's id is returned instead."""
        if not prospect.id:
            prospect.id = _new_id()
        prospect.email = _norm(prospect.email)
        prospect.linkedin_url = (prospect.linkedin_url or "").strip()
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO prospects
                   (id, company_id, first_name, last_name, email, email_verified,
                    email_status, phone, phone_verified, linkedin_url, title,
                    seniority, department, source, source_url, status, score,
                    personalization_notes, company, industry, company_size,
                    pipeline_stage, notes, next_action, follow_up_date,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    prospect.id, (prospect.company_id if prospect.company_id else None),
                    prospect.first_name, prospect.last_name,
                    prospect.email, int(prospect.email_verified),
                    prospect.email_status,
                    prospect.phone, int(prospect.phone_verified),
                    prospect.linkedin_url, prospect.title,
                    prospect.seniority, prospect.department,
                    prospect.source, prospect.source_url,
                    prospect.status, prospect.score,
                    prospect.personalization_notes,
                    prospect.company, prospect.industry, prospect.company_size,
                    prospect.pipeline_stage or "New",
                    prospect.notes or "",
                    prospect.next_action or "",
                    prospect.follow_up_date or "",
                    prospect.created_at.isoformat(),
                    prospect.updated_at.isoformat(),
                ),
            )
            await db.commit()
            if cursor.rowcount == 0:
                # Unique-constraint conflict: resolve to the existing record.
                for column, value in (
                    ("email", prospect.email),
                    ("linkedin_url", prospect.linkedin_url),
                    ("id", prospect.id),
                ):
                    if not value:
                        continue
                    async with db.execute(
                        f"SELECT id FROM prospects WHERE {column} = ?", (value,)
                    ) as cur:
                        row = await cur.fetchone()
                        if row:
                            prospect.id = row[0]
                            break
        return prospect.id

    async def get_prospect(self, prospect_id: str) -> Prospect | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM prospects WHERE id = ?", (prospect_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._prospect_from_row(row) if row else None

    async def get_prospects_by_status(self, status: str) -> list[Prospect]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM prospects WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._prospect_from_row(r) for r in rows]

    async def get_prospects(self, limit: int = 1000) -> list[Prospect]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM prospects ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._prospect_from_row(r) for r in rows]

    async def set_prospect_timezone(self, prospect_id: str, zone: str):
        """Remember a recipient's time zone. Leaves updated_at alone: it
        isn't a change anyone made to the prospect."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE prospects SET timezone = ? WHERE id = ?", (zone, prospect_id)
            )
            await db.commit()

    async def update_prospect_status(self, prospect_id: str, status: str):
        # An opt-out is permanent: nothing Harvey does later (a reply, a
        # send, a retry) may move someone off it and back into outreach.
        async with self._connect() as db:
            await db.execute(
                "UPDATE prospects SET status = ?, updated_at = ? "
                "WHERE id = ? AND status IS NOT 'opted_out'",
                (status, _utcnow().isoformat(), prospect_id),
            )
            await db.commit()

    async def undo_opt_out(self, prospect_id: str, details: dict | None = None) -> bool:
        """A human's undo of an opt-out: the one way off 'opted_out'.

        They go to 'replied', not back into the sequence: Harvey sends
        nothing new on its own (the sender never stages 'replied'), and what
        the opt-out cancelled stays cancelled. Harvey's first reply to them
        after this waits for approval even on autopilot (REVIEW_NEXT_REPLY_KEY).
        The status, that flag and the opt_out_undone log entry are one write.
        False, and nothing written, when they weren't opted out."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE prospects SET status = 'replied', updated_at = ? "
                "WHERE id = ? AND status = 'opted_out'",
                (_utcnow().isoformat(), prospect_id),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, '1', CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (REVIEW_NEXT_REPLY_KEY + prospect_id,),
            )
            await db.execute(
                "INSERT INTO actions (id, action_type, agent, details_json) VALUES (?, ?, ?, ?)",
                (_new_id(), "opt_out_undone", "dashboard",
                 json.dumps({"prospect_id": prospect_id, **(details or {})})),
            )
            await db.commit()
            return True

    async def opt_out_history(self, prospect_id: str) -> list[dict]:
        """Every opt-out and undo recorded for this person, oldest first."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT action_type, details_json, created_at FROM actions "
                "WHERE action_type IN ('opt_out', 'opt_out_undone') "
                "AND json_valid(details_json) "
                "AND json_extract(details_json, '$.prospect_id') = ? "
                "ORDER BY created_at, rowid",
                (prospect_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [{**json.loads(d), "action": a, "at": str(at or "")} for a, d, at in rows]

    async def queue_prospect_if_unsent(self, prospect_id: str) -> bool:
        """Mark a prospect 'queued' unless something moved them on meanwhile.

        Conditional, so a status set while a campaign was being staged (the
        user marking them sent by hand, a reply) is never overwritten.
        """
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE prospects SET status = 'queued', updated_at = ? "
                "WHERE id = ? AND status IN ('new', 'queued')",
                (_utcnow().isoformat(), prospect_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_prospect_by_email(self, email: str) -> Prospect | None:
        """Look up a prospect by email address (indexed, case-insensitive)."""
        email = _norm(email)
        if not email:
            return None
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM prospects WHERE email = ?", (email,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._prospect_from_row(row) if row else None

    async def update_prospect_email(
        self, prospect_id: str, email: str, email_status: str
    ):
        """Set a prospect's email + honesty status (verified/risky/guess/invalid)."""
        async with self._connect() as db:
            await db.execute(
                """UPDATE prospects
                   SET email = ?, email_status = ?, email_verified = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    _norm(email), email_status,
                    1 if email_status == "verified" else 0,
                    _utcnow().isoformat(), prospect_id,
                ),
            )
            await db.commit()

    # ── Email pattern cache (per-domain) ──

    async def get_email_pattern(self, domain: str) -> dict | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM email_patterns WHERE domain = ?", (_norm(domain),)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def save_email_pattern(
        self,
        domain: str,
        pattern: str = "",
        source: str = "",
        confidence: float = 0.0,
        mx_type: str = "",
        is_catch_all: int | None = None,
    ):
        """Upsert what we've learned about a domain's email conventions.

        Only overwrites the stored pattern when the new one has equal or
        higher confidence; mx_type/is_catch_all always refresh.
        """
        domain = _norm(domain)
        if not domain:
            return
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO email_patterns
                       (domain, pattern, source, confidence, mx_type, is_catch_all, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(domain) DO UPDATE SET
                       pattern = CASE WHEN excluded.confidence >= email_patterns.confidence
                                       AND excluded.pattern != ''
                                      THEN excluded.pattern ELSE email_patterns.pattern END,
                       source = CASE WHEN excluded.confidence >= email_patterns.confidence
                                      AND excluded.pattern != ''
                                     THEN excluded.source ELSE email_patterns.source END,
                       confidence = MAX(email_patterns.confidence, excluded.confidence),
                       mx_type = CASE WHEN excluded.mx_type != ''
                                      THEN excluded.mx_type ELSE email_patterns.mx_type END,
                       is_catch_all = CASE WHEN excluded.is_catch_all != -1
                                           THEN excluded.is_catch_all
                                           ELSE email_patterns.is_catch_all END,
                       updated_at = CURRENT_TIMESTAMP""",
                (
                    domain, pattern, source, float(confidence), mx_type,
                    -1 if is_catch_all is None else int(is_catch_all),
                ),
            )
            await db.commit()

    # ── Outbox (approval ladder + native sending) ──

    async def add_outbox_item(
        self,
        *,
        prospect_id: str,
        to_email: str,
        subject: str,
        body: str,
        send_at: str,
        status: str = "pending_review",
        campaign_id: str = "",
        conversation_id: str = "",
        step: int = 1,
        kind: str = "sequence",
        provider: str = "",
        thread_ref: str = "",
        in_reply_to: str = "",
        approved_by: str = "",
    ) -> str | None:
        """Queue one outgoing email. Returns its id, or None when the
        (campaign, prospect, step) slot already exists — the double-send guard.
        ``approved_by="autopilot"`` when it's queued approved on autopilot."""
        item_id = _new_id()
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO outbox
                   (id, campaign_id, prospect_id, conversation_id, step, kind,
                    to_email, subject, body, status, send_at, provider,
                    thread_ref, in_reply_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id, campaign_id, prospect_id, conversation_id,
                    int(step), kind, _norm(to_email), subject, body,
                    status, send_at, provider, thread_ref, in_reply_to,
                ),
            )
            if cursor.rowcount > 0 and approved_by:
                await db.execute("UPDATE outbox SET approved_by = ? WHERE id = ?", (approved_by, item_id))
            await db.commit()
            return item_id if cursor.rowcount > 0 else None

    async def get_outbox(
        self,
        status: str | None = None,
        due_before: str | None = None,
        limit: int = 200,
        exclude_kind: str | None = None,
        skip_unready_follow_ups: bool = False,
        kind: str | None = None,
        follow_ups_only: bool = False,
    ) -> list[dict]:
        """skip_unready_follow_ups: leave out sequence follow-ups whose
        previous step hasn't gone out yet, so they can't fill the page.
        kind: only that kind ("sequence" for cold email).
        follow_ups_only: only sequence emails after the first step."""
        where, params = [], []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if status:
            where.append("status = ?")
            params.append(status)
        if due_before:
            where.append("(send_at IS NULL OR send_at <= ?)")
            params.append(due_before)
        if exclude_kind:
            where.append("(kind IS NULL OR kind != ?)")
            params.append(exclude_kind)
        if follow_ups_only:
            where.append("o.kind = 'sequence' AND o.step > 1")
        if skip_unready_follow_ups:
            where.append(
                "NOT (o.kind = 'sequence' AND o.step > 1 AND EXISTS ("
                "SELECT 1 FROM outbox p WHERE p.prospect_id = o.prospect_id "
                "AND p.campaign_id = o.campaign_id AND p.kind = 'sequence' "
                "AND p.step = o.step - 1 "
                "AND p.status IN ('pending_review', 'approved', 'sending')))")
        sql = "SELECT * FROM outbox o"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY send_at ASC, created_at ASC LIMIT ?"
        params.append(int(limit))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def get_texts_for_prospect(self, prospect_id: str, limit: int = 50) -> list[str]:
        """Everything Harvey wrote for this person, which a reply may quote
        back: their Outbox emails (a sent one with its footer, whatever it
        said then) and the templates of campaigns they're in (Instantly and
        hand sends never pass through the Outbox; merge tags unfilled)."""
        texts: list[str] = []
        async with self._connect() as db:
            async with db.execute(
                "SELECT body FROM outbox WHERE prospect_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (prospect_id, int(limit)),
            ) as cursor:
                texts += [r[0] or "" for r in await cursor.fetchall()]
            async with db.execute(
                "SELECT sequence_json, prospect_ids_json FROM campaigns "
                "WHERE prospect_ids_json LIKE ? ORDER BY created_at DESC LIMIT ?",
                ("%" + prospect_id + "%", int(limit)),
            ) as cursor:
                rows = await cursor.fetchall()
        for sequence_json, ids_json in rows:
            try:
                if prospect_id not in json.loads(ids_json or "[]"):
                    continue
                texts += [str(step.get("body") or "") for step in json.loads(sequence_json or "[]")
                          if isinstance(step, dict)]
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
        return texts

    async def count_due_cold_outbox(
        self,
        due_before: str,
        stored_verdicts: tuple[str, str, str] | None = None,
        moved_back_prefix: str = "",
        has_cached_verdict: Callable[[dict], Awaitable[bool]] | None = None,
    ) -> int:
        """Approved cold email due by ``due_before``, leaving out follow-ups
        whose previous step hasn't gone out yet (as get_outbox's
        skip_unready_follow_ups does).

        stored_verdicts: (settings key prefix, inputs, checked after) of the
        compliance check's stored verdicts; email to an address with one
        still usable doesn't wait on the check, so it's left out.
        moved_back_prefix: also count approved cold email whose error starts
        with it (moved back by the check), due or not.
        has_cached_verdict: optional per-recipient cache check, for facts
        such as country that differ between recipients. Never calls an API."""
        due = "(o.send_at IS NULL OR o.send_at <= ?)"
        params: list = [due_before]
        if moved_back_prefix:
            due = f"({due} OR substr(COALESCE(o.error, ''), 1, ?) = ?)"
            params += [len(moved_back_prefix), moved_back_prefix]
        sql = ("SELECT COUNT(*) FROM outbox o WHERE o.status = 'approved' "
               f"AND o.kind = 'sequence' AND {due} "
               "AND NOT (o.step > 1 AND EXISTS ("
               "SELECT 1 FROM outbox p WHERE p.prospect_id = o.prospect_id "
               "AND p.campaign_id = o.campaign_id AND p.kind = 'sequence' "
               "AND p.step = o.step - 1 "
               "AND p.status IN ('pending_review', 'approved', 'sending')))")
        if stored_verdicts:
            # A verdict is stored as JSON under prefix + the lower-cased
            # address, with the facts it was decided on and when.
            sql += (" AND NOT EXISTS (SELECT 1 FROM settings s "
                    "WHERE s.key = ? || lower(trim(o.to_email)) "
                    "AND CASE WHEN json_valid(s.value) "
                    "THEN json_extract(s.value, '$.inputs') END = ? "
                    "AND CASE WHEN json_valid(s.value) "
                    "THEN json_extract(s.value, '$.verdict') END "
                    "IN ('ok_to_email', 'consent_needed', 'blocked') "
                    "AND CASE WHEN json_valid(s.value) "
                    "THEN julianday(json_extract(s.value, '$.checked_at')) END >= julianday(?))")
            params += list(stored_verdicts)
        async with self._connect() as db:
            if has_cached_verdict is not None:
                db.row_factory = aiosqlite.Row
                sql = sql.replace("SELECT COUNT(*)", "SELECT o.prospect_id, o.to_email", 1)
                async with db.execute(sql, params) as cursor:
                    items = [dict(row) for row in await cursor.fetchall()]
            else:
                async with db.execute(sql, params) as cursor:
                    return (await cursor.fetchone())[0]
        # Close the query connection before the callback reads recipient facts.
        waiting = 0
        for item in items:
            if not await has_cached_verdict(item):
                waiting += 1
        return waiting

    async def get_outbox_item(self, item_id: str) -> dict | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM outbox WHERE id = ?", (item_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    _OUTBOX_COLUMNS = frozenset({
        "status", "error", "message_id", "thread_ref", "sent_at",
        "subject", "body", "send_at", "provider",
    })

    async def update_outbox_item(self, item_id: str, **kwargs):
        fields = {k: v for k, v in kwargs.items() if k in self._OUTBOX_COLUMNS}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        async with self._connect() as db:
            await db.execute(
                f"UPDATE outbox SET {sets}, updated_at = ? WHERE id = ?",
                (*fields.values(), _utcnow().isoformat(), item_id),
            )
            await db.commit()

    async def claim_outbox_item(self, item_id: str) -> bool:
        """Take an approved item for sending: approved → sending, atomically.

        False when it is no longer approved and due (cancelled by a reply,
        bounce or hand send, rejected, rescheduled, or taken by another
        sender), so it must not go out now. Cancels never touch a claimed item: it is already on its way.
        """
        now = _utcnow().isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE outbox SET status = 'sending', updated_at = ? "
                "WHERE id = ? AND status = 'approved' "
                "AND (send_at IS NULL OR send_at <= ?)",
                (now, item_id, now),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def fail_stale_sending_outbox(
        self, older_than: timedelta = STALE_SENDING_AFTER
    ) -> int:
        """Items claimed long ago and never finished (Harvey stopped mid-send).

        Whether they went out is unknown, so they are never retried: they
        become failed with a note to check the Sent folder.
        """
        cutoff = (_utcnow() - older_than).isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE outbox SET status = 'failed', error = ?, updated_at = ? "
                "WHERE status = 'sending' AND datetime(updated_at) < datetime(?)",
                (STALE_SENDING_ERROR, _utcnow().isoformat(), cutoff),
            )
            await db.commit()
            return cursor.rowcount

    async def edit_outbox_draft(self, item_id: str, subject: str, body: str) -> int:
        """Rewrite a draft's subject and body — only while it awaits review.

        Atomic on status, like approve: an item already approved, sent or
        rejected is never changed. Editing never approves; that stays a
        separate, explicit step. Returns the number of rows changed (0/1).
        """
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE outbox SET subject = ?, body = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending_review'",
                (subject, body, _utcnow().isoformat(), item_id),
            )
            await db.commit()
            return cursor.rowcount

    async def restore_hand_cancelled_outbox(self, prospect_id: str, reason: str) -> int:
        """Undo a cancel made with `reason`: those items go back for review.

        Always to pending_review, even if they were approved before: a human
        decides again. They're rescheduled from now with their original gaps,
        so a late undo never releases a whole sequence at once. Items
        cancelled for any other reason stay cancelled.
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, send_at FROM outbox WHERE prospect_id = ? "
                "AND status = 'cancelled' AND error = ? ORDER BY send_at",
                (prospect_id, reason),
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]
            if not rows:
                return 0
            now = _utcnow()

            def _at(value):
                try:
                    return datetime.fromisoformat(str(value))
                except (TypeError, ValueError):
                    return now
            first = _at(rows[0]["send_at"])
            for r in rows:
                send_at = now + max(timedelta(0), _at(r["send_at"]) - first)
                await db.execute(
                    "UPDATE outbox SET status = 'pending_review', error = '', send_at = ?, "
                    "updated_at = ? WHERE id = ?",
                    (send_at.isoformat(), now.isoformat(), r["id"]),
                )
            await db.commit()
            return len(rows)

    async def reject_outbox_item(self, item_id: str) -> int:
        """Reject an item still waiting to go out. Mail already on its way
        or sent is never relabelled. Returns rows changed (0/1)."""
        async with self._connect() as db:
            # A note that the check will try it again no longer applies.
            cursor = await db.execute(
                "UPDATE outbox SET status = 'rejected', updated_at = ?, "
                "error = CASE WHEN substr(COALESCE(error, ''), 1, ?) = ? THEN '' ELSE error END "
                "WHERE id = ? AND status IN ('pending_review', 'approved')",
                (_utcnow().isoformat(), len(RECHECK_PREFIX), RECHECK_PREFIX, item_id),
            )
            await db.commit()
            return cursor.rowcount

    async def approve_outbox(self, item_id: str | None = None) -> int:
        """Approve one pending item, or ALL pending when item_id is None.

        A human's approval (dashboard or CLI): marked approved_by 'you', so
        the autopilot review (gate.autopilot_review) doesn't hold it again,
        and any reason it was held is cleared."""
        async with self._connect() as db:
            if item_id:
                cursor = await db.execute(
                    "UPDATE outbox SET status = 'approved', approved_by = 'you', error = '', "
                    "updated_at = ? WHERE id = ? AND status = 'pending_review'",
                    (_utcnow().isoformat(), item_id),
                )
            else:
                # Not the ones autopilot held for a look (possible planted
                # links or a leak): those are approved one at a time.
                cursor = await db.execute(
                    "UPDATE outbox SET status = 'approved', approved_by = 'you', error = '', "
                    "updated_at = ? WHERE status = 'pending_review' "
                    "AND COALESCE(error, '') NOT LIKE ?",
                    (_utcnow().isoformat(), HELD_FOR_REVIEW + "%"),
                )
            await db.commit()
            return cursor.rowcount

    async def note_sends_held(self, reason: str):
        """The heartbeat held sending this cycle. Keeps when it started."""
        now = _utcnow().isoformat()
        # A hold that went stale (Harvey stopped) doesn't date a new one.
        since = ((await self.current_sends_held()) or {}).get("since") or now
        await self.set_setting(SENDS_HELD_KEY, json.dumps(
            {"since": since, "last_seen": now, "reason": str(reason)[:500]}))

    async def clear_sends_held(self):
        if await self.get_setting(SENDS_HELD_KEY):
            await self.delete_setting(SENDS_HELD_KEY)

    async def current_sends_held(self, max_age_hours: float = 12) -> dict | None:
        """The hold, unless Harvey hasn't seen it for ``max_age_hours`` (it
        stopped, or the hold lifted without a cycle saying so)."""
        try:
            hold = json.loads(await self.get_setting(SENDS_HELD_KEY) or "null")
            seen = datetime.fromisoformat(hold["last_seen"])
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            return None
        if _utcnow() - seen > timedelta(hours=max_age_hours):
            return None
        return hold

    async def requeue_autopilot_approved(self) -> int:
        """Autopilot was turned off: everything it approved that hasn't gone
        out waits for you instead, and so does what was approved before
        Harvey recorded who approved ('earlier'; it can't tell). Yours stays
        approved. Returns how many."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE outbox SET status = 'pending_review', approved_by = '', "
                "error = ?, updated_at = ? WHERE status = 'approved' "
                "AND approved_by IN ('autopilot', 'earlier')",
                (AUTOPILOT_OFF_REASON, _utcnow().isoformat()),
            )
            await db.commit()
            return cursor.rowcount

    async def count_held_for_review(self) -> int:
        """Emails autopilot sent back for a human, still waiting."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM outbox WHERE status = 'pending_review' AND error LIKE ?",
                (HELD_FOR_REVIEW + "%",),
            ) as cursor:
                return (await cursor.fetchone())[0]

    async def cancel_pending_outbox_for_prospect(
        self, prospect_id: str, reason: str = "stop_on_reply"
    ) -> int:
        """Stop-on-reply: cancel everything queued for a prospect who replied."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE outbox SET status = 'cancelled', error = ?, updated_at = ? "
                "WHERE prospect_id = ? AND status IN ('pending_review', 'approved')",
                (reason, _utcnow().isoformat(), prospect_id),
            )
            await db.commit()
            return cursor.rowcount

    async def stop_company(self, prospect_id: str) -> int:
        """Someone replied: mark their company as answered and cancel the
        sequence emails queued for everyone else there. Replies Harvey owes
        a colleague who wrote in themselves are not touched."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT first_name, last_name, email FROM prospects WHERE id = ?",
                (prospect_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return 0
            who = " ".join(x for x in (row["first_name"], row["last_name"]) if x) or row["email"]
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (COMPANY_STOP_KEY + prospect_id, who, _utcnow().isoformat()),
            )
            cursor = await db.execute(
                "UPDATE outbox SET status = 'cancelled', error = ?, updated_at = ? "
                "WHERE kind = 'sequence' AND status IN ('pending_review', 'approved') "
                "AND prospect_id IN (SELECT c.id FROM prospects c, prospects p "
                f"WHERE p.id = ? AND {_colleagues('p', 'c')})",
                (f"{COLLEAGUE_REPLIED} ({who})", _utcnow().isoformat(), prospect_id),
            )
            await db.commit()
            return cursor.rowcount

    async def colleague_who_replied(self, prospect_id: str) -> str | None:
        """The name of someone at this prospect's company who has replied,
        or None. A cold email to them is no longer cold: the company answered."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT s.value FROM prospects p, prospects c, settings s "
                f"WHERE p.id = ? AND s.key = '{COMPANY_STOP_KEY}' || c.id "
                f"AND {_colleagues('p', 'c')} LIMIT 1",
                (prospect_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else None

    async def count_outbox_sent(self) -> int:
        async with self._connect() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM outbox WHERE status = 'sent'"
            ) as cursor:
                return (await cursor.fetchone())[0]

    async def count_outbox_sent_today(self, kind: str | None = None,
                                      since: str | None = None) -> int:
        """Emails sent today (the UTC day, or since `since`, a naive UTC
        ISO time); only one kind ("sequence", "reply") if given."""
        if since:
            sql = "SELECT COUNT(*) FROM outbox WHERE status = 'sent' AND sent_at >= ?"
            params: tuple = (since,)
        else:
            sql = ("SELECT COUNT(*) FROM outbox WHERE status = 'sent' "
                   "AND date(sent_at) = date('now')")
            params = ()
        if kind:
            sql += " AND kind = ?"
            params += (kind,)
        async with self._connect() as db:
            async with db.execute(sql, params) as cursor:
                return (await cursor.fetchone())[0]

    async def get_step_delay_days(self, campaign_id: str, step: int) -> int | None:
        """The campaign's planned wait before `step` (days after the step
        before it), or None when the campaign or step isn't known."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT sequence_json FROM campaigns WHERE id = ?", (campaign_id or "",)
            ) as cursor:
                row = await cursor.fetchone()
        try:
            for s in json.loads((row[0] if row else None) or "[]"):
                if isinstance(s, dict) and int(s.get("step", 0)) == int(step):
                    return max(0, int(s.get("delay_days") or 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return None

    async def get_previous_step(self, item: dict) -> dict | None:
        """The email a sequence follow-up follows (same campaign and person,
        one step earlier), or None."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM outbox WHERE prospect_id = ? AND campaign_id = ? "
                "AND kind = 'sequence' AND step = ? ORDER BY created_at DESC LIMIT 1",
                (item["prospect_id"], item.get("campaign_id") or "", int(item["step"]) - 1),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def find_outbox_by_message_id(self, message_id: str) -> dict | None:
        if not message_id:
            return None
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM outbox WHERE message_id = ?", (message_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    # ── Signal vocabulary (governed; user-confirmed before collection) ──

    async def upsert_signal_code(
        self,
        code: str,
        *,
        label: str = "",
        description: str = "",
        category: str = "",
        value_type: str = "text",
        collector: str = "",
        cost_note: str = "",
        confidence_floor: float = 0.0,
        status: str | None = None,
    ):
        """Register a signal in the vocabulary. Never downgrades a user's
        decision: an existing row's ``status`` is preserved unless explicitly
        passed, so re-seeding can't silently re-enable a rejected signal."""
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO signal_codes
                       (code, label, description, category, value_type,
                        collector, cost_note, confidence_floor, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'proposed'))
                   ON CONFLICT(code) DO UPDATE SET
                       label = excluded.label,
                       description = excluded.description,
                       category = excluded.category,
                       value_type = excluded.value_type,
                       collector = excluded.collector,
                       cost_note = excluded.cost_note,
                       confidence_floor = excluded.confidence_floor,
                       status = COALESCE(?, signal_codes.status),
                       updated_at = CURRENT_TIMESTAMP""",
                (code, label, description, category, value_type, collector,
                 cost_note, float(confidence_floor), status, status),
            )
            await db.commit()

    async def get_signal_codes(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM signal_codes"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY category, code"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def set_signal_status(self, code: str, status: str) -> bool:
        """Confirm / reject a proposed signal. Only confirmed signals are
        collected — Harvey proposes, the user decides."""
        if status not in ("proposed", "confirmed", "rejected"):
            raise ValueError(f"invalid signal status: {status}")
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE signal_codes SET status = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE code = ?",
                (status, code),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def confirmed_signal_codes(self) -> set[str]:
        return {r["code"] for r in await self.get_signal_codes(status="confirmed")}

    # ── Observations (every fact is a row, never a column) ──

    async def add_observation(
        self,
        signal_code: str,
        *,
        company_id: str = "",
        prospect_id: str = "",
        collector: str = "",
        value_num: float | None = None,
        value_text: str = "",
        confidence: float = 1.0,
        evidence_url: str = "",
        run_id: str = "",
        observed_at: str | None = None,
    ) -> str:
        """Record one fact. Raises if signal_code isn't in the vocabulary —
        a typo must fail loudly rather than create a junk signal."""
        obs_id = _new_id()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO observations
                       (id, company_id, prospect_id, signal_code, collector,
                        value_num, value_text, confidence, evidence_url,
                        observed_at, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                           COALESCE(?, CURRENT_TIMESTAMP), ?)""",
                (obs_id, company_id, prospect_id, signal_code, collector,
                 value_num, value_text, float(confidence), evidence_url,
                 observed_at, run_id),
            )
            await db.commit()
        return obs_id

    async def add_observations(self, rows: list[dict], run_id: str = "") -> int:
        """Batch insert. Flushed per batch by callers — a long run that dies
        must not lose everything it observed."""
        if not rows:
            return 0
        payload = [
            (
                _new_id(), r.get("company_id", ""), r.get("prospect_id", ""),
                r["signal_code"], r.get("collector", ""), r.get("value_num"),
                r.get("value_text", ""), float(r.get("confidence", 1.0)),
                r.get("evidence_url", ""), r.get("observed_at"),
                r.get("run_id", run_id),
            )
            for r in rows
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO observations
                       (id, company_id, prospect_id, signal_code, collector,
                        value_num, value_text, confidence, evidence_url,
                        observed_at, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                           COALESCE(?, CURRENT_TIMESTAMP), ?)""",
                payload,
            )
            await db.commit()
        return len(payload)

    async def yc_batch(self, company_id: str) -> str | None:
        """The YC batch the company says it's in ("" when it doesn't say
        which), or None when it isn't YC-backed or nobody checked. The
        latest reading wins; profiling never writes "no" over a YC-preset
        search hit, but does over its own earlier "yes"."""
        if not company_id:
            return None
        rows = await self.get_observations(company_id=company_id, signal_code="YC_BACKED",
                                           latest_only=True, limit=1)
        if rows and rows[0].get("value_num") == 1:
            return rows[0].get("value_text") or ""
        return None

    async def yc_search_hit(self, company_id: str) -> bool:
        """Did a YC-preset search find this company (its latest YC reading
        is a discovery hit)?"""
        return await self.search_hit(company_id, "YC_BACKED")

    async def search_hit(self, company_id: str, signal_code: str) -> bool:
        """Is the company's latest reading of ``signal_code`` a preset
        search's hit (collector "discover")? Profiling doesn't write "not
        stated" over one."""
        rows = await self.get_observations(company_id=company_id, signal_code=signal_code,
                                           latest_only=True, limit=1)
        return bool(rows and rows[0].get("collector") == "discover" and rows[0].get("value_num") == 1)

    async def _latest_yes(self, company_id: str, signal_code: str) -> dict | None:
        if not company_id:
            return None
        rows = await self.get_observations(company_id=company_id, signal_code=signal_code,
                                           latest_only=True, limit=1)
        return rows[0] if rows and rows[0].get("value_num") == 1 else None

    async def accelerator(self, company_id: str) -> str | None:
        """The accelerator(s) the company says it went through ("Techstars
        2024; Antler", or "" for a search hit that didn't say which), or
        None. YC is yc_batch's."""
        row = await self._latest_yes(company_id, "ACCELERATOR")
        return (row.get("value_text") or "") if row else None

    async def funding_round(self, company_id: str) -> dict | None:
        """The latest raise the company announced on its own site, as
        {"round", "amount", "date"} (any may be ""), or None."""
        row = await self._latest_yes(company_id, "FUNDING_ROUND")
        return parse_funding(row.get("value_text") or "") if row else None

    async def get_observations(
        self,
        company_id: str = "",
        signal_code: str = "",
        latest_only: bool = False,
        limit: int = 500,
    ) -> list[dict]:
        where, params = [], []
        if company_id:
            where.append("company_id = ?")
            params.append(company_id)
        if signal_code:
            where.append("signal_code = ?")
            params.append(signal_code)
        sql = "SELECT * FROM observations"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if latest_only:
            # Newest observation per (company, signal) — the current view of
            # the world, with history still on disk underneath.
            sql = (
                "SELECT * FROM (" + sql + " ORDER BY observed_at DESC) "
                "GROUP BY company_id, signal_code"
            )
        else:
            sql += " ORDER BY observed_at DESC"
        sql += " LIMIT ?"
        params.append(int(limit))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    # A boolean signal is recorded either way: "checked, not running ads" is a
    # real finding, and a later flip from 0 to 1 is a real event. But a COHORT
    # asks who *has* the signal, so it must read the value, not merely the
    # presence of a row. Without this, "companies running Google Ads" silently
    # means "companies we checked for Google Ads" — which is everyone.
    #
    # `latest` is the current view of the world (newest observation per company
    # and signal, with history still on disk underneath). `positive` is the
    # subset where the finding is actually true — value_num IS NULL covers text
    # signals like INCUMBENT_AGENCY, where the row's existence IS the finding.
    # Rank signals are different: a company has one rank PER SEARCH PHRASE
    # (value_text), so "is it ranked" and "rank 11-30" read the newest row of
    # each phrase — #4 for one search and #18 for another is in range on the
    # second. Ties within one second (two searches in a run) go to the row
    # written last. A search that no longer finds the company writes 0 for
    # that phrase, which reads as absent. Rows from before phrases were
    # stored (empty value_text) stop counting once a phrased row exists.
    _RANK_CODES = "('SERP_RANK', 'IN_LOCAL_PACK', 'MAPS_RANK')"
    _CURRENT_CTE = f"""
    WITH latest AS (
        SELECT o.* FROM observations o
        JOIN (SELECT company_id, signal_code, MAX(observed_at) AS t
              FROM observations GROUP BY company_id, signal_code) newest
          ON newest.company_id = o.company_id
         AND newest.signal_code = o.signal_code
         AND newest.t = o.observed_at
        WHERE o.signal_code NOT IN {_RANK_CODES}
    ),
    ranked AS (
        SELECT o.* FROM observations o
        WHERE o.signal_code IN {_RANK_CODES}
          AND o.rowid = (
              SELECT n.rowid FROM observations n
              WHERE n.company_id = o.company_id
                AND n.signal_code = o.signal_code
                AND COALESCE(n.value_text, '') = COALESCE(o.value_text, '')
              ORDER BY n.observed_at DESC, n.rowid DESC LIMIT 1)
          AND NOT (COALESCE(o.value_text, '') = '' AND EXISTS (
              SELECT 1 FROM observations p
              WHERE p.company_id = o.company_id
                AND p.signal_code = o.signal_code
                AND COALESCE(p.value_text, '') != ''))
    ),
    positive AS (
        SELECT * FROM latest WHERE value_num IS NULL OR value_num != 0
        UNION ALL
        SELECT * FROM ranked WHERE value_num IS NOT NULL AND value_num != 0
    )
    """

    async def signal_counts(self) -> list[dict]:
        """How many entities actually carry each signal — the cohort sizes."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                self._CURRENT_CTE + """
                SELECT p.signal_code,
                       COALESCE(sc.label, p.signal_code) AS label,
                       sc.status AS status,
                       COUNT(DISTINCT p.company_id) AS companies,
                       COUNT(*) AS observations
                FROM positive p
                LEFT JOIN signal_codes sc ON sc.code = p.signal_code
                GROUP BY p.signal_code
                ORDER BY companies DESC"""
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def cohort(
        self,
        require: list[str],
        exclude: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 500,
    ) -> list[str]:
        """Company IDs carrying ALL required signals and none excluded.

        Set intersection happens in SQL — doing it in application code over a
        capped SELECT silently returns the wrong cohort.
        """
        if not require:
            return []
        req_ph = ",".join("?" for _ in require)
        sql = self._CURRENT_CTE + (
            f"SELECT company_id FROM positive "
            f"WHERE signal_code IN ({req_ph}) AND company_id != '' "
            "AND confidence >= ? "
            "GROUP BY company_id HAVING COUNT(DISTINCT signal_code) = ?"
        )
        params: list = [*require, float(min_confidence), len(set(require))]
        if exclude:
            exc_ph = ",".join("?" for _ in exclude)
            sql += (
                " AND company_id NOT IN ("
                f"SELECT company_id FROM positive WHERE signal_code IN ({exc_ph})"
                ")"
            )
            params.extend(exclude)
        sql += " LIMIT ?"
        params.append(int(limit))
        async with self._connect() as db:
            async with db.execute(sql, params) as cursor:
                return [row[0] for row in await cursor.fetchall()]

    async def ranked_company_ids(self, code: str, phrase: str) -> set[str]:
        """Companies currently ranked (or in the pack) for this search phrase."""
        async with self._connect() as db:
            async with db.execute(
                self._CURRENT_CTE + " SELECT DISTINCT company_id FROM positive "
                "WHERE signal_code = ? AND value_text = ? AND company_id != ''",
                (code, phrase),
            ) as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def cohort_companies(self, require: list[str], exclude: list[str],
                               offset: int = 0, limit: int | None = None,
                               ranges: list[dict] | None = None) -> tuple[int, list[dict]]:
        """Count all matches and page company details in one read snapshot.

        ``ranges`` are numeric filters, ``[{"code", "min", "max"}]`` with
        either bound optional: organic rank 11-30 is
        ``{"code": "SERP_RANK", "min": 11, "max": 30}``. A range implies the
        signal is present, so a range alone is a cohort.
        """
        ranges = list(ranges or [])
        if not require and not ranges:
            return 0, []
        require = list(dict.fromkeys(require))
        exclude = list(dict.fromkeys(exclude))
        where, params = [], []
        if require:
            where.append(
                "id IN (SELECT company_id FROM positive WHERE company_id != '' AND "
                f"signal_code IN ({','.join('?' for _ in require)}) "
                "AND confidence >= 0 GROUP BY company_id "
                "HAVING COUNT(DISTINCT signal_code) = ?)")
            params += [*require, len(require)]
        if exclude:
            where.append("id NOT IN (SELECT company_id FROM positive "
                         f"WHERE signal_code IN ({','.join('?' for _ in exclude)}))")
            params.extend(exclude)
        for r in ranges:
            # The same current, positive, confidence >= 0 view require uses.
            sub = ("id IN (SELECT company_id FROM positive WHERE signal_code = ? "
                   "AND value_num IS NOT NULL AND confidence >= 0")
            params.append(r["code"])
            if r.get("min") is not None:
                sub += " AND value_num >= ?"
                params.append(float(r["min"]))
            if r.get("max") is not None:
                sub += " AND value_num <= ?"
                params.append(float(r["max"]))
            where.append(sub + ")")
        base = (self._CURRENT_CTE +
                " SELECT {columns} FROM companies WHERE " + " AND ".join(where))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            async with db.execute(base.format(columns="COUNT(*)"), params) as cursor:
                size = (await cursor.fetchone())[0]
            sql = base.format(columns="id, name, domain, website, industry, location")
            sql += " ORDER BY LOWER(COALESCE(name, '')), id"
            page_params = list(params)
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                page_params.extend([limit, offset])
            async with db.execute(sql, page_params) as cursor:
                return size, [dict(row) for row in await cursor.fetchall()]

    async def companies_needing_profile(
        self, limit: int = 100, stale_days: int = 90
    ) -> list[dict]:
        """Companies a profile run should visit next.

        Never checked, or last checked longer ago than ``stale_days`` —
        ordered nulls-first so it rotates through the whole backlog instead
        of retrying the same top-N-by-created_at companies every cycle.
        ``profile_checked_at`` is set on every attempt regardless of outcome
        (see ProfileCollector.run), so an unreachable site still counts as
        checked and rotates out instead of being retried forever. A profile
        observation written any other way still counts too, whichever is newer.

        Businesses with no domain are excluded — there is no site to read.
        They are not a failure, they are the NO_WEBSITE cohort.
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""SELECT c.id, c.name, c.domain
                   FROM companies c
                   {_LAST_PROFILED_JOIN}
                   WHERE c.domain != ''
                     AND ({_LAST_PROFILED} IS NULL
                          OR {_LAST_PROFILED} < datetime('now', ?))
                   ORDER BY {_LAST_PROFILED} IS NOT NULL, {_LAST_PROFILED}
                   LIMIT ?""",
                (f"-{int(stale_days)} days", int(limit)),
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def count_companies_needing_profile(self, stale_days: int = 90) -> int:
        async with self._connect() as db:
            async with db.execute(
                f"""SELECT COUNT(*) FROM companies c
                   {_LAST_PROFILED_JOIN}
                   WHERE c.domain != ''
                     AND ({_LAST_PROFILED} IS NULL
                          OR {_LAST_PROFILED} < datetime('now', ?))""",
                (f"-{int(stale_days)} days",),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def mark_company_profiled(self, company_id: str):
        """Record that a profile pass looked at this company, whether or
        not it was reachable — advances the rotation regardless."""
        async with self._connect() as db:
            await db.execute(
                """UPDATE companies SET profile_checked_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (company_id,),
            )
            await db.commit()

    async def rename_company(self, company_id: str, name: str):
        """Set a company's name, and the copy each of its prospects carries."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE companies SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, company_id),
            )
            await db.execute(
                "UPDATE prospects SET company = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE company_id = ?",
                (name, company_id),
            )
            await db.commit()

    async def companies_needing_prospects(
        self, limit: int = 10, stale_days: int = 14
    ) -> list[dict]:
        """Companies with a domain, zero prospects, and no recent check.

        `harvey discover` (and other company-only sources) can add companies
        without ever finding a named contact at them. This is what lets a
        prospecting pass go back and fill that in, instead of only ever
        looking at companies it stumbles into fresh in the same cycle.

        Ordered by ``prospects_checked_at`` (nulls first) rather than
        recency, so a backfill pass rotates through the whole backlog
        instead of retrying the same top-N-by-created_at companies every
        cycle — a company a team page didn't exist for last time is worth
        another look after ``stale_days``, not a permanent skip.
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT c.id, c.name, c.domain, c.industry
                   FROM companies c
                   LEFT JOIN prospects p ON p.company_id = c.id
                   WHERE c.domain != ''
                     AND (c.prospects_checked_at IS NULL
                          OR c.prospects_checked_at < datetime('now', ?))
                   GROUP BY c.id
                   HAVING COUNT(p.id) = 0
                   ORDER BY c.prospects_checked_at IS NOT NULL, c.prospects_checked_at
                   LIMIT ?""",
                (f"-{int(stale_days)} days", int(limit)),
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def mark_prospects_checked(self, company_id: str):
        """Record that a prospect-backfill pass looked at this company,
        whether or not it found anyone — advances the rotation regardless."""
        async with self._connect() as db:
            await db.execute(
                """UPDATE companies SET prospects_checked_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (company_id,),
            )
            await db.commit()

    # ── Run log (what ran, when, what it produced and cost) ──

    async def start_run(
        self, stage: str, provider: str = "", params: dict | None = None
    ) -> str:
        run_id = _new_id()
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO runs (id, stage, provider, params_json, status) "
                "VALUES (?, ?, ?, ?, 'running')",
                (run_id, stage, provider, json.dumps(params or {})),
            )
            await db.commit()
        return run_id

    async def finish_run(
        self,
        run_id: str,
        status: str = "completed",
        records: int = 0,
        cost_usd: float = 0.0,
        error: str = "",
    ):
        async with self._connect() as db:
            await db.execute(
                "UPDATE runs SET status = ?, records = ?, cost_usd = ?, "
                "error = ?, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, int(records), float(cost_usd), error[:500], run_id),
            )
            await db.commit()

    async def sweep_stale_runs(self, older_than_hours: int = 6) -> int:
        """A killed collector can't close its own run — never trust it to."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE runs SET status = 'stale', ended_at = CURRENT_TIMESTAMP "
                "WHERE status = 'running' "
                "AND started_at < datetime('now', ?)",
                (f"-{int(older_than_hours)} hours",),
            )
            await db.commit()
            return cursor.rowcount

    async def get_runs(self, limit: int = 25) -> list[dict]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (int(limit),)
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    # ── Settings (operational flags: kill switch, counters) ──

    FOOTER_HISTORY_KEY = "compliance_footer_history"

    async def remember_footer_lines(self, lines) -> None:
        """Keep every footer line Harvey has sent under a cold email, so a
        reply quoting an old one (since reworded) is still known as ours."""
        lines = [l.strip() for l in lines if l and l.strip()]
        if not lines:
            return
        try:
            known = json.loads(await self.get_setting(self.FOOTER_HISTORY_KEY) or "[]")
        except (json.JSONDecodeError, TypeError):
            known = []
        if not isinstance(known, list):
            known = []
        merged = [l for l in known if isinstance(l, str)]
        merged += [l for l in lines if l not in merged]
        if merged != known:
            await self.set_setting(self.FOOTER_HISTORY_KEY, json.dumps(merged[-50:]))

    async def get_footer_history(self) -> list[str]:
        try:
            known = json.loads(await self.get_setting(self.FOOTER_HISTORY_KEY) or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [l for l in known if isinstance(l, str)] if isinstance(known, list) else []

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self._connect() as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    async def retry_write_failed(self) -> int:
        """Give every 'write_failed' prospect another try. Returns how many."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE prospects SET status = 'new', updated_at = ? "
                "WHERE status = 'write_failed'",
                (_utcnow().isoformat(),),
            )
            await db.commit()
            return cursor.rowcount

    async def delete_setting(self, key: str):
        async with self._connect() as db:
            await db.execute("DELETE FROM settings WHERE key = ?", (key,))
            await db.commit()

    async def set_setting(self, key: str, value: str):
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, str(value)),
            )
            await db.commit()

    async def increment_setting(self, key: str, by: int = 1) -> int:
        current = await self.get_setting(key, "0")
        try:
            value = int(current) + by
        except ValueError:
            value = by
        await self.set_setting(key, str(value))
        return value

    async def prospect_exists(
        self, email: str = "", linkedin_url: str = "",
        first_name: str = "", last_name: str = "", company: str = "",
    ) -> bool:
        async with self._connect() as db:
            if email:
                async with db.execute(
                    "SELECT 1 FROM prospects WHERE email = ?", (_norm(email),)
                ) as cursor:
                    if await cursor.fetchone():
                        return True
            if linkedin_url:
                async with db.execute(
                    "SELECT 1 FROM prospects WHERE linkedin_url = ?",
                    (linkedin_url.strip(),),
                ) as cursor:
                    if await cursor.fetchone():
                        return True
            # Name + company dedup (case-insensitive, expression-indexed)
            if first_name and last_name and company:
                async with db.execute(
                    """SELECT 1 FROM prospects
                       WHERE LOWER(first_name) = ? AND LOWER(last_name) = ?
                         AND LOWER(company) = ?""",
                    (first_name.lower(), last_name.lower(), company.lower()),
                ) as cursor:
                    if await cursor.fetchone():
                        return True
        return False

    async def count_writable_prospects(self) -> int:
        """New prospects the Writer can draft for (a deliverable email)."""
        marks = ",".join("?" for _ in WRITABLE_EMAIL_STATUSES)
        async with self._connect() as db:
            async with db.execute(
                f"""SELECT COUNT(*) FROM prospects p
                    WHERE status = 'new' AND email != ''
                      AND email_status IN ({marks}) AND NOT {_company_answered('p')}""",
                WRITABLE_EMAIL_STATUSES,
            ) as cursor:
                return (await cursor.fetchone())[0]

    async def count_recent_unusable_prospects(self, hours: int = 24) -> int:
        """New prospects found in the last ``hours`` that the Writer can't use."""
        since = (_utcnow() - timedelta(hours=hours)).isoformat()
        marks = ",".join("?" for _ in WRITABLE_EMAIL_STATUSES)
        async with self._connect() as db:
            async with db.execute(
                f"""SELECT COUNT(*) FROM prospects
                    WHERE status = 'new'
                      AND NOT (COALESCE(email, '') != ''
                               AND COALESCE(email_status, '') IN ({marks}))
                      AND datetime(created_at) >= datetime(?)""",
                (*WRITABLE_EMAIL_STATUSES, since),
            ) as cursor:
                return (await cursor.fetchone())[0]

    async def count_prospects_by_status(self) -> dict[str, int]:
        async with self._connect() as db:
            async with db.execute(
                "SELECT status, COUNT(*) FROM prospects GROUP BY status"
            ) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}

    # ── Feedback ──

    async def add_feedback(
        self, entity_type: str, entity_id: str, comment: str
    ) -> str:
        feedback_id = _new_id()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO feedback (id, entity_type, entity_id, comment)
                   VALUES (?, ?, ?, ?)""",
                (feedback_id, entity_type, entity_id, comment),
            )
            await db.commit()
        return feedback_id

    async def get_feedback(
        self, entity_type: str, entity_id: str
    ) -> list[dict]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM feedback
                   WHERE entity_type = ? AND entity_id = ?
                   ORDER BY created_at DESC""",
                (entity_type, entity_id),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_all_feedback(self) -> list[dict]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100"
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_writing_feedback(
        self, prospect_ids: list[str] | tuple = (), limit: int = 10
    ) -> list[dict]:
        """What the owner said about past campaigns (newest first), plus
        anything said about these contacts: the Writer follows it.

        Each row: {"about": "campaign 'Denver roofers'" | "Dana Ruiz",
        "comment": ...}."""
        out: list[dict] = []
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT f.comment, f.created_at, c.name FROM feedback f
                   LEFT JOIN campaigns c ON c.id = f.entity_id
                   WHERE f.entity_type = 'campaign' AND TRIM(f.comment) != ''
                   ORDER BY f.created_at DESC LIMIT ?""",
                (int(limit),),
            ) as cursor:
                for r in await cursor.fetchall():
                    about = f"campaign '{r['name']}'" if r["name"] else "a past campaign"
                    out.append({"about": about, "comment": r["comment"],
                                "created_at": r["created_at"]})
            ids = [i for i in prospect_ids if i][:50]
            if ids:
                marks = ",".join("?" * len(ids))
                async with db.execute(
                    f"""SELECT f.comment, f.created_at, p.first_name, p.last_name FROM feedback f
                        JOIN prospects p ON p.id = f.entity_id
                        WHERE f.entity_type = 'contact' AND f.entity_id IN ({marks})
                          AND TRIM(f.comment) != ''
                        ORDER BY f.created_at DESC LIMIT ?""",
                    (*ids, int(limit)),
                ) as cursor:
                    for r in await cursor.fetchall():
                        name = " ".join(x for x in (r["first_name"], r["last_name"]) if x)
                        out.append({"about": name or "this contact", "comment": r["comment"],
                                    "created_at": r["created_at"]})
        out.sort(key=lambda n: str(n.get("created_at") or ""), reverse=True)  # newest first
        return out

    # ── Reply Deduplication ──

    async def is_reply_processed(self, reply_id: str) -> bool:
        """Check if a reply has already been processed."""
        if not reply_id:
            return False
        async with self._connect() as db:
            async with db.execute(
                "SELECT 1 FROM processed_replies WHERE reply_id = ?", (reply_id,)
            ) as cursor:
                return bool(await cursor.fetchone())

    async def mark_reply_processed(self, reply_id: str):
        """Mark a reply as processed to avoid double-handling."""
        if not reply_id:
            return
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO processed_replies (reply_id) VALUES (?)",
                (reply_id,),
            )
            await db.commit()

    # ── Campaigns ──

    async def add_campaign(self, campaign: Campaign) -> str:
        if not campaign.id:
            campaign.id = _new_id()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO campaigns
                   (id, name, channel, instantly_campaign_id, sequence_json,
                    prospect_ids_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    campaign.id, campaign.name, campaign.channel,
                    campaign.instantly_campaign_id, campaign.sequence_json(),
                    json.dumps(campaign.prospect_ids), campaign.status,
                    campaign.created_at.isoformat(),
                ),
            )
            await db.commit()
        return campaign.id

    async def get_campaign(self, campaign_id: str) -> Campaign | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                d = dict(row)
                d["sequence"] = Campaign.sequence_from_json(d.pop("sequence_json", None))
                d["prospect_ids"] = json.loads(d.pop("prospect_ids_json", None) or "[]")
                return Campaign(**d)

    async def get_campaigns_by_status(self, status: str) -> list[Campaign]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM campaigns WHERE status = ?", (status,)
            ) as cursor:
                rows = await cursor.fetchall()
                campaigns = []
                for r in rows:
                    d = dict(r)
                    d["sequence"] = Campaign.sequence_from_json(d.pop("sequence_json"))
                    d["prospect_ids"] = json.loads(d.pop("prospect_ids_json") or "[]")
                    campaigns.append(Campaign(**d))
                return campaigns

    async def update_campaign(self, campaign_id: str, **kwargs):
        """Update whitelisted campaign columns in a single atomic statement."""
        if not kwargs:
            return
        invalid = set(kwargs) - _CAMPAIGN_COLUMNS
        if invalid:
            raise ValueError(f"Invalid campaign column(s): {sorted(invalid)}")
        set_clause = ", ".join(f"{key} = ?" for key in kwargs)
        async with self._connect() as db:
            await db.execute(
                f"UPDATE campaigns SET {set_clause} WHERE id = ?",
                (*kwargs.values(), campaign_id),
            )
            await db.commit()

    # ── Conversations ──

    async def add_conversation(self, convo: Conversation) -> str:
        if not convo.id:
            convo.id = _new_id()
        async with self._connect() as db:
            priority = getattr(convo, "priority", "medium") or "medium"
            await db.execute(
                """INSERT INTO conversations
                   (id, prospect_id, campaign_id, channel, thread_json,
                    intent, priority, stage, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    convo.id, (convo.prospect_id if convo.prospect_id else None), convo.campaign_id,
                    convo.channel, convo.thread_json(), convo.intent,
                    priority, convo.stage, convo.status, convo.created_at.isoformat(),
                    convo.updated_at.isoformat(),
                ),
            )
            await db.commit()
        return convo.id

    async def get_conversation(self, convo_id: str) -> Conversation | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM conversations WHERE id = ?", (convo_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                d = dict(row)
                d["thread"] = Conversation.thread_from_json(d.pop("thread_json"))
                return Conversation(**d)

    async def get_conversations_by_status(self, status: str) -> list[Conversation]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM conversations WHERE status = ?", (status,)
            ) as cursor:
                rows = await cursor.fetchall()
                convos = []
                for r in rows:
                    d = dict(r)
                    d["thread"] = Conversation.thread_from_json(d.pop("thread_json"))
                    convos.append(Conversation(**d))
                return convos

    async def update_conversation(self, convo_id: str, **kwargs):
        """Update whitelisted conversation columns atomically (bumps updated_at)."""
        if not kwargs:
            return
        invalid = set(kwargs) - _CONVERSATION_COLUMNS
        if invalid:
            raise ValueError(f"Invalid conversation column(s): {sorted(invalid)}")
        set_clause = ", ".join(f"{key} = ?" for key in kwargs)
        async with self._connect() as db:
            await db.execute(
                f"UPDATE conversations SET {set_clause}, updated_at = ? WHERE id = ?",
                (*kwargs.values(), _utcnow().isoformat(), convo_id),
            )
            await db.commit()

    # ── Actions Log ──

    async def log_action(self, action_type: str, agent: str, details: dict | None = None):
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO actions (id, action_type, agent, details_json) VALUES (?, ?, ?, ?)",
                (_new_id(), action_type, agent, json.dumps(details or {})),
            )
            await db.commit()

    async def get_actions_for_campaign(self, campaign_id: str) -> list[dict]:
        """Chronological audit actions for a specific campaign, newest first."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id, action_type, agent, details_json, created_at
                   FROM actions
                   WHERE details_json LIKE '%' || ? || '%'
                   ORDER BY created_at DESC, rowid DESC""",
                (campaign_id,),
            ) as cursor:
                rows = await cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    details = json.loads(d.get("details_json") or "{}")
                except Exception:
                    details = {}
                if details.get("campaign_id") == campaign_id:
                    d["details"] = details
                    out.append(d)
            return out

    # ── Analytics ──

    async def get_campaign_stats(self) -> list[dict]:
        """Get performance stats for each campaign (one indexed pass over conversations)."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT c.id, c.name, c.status, c.prospect_ids_json,
                          COUNT(v.id) as reply_count,
                          SUM(CASE WHEN v.intent = 'interested' THEN 1 ELSE 0 END) as interested_count,
                          SUM(CASE WHEN v.intent = 'objection' THEN 1 ELSE 0 END) as objection_count,
                          SUM(CASE WHEN v.intent = 'not_interested' THEN 1 ELSE 0 END) as not_interested_count
                   FROM campaigns c
                   LEFT JOIN conversations v ON v.campaign_id = c.id
                   WHERE c.status IN ('active', 'completed')
                   GROUP BY c.id
                   ORDER BY c.created_at DESC"""
            ) as cursor:
                rows = await cursor.fetchall()
                stats = []
                for r in rows:
                    d = dict(r)
                    for key in ("interested_count", "objection_count", "not_interested_count"):
                        d[key] = d[key] or 0
                    prospect_ids = json.loads(d.get("prospect_ids_json") or "[]")
                    d["leads_count"] = len(prospect_ids)
                    d["reply_rate"] = (
                        round(d["reply_count"] / len(prospect_ids) * 100, 1)
                        if prospect_ids else 0
                    )
                    stats.append(d)
                return stats

    async def get_intent_distribution(self) -> dict[str, int]:
        """Count conversations by intent."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT intent, COUNT(*) FROM conversations WHERE intent != '' GROUP BY intent"
            ) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}

    async def get_stage_distribution(self) -> dict[str, int]:
        """Count conversations by sales stage."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT stage, COUNT(*) FROM conversations WHERE stage != '' GROUP BY stage"
            ) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}

    # ── Usage Tracking ──

    async def get_usage_today(self, provider: str = "claude") -> int:
        """Brain calls made today on one provider's subscription."""
        column = _USAGE_LOG_COLUMN.get(provider)
        today = date.today().isoformat()
        async with self._connect() as db:
            if column:
                async with db.execute(
                    f"SELECT {column} FROM usage_log WHERE date = ?", (today,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return (row[0] or 0) if row else 0
            else:
                async with db.execute(
                    "SELECT COUNT(*) FROM usage_events WHERE provider = ? AND date(created_at) = ?",
                    (provider, today),
                ) as cursor:
                    row = await cursor.fetchone()
                    return (row[0] or 0) if row else 0

    async def increment_usage(self, provider: str = "claude"):
        column = _USAGE_LOG_COLUMN.get(provider)
        if not column:
            return
        today = date.today().isoformat()
        async with self._connect() as db:
            await db.execute(
                f"""INSERT INTO usage_log (id, date, {column})
                   VALUES (?, ?, 1)
                   ON CONFLICT(date) DO UPDATE SET {column} = {column} + 1""",
                (_new_id(), today),
            )
            await db.commit()

    # ── Per-call usage accounting (usage_events) ──

    async def record_usage_event(
        self,
        *,
        agent: str = "",
        task: str = "",
        session_id: str = "",
        request_key: str = "",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        num_turns: int = 0,
        is_error: bool = False,
        source: str = "result_json",
        created_at: str | None = None,
        provider: str = "claude",
    ) -> bool:
        """Insert one usage row (one model within one brain call).

        Returns False when the row was skipped as a duplicate (request_key
        uniqueness makes transcript reconciliation idempotent).
        """
        if provider not in BRAIN_PROVIDERS:
            raise ValueError(f"unknown brain provider: {provider!r}")
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO usage_events
                   (id, created_at, agent, task, session_id, request_key, model,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, cost_usd, duration_ms, num_turns,
                    is_error, source, provider)
                   VALUES (?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _new_id(), created_at, agent, task, session_id, request_key,
                    model, int(input_tokens or 0), int(output_tokens or 0),
                    int(cache_read_tokens or 0), int(cache_creation_tokens or 0),
                    float(cost_usd or 0.0), int(duration_ms or 0),
                    int(num_turns or 0), 1 if is_error else 0, source, provider,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    _USAGE_SUM = (
        "COUNT(DISTINCT CASE WHEN session_id != '' THEN session_id ELSE id END) AS calls, "
        "SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, "
        "SUM(cache_read_tokens) AS cache_read_tokens, "
        "SUM(cache_creation_tokens) AS cache_creation_tokens, "
        "SUM(cost_usd) AS cost_usd"
    )

    @staticmethod
    def _usage_row_to_dict(row: aiosqlite.Row) -> dict:
        d = dict(row)
        for key, value in d.items():
            if value is None and key != "period":
                d[key] = 0
        if "cost_usd" in d:
            d["cost_usd"] = round(float(d["cost_usd"] or 0.0), 6)
        return d

    async def _usage_grouped(self, group_expr: str, alias: str, days: int) -> list[dict]:
        sql = (
            f"SELECT {group_expr} AS {alias}, {self._USAGE_SUM} "
            f"FROM usage_events "
            f"WHERE created_at >= datetime('now', ?) "
            f"GROUP BY {alias} ORDER BY cost_usd DESC"
        )
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, (f"-{int(days)} days",)) as cursor:
                return [self._usage_row_to_dict(r) for r in await cursor.fetchall()]

    async def usage_by_agent(self, days: int = 30) -> list[dict]:
        return await self._usage_grouped(
            "CASE WHEN agent = '' THEN 'other' ELSE agent END", "agent", days
        )

    async def usage_by_task(self, days: int = 30) -> list[dict]:
        return await self._usage_grouped(
            "CASE WHEN task = '' THEN 'other' ELSE task END", "task", days
        )

    async def usage_by_model(self, days: int = 30) -> list[dict]:
        return await self._usage_grouped(
            "CASE WHEN model = '' THEN 'unknown' ELSE model END", "model", days
        )

    async def usage_by_provider(self, days: int = 30) -> list[dict]:
        return await self._usage_grouped("provider", "provider", days)

    async def usage_by_day(self, days: int = 30) -> list[dict]:
        sql = (
            f"SELECT date(created_at) AS day, {self._USAGE_SUM} "
            f"FROM usage_events "
            f"WHERE created_at >= datetime('now', ?) "
            f"GROUP BY day ORDER BY day ASC"
        )
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, (f"-{int(days)} days",)) as cursor:
                return [self._usage_row_to_dict(r) for r in await cursor.fetchall()]

    async def usage_totals(self) -> dict:
        """Rollups for today / last 7 days / last 30 days (UTC)."""
        totals = {}
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            for label, where in (
                ("today", "date(created_at) = date('now')"),
                ("week", "created_at >= datetime('now', '-7 days')"),
                ("month", "created_at >= datetime('now', '-30 days')"),
            ):
                async with db.execute(
                    f"SELECT {self._USAGE_SUM} FROM usage_events WHERE {where}"
                ) as cursor:
                    row = await cursor.fetchone()
                    totals[label] = self._usage_row_to_dict(row) if row else {}
        return totals

    # ── Summary for Decision Making ──

    async def get_state_summary(self) -> dict:
        prospect_counts = await self.count_prospects_by_status()
        today = date.today().isoformat()
        async with self._connect() as db:
            async with db.execute(
                """SELECT
                     SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END)
                   FROM campaigns"""
            ) as cursor:
                row = await cursor.fetchone()
                draft_campaigns = row[0] or 0
                active_campaigns = row[1] or 0
            async with db.execute(
                "SELECT COUNT(*) FROM conversations WHERE status = 'open'"
            ) as cursor:
                open_conversations = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM conversations WHERE status = 'needs_human' AND intent = 'ooo'"
            ) as cursor:
                auto_reply_handoffs = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT claude_calls, codex_calls FROM usage_log WHERE date = ?", (today,)
            ) as cursor:
                usage_row = await cursor.fetchone()
                usage_today = (usage_row[0] or 0) if usage_row else 0
                codex_today = (usage_row[1] or 0) if usage_row else 0

        return {
            "prospects": prospect_counts,
            # The 'new' prospects the Writer can act on. A guessed or invalid
            # address stays 'new' forever, so deciding from the raw count
            # would keep choosing write_campaign with nothing to write.
            "writable_prospects": await self.count_writable_prospects(),
            # Unusable ones found in the last day: the heartbeat pauses
            # prospecting for the day when these alone fill the backlog.
            "recent_unusable_prospects": await self.count_recent_unusable_prospects(),
            "draft_campaigns": draft_campaigns,
            "active_campaigns": active_campaigns,
            # The kill switch: native sending stages nothing while it's set.
            "sending_paused": bool(await self.get_setting("sending_paused")),
            "open_conversations": open_conversations,
            "auto_reply_handoffs": auto_reply_handoffs,
            "usage_today": usage_today,  # Claude calls (kept for compatibility)
            "usage_today_by_provider": {"claude": usage_today, "codex": codex_today},
            # Companies discovered but not yet read. The heartbeat profiles
            # these every cycle — it is free, so it never competes with the
            # Claude budget the rest of the loop is rationing.
            "unprofiled": await self.count_companies_needing_profile(),
        }

    # ── Business Profiles & Lead Discovery ──

    @staticmethod
    def _normalize_business_profile_dict(d: dict) -> dict:
        json_fields = (
            "icp_titles",
            "target_criteria",
            "decision_maker_roles",
            "qualification_questions",
            "scoring_criteria",
            "products_services",
            "pain_points",
            "buying_signals",
            "messaging_rules",
        )
        for k in json_fields:
            raw = d.get(k)
            try:
                if raw is None or raw == "":
                    d[k] = [] if k in ("icp_titles", "decision_maker_roles", "qualification_questions", "products_services", "pain_points", "buying_signals") else {}
                else:
                    d[k] = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                d[k] = [] if k in ("icp_titles", "decision_maker_roles", "qualification_questions", "products_services", "pain_points", "buying_signals") else {}
        if not d.get("decision_maker_roles") and d.get("icp_titles"):
            d["decision_maker_roles"] = d["icp_titles"]
        if not d.get("scoring_criteria") and d.get("target_criteria"):
            d["scoring_criteria"] = d["target_criteria"]
        return d

    async def get_business_profiles(self) -> list[dict]:
        await self.seed_default_business_profiles()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM business_profiles ORDER BY is_default DESC, created_at ASC"
            ) as cursor:
                rows = await cursor.fetchall()
                out = []
                seen_names = set()
                for r in rows:
                    d = dict(r)
                    norm_name = (d.get("name") or "").strip().lower()
                    if norm_name and norm_name in seen_names:
                        continue
                    if norm_name:
                        seen_names.add(norm_name)
                    out.append(self._normalize_business_profile_dict(d))
                return out

    async def get_business_profile(self, profile_id: str) -> dict | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM business_profiles WHERE id = ?", (profile_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return self._normalize_business_profile_dict(dict(row))

    async def create_business_profile(self, profile_data: dict) -> dict:
        name = (profile_data.get("name") or "New Business Profile").strip()
        industry = profile_data.get("industry") or ""
        geography = profile_data.get("geography") or ""
        geography_scope = profile_data.get("geography_scope") or "worldwide"
        company_size = profile_data.get("company_size") or ""
        
        titles_val = profile_data.get("icp_titles") or profile_data.get("decision_maker_roles") or []
        icp_titles = json.dumps(titles_val if isinstance(titles_val, list) else [titles_val])
        
        product_name = profile_data.get("product_name") or ""
        product_description = profile_data.get("product_description") or ""
        
        crit_val = profile_data.get("target_criteria") or profile_data.get("scoring_criteria") or {}
        target_criteria = json.dumps(crit_val if isinstance(crit_val, dict) else {})
        is_default = 1 if profile_data.get("is_default") else 0

        subcategory = profile_data.get("subcategory") or ""
        business_type = profile_data.get("business_type") or ""
        target_customer_profile = profile_data.get("target_customer_profile") or ""
        revenue_range = profile_data.get("revenue_range") or ""
        
        dm_roles = profile_data.get("decision_maker_roles") or titles_val
        decision_maker_roles = json.dumps(dm_roles if isinstance(dm_roles, list) else [dm_roles])
        
        qual_q = profile_data.get("qualification_questions") or []
        qualification_questions = json.dumps(qual_q if isinstance(qual_q, list) else [qual_q])
        
        score_crit = profile_data.get("scoring_criteria") or crit_val
        scoring_criteria = json.dumps(score_crit if isinstance(score_crit, dict) else {})
        
        prod_serv = profile_data.get("products_services") or []
        products_services = json.dumps(prod_serv if isinstance(prod_serv, list) else [prod_serv])
        
        pains = profile_data.get("pain_points") or []
        pain_points = json.dumps(pains if isinstance(pains, list) else [pains])
        
        signals = profile_data.get("buying_signals") or []
        buying_signals = json.dumps(signals if isinstance(signals, list) else [signals])
        
        msg_rules = profile_data.get("messaging_rules") or {}
        messaging_rules = json.dumps(msg_rules if isinstance(msg_rules, dict) else {})

        async with self._connect() as db:
            pid = profile_data.get("id")
            if not pid:
                async with db.execute(
                    "SELECT id, is_default FROM business_profiles WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1",
                    (name,),
                ) as cursor:
                    existing = await cursor.fetchone()
                    if existing:
                        pid = existing[0]
                        if existing[1] == 1:
                            is_default = 1
                    else:
                        pid = _new_id()

            # Check if migration v24 columns are present in business_profiles
            async with db.execute("PRAGMA table_info(business_profiles)") as cursor:
                cols = {row[1] for row in await cursor.fetchall()}

            if "subcategory" in cols:
                await db.execute(
                    """INSERT INTO business_profiles
                       (id, name, industry, geography, geography_scope, company_size, icp_titles,
                        product_name, product_description, target_criteria, is_default,
                        subcategory, business_type, target_customer_profile, revenue_range,
                        decision_maker_roles, qualification_questions, scoring_criteria,
                        products_services, pain_points, buying_signals, messaging_rules)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        industry=excluded.industry,
                        geography=excluded.geography,
                        geography_scope=excluded.geography_scope,
                        company_size=excluded.company_size,
                        icp_titles=excluded.icp_titles,
                        product_name=excluded.product_name,
                        product_description=excluded.product_description,
                        target_criteria=excluded.target_criteria,
                        is_default=excluded.is_default,
                        subcategory=excluded.subcategory,
                        business_type=excluded.business_type,
                        target_customer_profile=excluded.target_customer_profile,
                        revenue_range=excluded.revenue_range,
                        decision_maker_roles=excluded.decision_maker_roles,
                        qualification_questions=excluded.qualification_questions,
                        scoring_criteria=excluded.scoring_criteria,
                        products_services=excluded.products_services,
                        pain_points=excluded.pain_points,
                        buying_signals=excluded.buying_signals,
                        messaging_rules=excluded.messaging_rules,
                        updated_at=CURRENT_TIMESTAMP""",
                    (pid, name, industry, geography, geography_scope, company_size, icp_titles,
                     product_name, product_description, target_criteria, is_default,
                     subcategory, business_type, target_customer_profile, revenue_range,
                     decision_maker_roles, qualification_questions, scoring_criteria,
                     products_services, pain_points, buying_signals, messaging_rules)
                )
            else:
                await db.execute(
                    """INSERT INTO business_profiles
                       (id, name, industry, geography, geography_scope, company_size, icp_titles,
                        product_name, product_description, target_criteria, is_default)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        industry=excluded.industry,
                        geography=excluded.geography,
                        geography_scope=excluded.geography_scope,
                        company_size=excluded.company_size,
                        icp_titles=excluded.icp_titles,
                        product_name=excluded.product_name,
                        product_description=excluded.product_description,
                        target_criteria=excluded.target_criteria,
                        is_default=excluded.is_default,
                        updated_at=CURRENT_TIMESTAMP""",
                    (pid, name, industry, geography, geography_scope, company_size, icp_titles,
                     product_name, product_description, target_criteria, is_default)
                )
            await db.commit()
        res = await self.get_business_profile(pid)
        return res or profile_data

    async def seed_default_business_profiles(self, config=None):
        """Seed universal multi-industry business profiles (16 core verticals + test profiles)."""
        profiles = [
            # 1. IT, Software, SaaS & AI (PRIMARY DEFAULT)
            {
                "id": "prof_saas_global",
                "name": "B2B SaaS & High-Growth Tech",
                "industry": "IT, Software, SaaS and AI",
                "subcategory": "Cloud Platforms & AI Automation",
                "business_type": "B2B SaaS / Enterprise Software",
                "target_customer_profile": "Mid-market & enterprise technology providers scaling sales pipelines",
                "company_size": "20-500 employees",
                "geography": "North America, Europe & Global Remote",
                "geography_scope": "worldwide",
                "revenue_range": "$2M - $50M ARR",
                "decision_maker_roles": ["VP Sales", "Head of Growth", "Founder", "CEO", "CTO", "Revenue Operations"],
                "icp_titles": ["VP Sales", "Head of Growth", "Founder", "CEO", "Revenue Operations"],
                "product_name": "Harvey AI",
                "product_description": "Autonomous AI sales development agent that grounds prospect outreach in verified account research",
                "qualification_questions": ["Are you actively scaling outbound pipeline?", "Do you have dedicated SDR/BDR teams?"],
                "scoring_criteria": {"min_score": 65, "require_decision_maker": True},
                "products_services": ["Autonomous Prospect Intelligence", "Outbound Pipeline Automation", "AI Sales Agent"],
                "pain_points": ["Manual prospecting time drain", "High SDR ramp overhead", "Low response on generic templates"],
                "buying_signals": ["Hiring SDRs/BDRs", "Recent venture capital round", "Adoption of modern sales tech stack"],
                "messaging_rules": {"tone": "consultative", "focus": "pipeline velocity and account research", "forbidden_terms": ["patient", "clinical", "clinic", "dental", "doctor", "dentist"]},
                "target_criteria": {"min_score": 65, "require_decision_maker": True},
                "is_default": 1,
            },
            # 2. Legal Services and Law Firms
            {
                "id": "prof_legal_services",
                "name": "Corporate Law & Legal Advisory",
                "industry": "Legal Services and Law Firms",
                "subcategory": "Commercial Advisory & Litigation",
                "business_type": "Law Firm / Legal Partnership",
                "target_customer_profile": "Corporate legal practices handling commercial transactions and regulatory matters",
                "company_size": "15-250 employees",
                "geography": "United States & United Kingdom",
                "geography_scope": "countries",
                "revenue_range": "$5M - $100M",
                "decision_maker_roles": ["Managing Partner", "Senior Partner", "Practice Group Leader", "Legal Director", "General Counsel"],
                "icp_titles": ["Managing Partner", "Partner", "Practice Group Leader", "Legal Director", "General Counsel"],
                "product_name": "Harvey AI",
                "product_description": "Intelligent client intake and consultation coordination agent with local confidentiality and operator review gates",
                "qualification_questions": ["Does the firm manage direct client consultation triage?", "Is there a designated partner overseeing intake?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Confidential Client Intake Automation", "Inquiry Triage & Partner Review Assistant"],
                "pain_points": ["Prospective client intake triage bottlenecks", "Consultation scheduling while ensuring strict confidentiality", "Managing non-billable administrative follow-up"],
                "buying_signals": ["Expanding regional office locations", "High-profile commercial matter verdicts", "Practice group lateral hires"],
                "messaging_rules": {"tone": "formal", "focus": "client confidentiality and partner review oversight", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 3. Healthcare, Hospitals, Clinics & Diagnostics
            {
                "id": "prof_specialty_clinics",
                "name": "Private Healthcare & Specialty Clinics",
                "industry": "Healthcare, Hospitals, Clinics and Diagnostics",
                "subcategory": "Outpatient Multi-Specialty Clinics",
                "business_type": "Private Healthcare Practice",
                "target_customer_profile": "Specialty medical centers, surgical suites, and diagnostic clinics",
                "company_size": "10-150 employees",
                "geography": "Tier 1 & Tier 2 Regional Metro Centers",
                "geography_scope": "regions",
                "revenue_range": "$2M - $25M",
                "decision_maker_roles": ["Clinic Director", "Medical Superintendent", "Practice Owner", "Clinical Administrator"],
                "icp_titles": ["Clinic Director", "Practice Owner", "Medical Superintendent", "Partner"],
                "product_name": "Harvey AI",
                "product_description": "Automated consultation coordination and patient intake triage software",
                "qualification_questions": ["Does the practice handle elective consultations?", "Is there an administrative manager for scheduling?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Consultation Intake Coordination", "Specialist Referral Follow-Up"],
                "pain_points": ["Patient consultation drop-off", "Front-desk triage phone congestion", "Specialist referral follow-up overhead"],
                "buying_signals": ["Opening additional surgical suites", "New diagnostic imaging equipment", "Expansion to multi-site practice"],
                "messaging_rules": {"tone": "professional", "focus": "consultation show rates and patient care continuity", "forbidden_terms": []},
                "target_criteria": {"min_score": 60},
                "is_default": 0,
            },
            # 4. Dental Clinics (TEST INDUSTRY — STRICTLY NOT DEFAULT)
            {
                "id": "prof_dental_in",
                "name": "Dental Clinics & Practices (India)",
                "industry": "Healthcare / Dental",
                "subcategory": "General & Cosmetic Dentistry",
                "business_type": "Private Dental Practice",
                "target_customer_profile": "Multi-chair dental clinics and oral surgery practices",
                "company_size": "10-200 employees",
                "geography": "Indore, Madhya Pradesh, India",
                "geography_scope": "cities",
                "revenue_range": "INR 50L - 10Cr",
                "decision_maker_roles": ["Principal Dentist", "Dental Surgeon", "Clinic Owner", "Practice Manager"],
                "icp_titles": ["Owner", "Dentist", "Dental Surgeon", "Founder", "Director", "Practice Manager"],
                "product_name": "Harvey AI",
                "product_description": "Autonomous B2B outreach assistant that drafts personalized introductory correspondence grounded in verified research",
                "qualification_questions": ["How many operatories/chairs are active?", "Do you handle elective cosmetic oral procedures?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Dental Consultation Triage", "Recall & Preventive Hygiene Follow-Up"],
                "pain_points": ["Front-desk phone overload", "No-shows on elective cosmetic consultations", "Manual recall chasing"],
                "buying_signals": ["Adding new operatories", "Investing in digital scanners/CBCT", "New associate dentists"],
                "messaging_rules": {"tone": "consultative", "focus": "chair utilization and low-friction appointment scheduling", "forbidden_terms": []},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 5. Real Estate and Property
            {
                "id": "prof_real_estate",
                "name": "Commercial Real Estate & Property Advisory",
                "industry": "Real Estate and Property",
                "subcategory": "Commercial Brokerage & Multi-Family Management",
                "business_type": "Real Estate Brokerage / Asset Management Firm",
                "target_customer_profile": "Commercial real estate brokerages, property management firms, and asset developers",
                "company_size": "10-150 employees",
                "geography": "North America, UK & UAE Metro Markets",
                "geography_scope": "worldwide",
                "revenue_range": "$3M - $50M Transaction Volume",
                "decision_maker_roles": ["Broker-Owner", "Managing Principal", "Director of Acquisitions", "VP Property Management", "Commercial Leasing Director"],
                "icp_titles": ["Broker-Owner", "Managing Principal", "Director of Acquisitions", "VP Property Management", "Commercial Leasing Director"],
                "product_name": "Harvey AI",
                "product_description": "Autonomous real estate inquiry qualification and property viewing coordination assistant",
                "qualification_questions": ["What is your square footage under management?", "Do you focus on commercial leasing, residential, or mixed-use?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Property Viewing Inquiry Qualification", "Listing Follow-up & Lease Renewal Assistant"],
                "pain_points": ["Slow response time to high-value buyer inquiries", "Lease renewal tracking lag", "Unqualified listing inquiries overwhelming agents"],
                "buying_signals": ["New commercial development permits", "Expanding portfolio under management", "Major leasing transactions"],
                "messaging_rules": {"tone": "consultative", "focus": "speed-to-lead for high-value inquiries and lease conversion", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 6. Manufacturing and Industrial
            {
                "id": "prof_manufacturing",
                "name": "Precision Manufacturing & Industrial OEM",
                "industry": "Manufacturing and Industrial",
                "subcategory": "Contract Manufacturing, CNC Machining & Industrial Equipment",
                "business_type": "Industrial OEM & Contract Manufacturer",
                "target_customer_profile": "Precision machining, industrial automation, and custom equipment manufacturers",
                "company_size": "25-300 employees",
                "geography": "North America, Germany & Global Industrial Corridors",
                "geography_scope": "worldwide",
                "revenue_range": "$10M - $100M",
                "decision_maker_roles": ["VP Operations", "Plant Manager", "Director of Procurement", "Managing Director", "VP Engineering"],
                "icp_titles": ["VP Operations", "Plant Manager", "Director of Procurement", "Managing Director", "VP Engineering"],
                "product_name": "Harvey AI",
                "product_description": "Industrial RFQ intake qualification and commercial supply agreement follow-up agent",
                "qualification_questions": ["What ISO/AS9100 certifications does the facility hold?", "Do you handle custom build-to-print or proprietary lines?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Industrial RFQ Intake & Estimate Tracking", "Supply Agreement Renewal Outreach"],
                "pain_points": ["Lengthy quotation cycles and unclosed RFQs", "Customer concentration risk", "Manual follow-up on engineering estimates"],
                "buying_signals": ["Facility expansions", "Acquisition of 5-axis CNC equipment", "ISO/AS9100 certification renewals"],
                "messaging_rules": {"tone": "direct", "focus": "quote conversion velocity and industrial supply agreement renewals", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 7. Finance, Accounting and Insurance
            {
                "id": "prof_accounting_finance",
                "name": "Accounting, Tax & Advisory Services",
                "industry": "Finance, Accounting and Insurance",
                "subcategory": "CPA Advisory, Corporate Tax & Commercial Risk Underwriting",
                "business_type": "Accounting & Financial Advisory Firm",
                "target_customer_profile": "Regional CPA partnerships, fractional CFO advisory, and commercial insurance brokerages",
                "company_size": "10-150 employees",
                "geography": "National Metro & Regional Commercial Centers",
                "geography_scope": "regions",
                "revenue_range": "$2M - $30M",
                "decision_maker_roles": ["Managing Partner", "CPA", "Tax Director", "Audit Partner", "Practice Leader", "Commercial Risk Director"],
                "icp_titles": ["Managing Partner", "CPA", "Audit Partner", "Tax Director", "Founder", "Practice Leader"],
                "product_name": "Harvey AI",
                "product_description": "Autonomous advisory correspondence assistant that automates client onboarding and reduces seasonal follow-up overhead",
                "qualification_questions": ["What accounting and tax software ecosystem does the firm utilize?", "Are you expanding outsourced advisory?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Client Advisory Intake Automation", "Seasonal Onboarding Document Chase Assistant"],
                "pain_points": ["Seasonal onboarding document collection bottlenecks", "Client churn during fee renegotiations", "Partner administrative scheduling overhead"],
                "buying_signals": ["Firm mergers & acquisitions", "Opening new advisory branches", "Adopting cloud ERP systems"],
                "messaging_rules": {"tone": "formal", "focus": "onboarding turnaround times and partner advisory leverage", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 8. Education and EdTech
            {
                "id": "prof_education_edtech",
                "name": "Executive Education & EdTech Programs",
                "industry": "Education and EdTech",
                "subcategory": "Higher Education, Executive Certification & Corporate Learning",
                "business_type": "Educational Institution / Enterprise EdTech Provider",
                "target_customer_profile": "Universities, executive learning institutes, and corporate training platforms",
                "company_size": "20-250 employees",
                "geography": "Global & Regional Educational Hubs",
                "geography_scope": "worldwide",
                "revenue_range": "$3M - $40M",
                "decision_maker_roles": ["Dean of Admissions", "Director of Enrollment", "VP Academic Partnerships", "Head of Corporate Training", "Provost"],
                "icp_titles": ["Dean of Admissions", "Director of Enrollment", "VP Academic Partnerships", "Head of Corporate Training", "Provost"],
                "product_name": "Harvey AI",
                "product_description": "Automated student inquiry qualification and corporate partnership outreach assistant",
                "qualification_questions": ["What learning management and SIS systems do you use?", "Do you offer executive or hybrid degree programs?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Prospective Student Inquiry Qualification", "Corporate Training Partnership Outreach"],
                "pain_points": ["Student drop-off between inquiry and application", "Corporate training RFP follow-up delays", "Manual enrollment counseling workload"],
                "buying_signals": ["Launching new online certification tracks", "Corporate workforce development initiatives", "Accreditation milestones"],
                "messaging_rules": {"tone": "consultative", "focus": "inquiry responsiveness and student enrollment yield", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 9. E-commerce and Retail
            {
                "id": "prof_ecommerce_retail",
                "name": "Omnichannel E-commerce & Wholesale Retail",
                "industry": "E-commerce and Retail",
                "subcategory": "Omnichannel Retail Brands & B2B Wholesale Distribution",
                "business_type": "Consumer Brand / Wholesale Merchandising Group",
                "target_customer_profile": "Fast-growing direct-to-consumer brands and wholesale retail distributors",
                "company_size": "15-200 employees",
                "geography": "National Distribution & International E-commerce Hubs",
                "geography_scope": "worldwide",
                "revenue_range": "$5M - $60M GMV",
                "decision_maker_roles": ["VP E-commerce", "Head of Merchandising", "Director of Wholesale Partnerships", "Founder", "Chief Commercial Officer"],
                "icp_titles": ["VP E-commerce", "Head of Merchandising", "Director of Wholesale Partnerships", "Founder", "Chief Commercial Officer"],
                "product_name": "Harvey AI",
                "product_description": "B2B wholesale buyer account acquisition and retail merchant partnership assistant",
                "qualification_questions": ["What e-commerce platform and WMS do you operate?", "Are you actively onboarding wholesale retail accounts?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["B2B Wholesale Account Acquisition", "Retail Buyer Partnership Outreach"],
                "pain_points": ["Difficulty opening new retail stockist accounts", "Wholesale order reorder drop-off", "Manual catalog distribution to buyers"],
                "buying_signals": ["New seasonal catalog launches", "Retail store shelf placements", "Expanded warehouse logistics"],
                "messaging_rules": {"tone": "consultative", "focus": "wholesale account expansion and buyer reorder velocity", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 10. Hospitality, Hotels and Restaurants
            {
                "id": "prof_hospitality_hotels",
                "name": "Boutique Hotels & Event Hospitality",
                "industry": "Hospitality, Hotels and Restaurants",
                "subcategory": "Boutique Hotels, Luxury Resorts & Restaurant Hospitality Groups",
                "business_type": "Hospitality Operator / Hotel & Event Management Group",
                "target_customer_profile": "Independent boutique hotel groups, corporate event resorts, and multi-concept restaurant operators",
                "company_size": "25-300 employees",
                "geography": "Metropolitan Tourist Hubs & Resort Destinations",
                "geography_scope": "worldwide",
                "revenue_range": "$5M - $50M",
                "decision_maker_roles": ["General Manager", "Director of Sales & Events", "VP Hospitality", "Corporate Food & Beverage Director", "Managing Principal"],
                "icp_titles": ["General Manager", "Director of Sales & Events", "VP Hospitality", "Corporate Food & Beverage Director", "Managing Principal"],
                "product_name": "Harvey AI",
                "product_description": "Corporate group event and private banquet inquiry qualification assistant",
                "qualification_questions": ["What is your property event and banquet room capacity?", "Do you host corporate offsites and conferences?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Corporate Group & Event Inquiry Qualification", "Private Dining & Banquet Follow-Up Assistant"],
                "pain_points": ["Delayed responses to corporate event and retreat RFPs", "Off-season corporate booking dips", "Event coordinator administrative overload"],
                "buying_signals": ["Property renovations", "Opening new banquet venues", "Hosting regional business conferences"],
                "messaging_rules": {"tone": "consultative", "focus": "event booking conversion speed and corporate retreat yield", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 11. Logistics and Transportation
            {
                "id": "prof_logistics_transport",
                "name": "Freight Logistics & Intermodal Haulage",
                "industry": "Logistics and Transportation",
                "subcategory": "Freight Brokerage, Third-Party Logistics (3PL) & Fleet Haulage",
                "business_type": "Logistics & Supply Chain Carrier / Brokerage",
                "target_customer_profile": "Asset and non-asset freight carriers, 3PL warehouses, and intermodal transport firms",
                "company_size": "20-250 employees",
                "geography": "National Freight Corridors & Global Shipping Hubs",
                "geography_scope": "worldwide",
                "revenue_range": "$8M - $100M",
                "decision_maker_roles": ["VP Logistics", "Director of Supply Chain", "Head of Freight Brokerage", "Managing Director", "Fleet Operations Director"],
                "icp_titles": ["VP Logistics", "Director of Supply Chain", "Head of Freight Brokerage", "Managing Director", "Fleet Operations Director"],
                "product_name": "Harvey AI",
                "product_description": "Shipper RFP outreach and freight quotation follow-up acceleration assistant",
                "qualification_questions": ["What TMS do you run?", "What are your primary lanes and equipment types?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Shipper Account Acquisition Agent", "Freight Quote & RFP Proposal Chase Assistant"],
                "pain_points": ["Slow spot-quote response allowing competing brokers to win freight", "Empty backhauls and lane imbalance", "High cost of manual shipper prospecting"],
                "buying_signals": ["Expanding fleet size", "New warehouse locations", "Awarded logistics broker licenses"],
                "messaging_rules": {"tone": "direct", "focus": "lane freight booking speed and shipper account expansion", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 12. Construction and Infrastructure
            {
                "id": "prof_construction_infra",
                "name": "Commercial Construction & Infrastructure",
                "industry": "Construction and Infrastructure",
                "subcategory": "Commercial General Contracting, Civil Works & Structural Engineering",
                "business_type": "General Contractor / Civil Engineering Builder",
                "target_customer_profile": "Commercial general contractors, civil engineering infrastructure firms, and specialty trade builders",
                "company_size": "25-350 employees",
                "geography": "Regional Metro Infrastructure & Commercial Corridors",
                "geography_scope": "regions",
                "revenue_range": "$15M - $150M",
                "decision_maker_roles": ["VP Construction", "Project Executive", "Director of Estimating", "Managing Principal", "Chief Estimator"],
                "icp_titles": ["VP Construction", "Project Executive", "Director of Estimating", "Managing Principal", "Chief Estimator"],
                "product_name": "Harvey AI",
                "product_description": "Subcontractor bid invitation follow-up and commercial owner project intake assistant",
                "qualification_questions": ["What project bonding capacity does the firm carry?", "Do you utilize Procore or Autodesk Construction Cloud?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Subcontractor Bid Invitation Follow-Up", "Commercial Owner Project Intake Assistant"],
                "pain_points": ["Low subcontractor bid coverage on major tenders", "Delayed follow-up on commercial owner negotiated bids", "Estimating team manual phone chasing burden"],
                "buying_signals": ["Municipal bond contract awards", "Permit filings for new commercial towers", "Joint venture announcements"],
                "messaging_rules": {"tone": "direct", "focus": "bid coverage and negotiated contract closing velocity", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 13. Automotive
            {
                "id": "prof_automotive",
                "name": "Automotive Dealerships & Fleet Operations",
                "industry": "Automotive",
                "subcategory": "Commercial Fleet Dealerships, Auto Parts OEM & Heavy Vehicle Maintenance",
                "business_type": "Automotive Group / Commercial Vehicle Dealership",
                "target_customer_profile": "Commercial fleet sales divisions, heavy equipment dealers, and tier-2 auto parts suppliers",
                "company_size": "20-200 employees",
                "geography": "Regional Transportation Hubs & Automotive Industrial Zones",
                "geography_scope": "regions",
                "revenue_range": "$10M - $80M",
                "decision_maker_roles": ["Commercial Fleet Director", "General Manager", "VP Automotive Sales", "Dealership Principal", "Parts Operations Director"],
                "icp_titles": ["Commercial Fleet Director", "General Manager", "VP Automotive Sales", "Dealership Principal", "Parts Operations Director"],
                "product_name": "Harvey AI",
                "product_description": "Commercial vehicle fleet quote follow-up and corporate account retention assistant",
                "qualification_questions": ["Do you handle municipal and commercial fleet leasing accounts?", "What DMS do you operate?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Commercial Fleet Quote Follow-Up", "Wholesale Parts & Fleet Service Agreement Outreach"],
                "pain_points": ["Fleet quote follow-up dropped during month-end sales rushes", "Corporate fleet replacement cycle tracking lag", "Wholesale parts accounts going dormant"],
                "buying_signals": ["Fleet EV inventory arrivals", "New commercial franchise agreements", "Expansion of commercial service bays"],
                "messaging_rules": {"tone": "consultative", "focus": "fleet delivery turnaround and commercial account retention", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 14. Agriculture
            {
                "id": "prof_agriculture",
                "name": "Agribusiness & Precision Agricultural Inputs",
                "industry": "Agriculture",
                "subcategory": "Agri-Inputs, Precision Agriculture, Grain Storage & Equipment Dealerships",
                "business_type": "Agribusiness Enterprise / Agricultural Supplier",
                "target_customer_profile": "Commercial farm management companies, precision agritech dealers, and grain wholesale cooperatives",
                "company_size": "15-150 employees",
                "geography": "Agricultural Belts & Regional Wholesale Corridors",
                "geography_scope": "regions",
                "revenue_range": "$5M - $70M",
                "decision_maker_roles": ["Agricultural Operations Director", "Farm General Manager", "VP Agribusiness", "Commercial Agronomist", "Managing Director"],
                "icp_titles": ["Agricultural Operations Director", "Farm General Manager", "VP Agribusiness", "Commercial Agronomist", "Managing Director"],
                "product_name": "Harvey AI",
                "product_description": "Commercial grower contract outreach and agri-input distributor acquisition assistant",
                "qualification_questions": ["What acreage or distribution territory does the company manage?", "Do you offer precision agronomy?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Commercial Grower Contract Outreach", "Agri-Input Distributor Acquisition Assistant"],
                "pain_points": ["Tight pre-season booking windows for inputs and equipment", "Dispersed rural client base difficult to contact by phone", "Grower contract renewals slipping to competitors"],
                "buying_signals": ["Pre-season planting announcements", "Expansion of grain elevator capacity", "Precision drone/sensor partnerships"],
                "messaging_rules": {"tone": "consultative", "focus": "seasonal contracting deadlines and grower partnership yield", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 15. Recruitment and HR
            {
                "id": "prof_recruitment_hr",
                "name": "Executive Search & Talent Acquisition",
                "industry": "Recruitment and HR",
                "subcategory": "Executive Search, Technical Staffing & Recruitment Process Outsourcing (RPO)",
                "business_type": "Talent Acquisition Agency / Executive Search Firm",
                "target_customer_profile": "Retained executive search firms, technical contract staffing agencies, and boutique recruitment consultancies",
                "company_size": "10-100 employees",
                "geography": "Major Financial & Tech Employment Hubs",
                "geography_scope": "worldwide",
                "revenue_range": "$2M - $25M",
                "decision_maker_roles": ["Managing Director", "VP Talent Acquisition", "Head of Search", "Practice Leader", "Agency Founder"],
                "icp_titles": ["Managing Director", "VP Talent Acquisition", "Head of Search", "Practice Leader", "Agency Founder"],
                "product_name": "Harvey AI",
                "product_description": "Hiring manager client acquisition and search mandate briefing coordinator",
                "qualification_questions": ["What recruiting specialization do you lead?", "Are searches conducted on a retained or contingent basis?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Hiring Manager Client Acquisition", "Search Mandate Intake & Briefing Coordinator"],
                "pain_points": ["Hiring manager ghosting on search mandate proposals", "Manual outbound BD eating into recruiter search delivery hours", "Job openings filled before agency contracts are signed"],
                "buying_signals": ["Target client companies posting high-volume openings", "New VP/C-suite appointments", "Series A/B funding announcements"],
                "messaging_rules": {"tone": "consultative", "focus": "time-to-fill and hiring manager relationship development", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 16. Marketing and Consulting Agencies
            {
                "id": "prof_marketing_consulting",
                "name": "Performance Marketing & Strategic Advisory",
                "industry": "Marketing and Consulting Agencies",
                "subcategory": "Digital Performance Agencies, Brand Strategy & Management Consulting",
                "business_type": "Strategic Marketing / Management Consulting Agency",
                "target_customer_profile": "Digital marketing agencies, B2B growth consultancies, and management advisory boutiques",
                "company_size": "10-120 employees",
                "geography": "Global Creative & Financial Metro Centers",
                "geography_scope": "worldwide",
                "revenue_range": "$2M - $30M",
                "decision_maker_roles": ["Agency Principal", "Managing Director", "VP Client Strategy", "Head of Business Development", "Partner"],
                "icp_titles": ["Agency Principal", "Managing Director", "VP Client Strategy", "Head of Business Development", "Partner"],
                "product_name": "Harvey AI",
                "product_description": "Agency pitch consultation scheduling and RFP proposal follow-up assistant",
                "qualification_questions": ["What is your typical monthly client retainer floor?", "Do you focus on performance marketing or brand strategy?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Agency Pitch Consultation Scheduling", "RFP Proposal Follow-Up Assistant"],
                "pain_points": ["Feast-or-famine agency new business pipeline", "High-value prospective clients stalling after initial proposal delivery", "Senior partners bogged down in cold prospecting"],
                "buying_signals": ["Target client CMO transitions", "Brand repositioning campaigns", "Expanding marketing budgets"],
                "messaging_rules": {"tone": "consultative", "focus": "new business pipeline consistency and pitch conversion", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 17. Professional and Business Services
            {
                "id": "prof_business_services",
                "name": "Corporate Facilities & Managed Business Services",
                "industry": "Professional and Business Services",
                "subcategory": "Managed IT Services (MSP), Facility Compliance & Commercial Security",
                "business_type": "B2B Managed Services Provider",
                "target_customer_profile": "Commercial facility services, managed service providers (MSPs), and corporate compliance advisory",
                "company_size": "15-180 employees",
                "geography": "Regional Commercial Corridors & Corporate Parks",
                "geography_scope": "regions",
                "revenue_range": "$3M - $40M",
                "decision_maker_roles": ["VP Commercial Services", "Director of Business Development", "General Manager", "COO", "Managing Director"],
                "icp_titles": ["VP Commercial Services", "Director of Business Development", "General Manager", "COO", "Managing Director"],
                "product_name": "Harvey AI",
                "product_description": "Commercial service agreement outreach and contract proposal renewal assistant",
                "qualification_questions": ["What is your service contract model (annual agreement, SLA)?", "What vertical industries represent your core accounts?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Commercial Service Agreement Outreach", "Contract Proposal & Renewal Follow-Up Assistant"],
                "pain_points": ["Multi-year service agreements lost due to slow proposal turnaround", "Manual prospecting across regional facility managers", "Dormant service accounts"],
                "buying_signals": ["Corporate office expansions", "New commercial tenant move-ins", "Cybersecurity compliance mandates"],
                "messaging_rules": {"tone": "consultative", "focus": "contract agreement value and operational reliability", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
            # 18. Commercial HVAC & Facility Services (Home Services)
            {
                "id": "prof_home_services",
                "name": "Commercial HVAC & Facility Services",
                "industry": "Commercial & Home Services",
                "subcategory": "Commercial Mechanical & Environmental Controls",
                "business_type": "Commercial Contracting & MEP",
                "target_customer_profile": "Facility maintenance, mechanical contractors, and commercial HVAC teams",
                "company_size": "20-200 employees",
                "geography": "Regional Commercial Service Areas",
                "geography_scope": "regions",
                "revenue_range": "$5M - $40M",
                "decision_maker_roles": ["Owner", "VP Operations", "Commercial Service Director", "General Manager", "Estimating Director"],
                "icp_titles": ["Owner", "VP Operations", "Commercial Service Director", "General Manager", "Estimating Director"],
                "product_name": "Harvey AI",
                "product_description": "Fast quote follow-up and commercial service agreement outreach assistant for facility and contracting teams",
                "qualification_questions": ["Do you provide 24/7 commercial service?", "What is your typical maintenance agreement term?"],
                "scoring_criteria": {"min_score": 60, "require_decision_maker": True},
                "products_services": ["Quotation Follow-Up Automation", "Maintenance Renewal Assistant"],
                "pain_points": ["Commercial contractors losing bids due to slow follow-up", "Preventative maintenance renewals slipping", "Manual estimating tracking burden"],
                "buying_signals": ["Commercial building acquisitions", "Chiller plant retrofit permits", "Industrial park developments"],
                "messaging_rules": {"tone": "direct", "focus": "estimate closing rates and preventative maintenance renewals", "forbidden_terms": ["patient", "clinical", "clinic", "dental"]},
                "target_criteria": {"min_score": 60, "require_decision_maker": True},
                "is_default": 0,
            },
        ]
        for p in profiles:
            existing = await self.get_business_profile(p["id"])
            if not existing or not existing.get("subcategory"):
                await self.create_business_profile(p)
            elif p.get("is_default") == 1 and not existing.get("is_default"):
                # Ensure primary default is updated
                await self.create_business_profile(p)

    async def delete_business_profile(self, profile_id: str) -> bool:
        """Delete a business profile by ID. Prevents deleting the default profile."""
        async with self._connect() as db:
            async with db.execute("SELECT is_default FROM business_profiles WHERE id = ?", (profile_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False
                if row[0] == 1:
                    raise ValueError("Cannot delete the default business profile.")
            await db.execute("DELETE FROM business_profiles WHERE id = ?", (profile_id,))
            await db.commit()
            return True

    async def save_discovered_lead(self, lead_data: dict) -> str:
        lid = lead_data.get("id")
        profile_id = lead_data.get("profile_id") or ""
        company_name = lead_data.get("company_name") or ""
        domain = _norm(lead_data.get("domain") or "")
        website = lead_data.get("website") or ""
        industry = lead_data.get("industry") or ""
        location = lead_data.get("location") or ""
        city = lead_data.get("city") or ""
        state_region = lead_data.get("state_region") or ""
        country = lead_data.get("country") or ""
        if location and (not city or not country):
            c, s, co = parse_location_components(location)
            city = city or c
            state_region = state_region or s
            country = country or co
        company_size = lead_data.get("company_size") or ""
        description = lead_data.get("description") or ""
        decision_maker_json = json.dumps(lead_data.get("decision_maker") or {})
        potential_needs_json = json.dumps(lead_data.get("potential_needs") or [])
        research_sources_json = json.dumps(lead_data.get("research_sources") or lead_data.get("sources") or [])
        missing_fields_json = json.dumps(lead_data.get("missing_fields") or [])
        score = int(lead_data.get("score") or 0)
        tier = lead_data.get("tier") or "unscored"
        reasons_json = json.dumps(lead_data.get("reasons") or [])
        confidence = float(lead_data.get("confidence") or 0.0)
        is_qualified = 1 if lead_data.get("is_qualified") else 0
        is_sample = 1 if lead_data.get("is_sample", True) else 0
        bant_breakdown_json = json.dumps(lead_data.get("bant_breakdown") or {})

        async with self._connect() as db:
            # Domain deduplication: reuse existing lead record if domain matches
            if not lid and domain:
                query = "SELECT id FROM discovered_leads WHERE LOWER(domain) = ?"
                params: tuple = (domain,)
                if profile_id:
                    query += " AND profile_id = ?"
                    params = (domain, profile_id)
                async with db.execute(query, params) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        lid = row[0]

            if not lid:
                lid = _new_id()

            await db.execute(
                """INSERT INTO discovered_leads
                   (id, profile_id, company_name, domain, website, industry, location,
                    city, state_region, country,
                    company_size, description, decision_maker_json, potential_needs_json,
                    research_sources_json, missing_fields_json, score, tier,
                    reasons_json, confidence, is_qualified, is_sample, bant_breakdown_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    company_name=excluded.company_name,
                    domain=excluded.domain,
                    website=excluded.website,
                    industry=excluded.industry,
                    location=excluded.location,
                    city=excluded.city,
                    state_region=excluded.state_region,
                    country=excluded.country,
                    company_size=excluded.company_size,
                    description=excluded.description,
                    decision_maker_json=excluded.decision_maker_json,
                    potential_needs_json=excluded.potential_needs_json,
                    research_sources_json=excluded.research_sources_json,
                    missing_fields_json=excluded.missing_fields_json,
                    score=excluded.score,
                    tier=excluded.tier,
                    reasons_json=excluded.reasons_json,
                    confidence=excluded.confidence,
                    is_qualified=excluded.is_qualified,
                    bant_breakdown_json=excluded.bant_breakdown_json,
                    updated_at=CURRENT_TIMESTAMP""",
                (lid, profile_id, company_name, domain, website, industry, location,
                 city, state_region, country,
                 company_size, description, decision_maker_json, potential_needs_json,
                 research_sources_json, missing_fields_json, score, tier,
                 reasons_json, confidence, is_qualified, is_sample, bant_breakdown_json)
            )
            await db.commit()
        return lid

    async def get_discovered_leads(self, profile_id: str | None = None) -> list[dict]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM discovered_leads"
            params: tuple = ()
            if profile_id:
                query += " WHERE profile_id = ?"
                params = (profile_id,)
            query += " ORDER BY score DESC, created_at DESC"
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                out = []
                seen_domains = set()
                for r in rows:
                    d = dict(r)
                    dom = _norm(d.get("domain") or "")
                    if dom:
                        if dom in seen_domains:
                            continue
                        seen_domains.add(dom)
                    for k in ("decision_maker_json", "potential_needs_json", "research_sources_json",
                              "missing_fields_json", "reasons_json", "bant_breakdown_json"):
                        raw = d.get(k)
                        clean_key = k[:-5] if k.endswith("_json") else k
                        try:
                            d[clean_key] = json.loads(raw) if raw else ([] if k not in ("decision_maker_json", "bant_breakdown_json") else {})
                        except (json.JSONDecodeError, TypeError):
                            d[clean_key] = [] if k not in ("decision_maker_json", "bant_breakdown_json") else {}
                    out.append(d)
                return out

    async def get_discovered_lead(self, lead_id: str) -> dict | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM discovered_leads WHERE id = ?", (lead_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                d = dict(row)
                for k in ("decision_maker_json", "potential_needs_json", "research_sources_json",
                          "missing_fields_json", "reasons_json", "bant_breakdown_json"):
                    raw = d.get(k)
                    clean_key = k[:-5] if k.endswith("_json") else k
                    try:
                        d[clean_key] = json.loads(raw) if raw else ([] if k not in ("decision_maker_json", "bant_breakdown_json") else {})
                    except (json.JSONDecodeError, TypeError):
                        d[clean_key] = [] if k not in ("decision_maker_json", "bant_breakdown_json") else {}
                return d

    async def add_discovered_lead_to_pipeline(self, lead_id: str) -> dict:
        lead = await self.get_discovered_lead(lead_id)
        if not lead:
            raise ValueError(f"Discovered lead not found: {lead_id}")

        domain = _norm(lead.get("domain") or "")
        dm = lead.get("decision_maker") or {}
        email = _norm(dm.get("email") or "")
        company_name = lead.get("company_name") or "Unknown Company"

        # Check existing company
        existing_company = await self.get_company_by_domain(domain) if domain else None
        if not existing_company:
            # Check by name
            async with self._connect() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (company_name,)
                ) as c:
                    r = await c.fetchone()
                    if r:
                        existing_company = self._company_from_row(r)

        created_company = False
        if existing_company:
            company_id = existing_company.id
        else:
            comp = Company(
                name=company_name,
                domain=domain,
                website=lead.get("website") or (f"https://{domain}" if domain else ""),
                description=lead.get("description") or "",
                industry=lead.get("industry") or "",
                company_size=lead.get("company_size") or "",
                location=lead.get("location") or "",
                source=f"discovery:{lead.get('profile_id', 'sample')}",
            )
            company_id = await self.add_company(comp)
            created_company = True

        # Check existing prospect
        existing_prospect = await self.get_prospect_by_email(email) if email else None
        created_prospect = False
        prospect_id = ""

        if existing_prospect:
            prospect_id = existing_prospect.id
        elif dm.get("name") or email:
            full_name = dm.get("name") or ""
            parts = full_name.split(None, 1)
            first_name = parts[0] if parts else ""
            last_name = parts[1] if len(parts) > 1 else ""

            prosp = Prospect(
                company_id=company_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                email_status=dm.get("email_status") or "verified",
                email_verified=bool(dm.get("email_status") == "verified"),
                title=dm.get("title") or "Decision Maker",
                linkedin_url=dm.get("linkedin_url") or "",
                company=company_name,
                industry=lead.get("industry") or "",
                company_size=lead.get("company_size") or "",
                source=f"discovery:{lead.get('profile_id', 'sample')}",
                status="new",
                score=lead.get("score") or 0,
            )
            prospect_id = await self.add_prospect(prosp)
            created_prospect = True

        # Mark lead added to CRM
        async with self._connect() as db:
            await db.execute("UPDATE discovered_leads SET added_to_crm = 1 WHERE id = ?", (lead_id,))
            await db.commit()

        await self.log_action("lead_added_to_crm", lead_id, {
            "company_id": company_id,
            "prospect_id": prospect_id,
            "created_company": created_company,
            "created_prospect": created_prospect,
        })

        return {
            "lead_id": lead_id,
            "company_id": company_id,
            "prospect_id": prospect_id,
            "created_company": created_company,
            "created_prospect": created_prospect,
            "already_existed": not (created_company or created_prospect),
        }



    # ── Lead Research & Company Enrichment (v17) ──

    @staticmethod
    def _parse_research_row(row: Any) -> dict:
        d = dict(row)
        json_fields = (
            ("products_services_json", "products_services", []),
            ("potential_needs_json", "potential_needs", []),
            ("verified_facts_json", "verified_facts", []),
            ("inferred_observations_json", "inferred_observations", []),
            ("unknown_fields_json", "unknown_fields", []),
            ("sources_json", "sources", []),
            ("fit_reasons_json", "fit_reasons", []),
            ("potential_use_cases_json", "potential_use_cases", []),
        )
        for col, clean_k, default in json_fields:
            raw = d.get(col)
            try:
                d[clean_k] = json.loads(raw) if raw else default
            except (json.JSONDecodeError, TypeError):
                d[clean_k] = default
        return d

    async def get_lead_research(self, prospect_id: str) -> dict | None:
        """Get latest research & enrichment record for a prospect."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM lead_research WHERE prospect_id = ? ORDER BY researched_at DESC LIMIT 1",
                (prospect_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return self._parse_research_row(row) if row else None

    async def get_lead_research_by_domain(self, domain: str) -> dict | None:
        """Get latest research & enrichment record for a domain (for deduplication / freshness)."""
        domain_norm = (domain or "").strip().lower()
        if not domain_norm:
            return None
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM lead_research WHERE domain = ? ORDER BY researched_at DESC LIMIT 1",
                (domain_norm,),
            ) as cursor:
                row = await cursor.fetchone()
                return self._parse_research_row(row) if row else None

    async def save_lead_research(self, record: dict) -> str:
        """Insert or update a lead research & enrichment record."""
        import uuid
        rid = record.get("id") or f"res_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        products_json = json.dumps(record.get("products_services") or [])
        needs_json = json.dumps(record.get("potential_needs") or [])
        facts_json = json.dumps(record.get("verified_facts") or [])
        inferred_json = json.dumps(record.get("inferred_observations") or [])
        unknown_json = json.dumps(record.get("unknown_fields") or [])
        sources_json = json.dumps(record.get("sources") or [])
        fit_reasons_json = json.dumps(record.get("fit_reasons") or [])
        use_cases_json = json.dumps(record.get("potential_use_cases") or [])

        async with self._connect() as db:
            await db.execute(
                """INSERT INTO lead_research (
                    id, prospect_id, company_id, company_name, domain, website,
                    industry, business_category, company_overview, products_services_json,
                    target_customers, potential_needs_json, verified_facts_json,
                    inferred_observations_json, unknown_fields_json, sources_json,
                    fit_score, fit_tier, fit_reasons_json, potential_use_cases_json,
                    recommended_action, confidence, is_sample, researched_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    company_name = excluded.company_name,
                    domain = excluded.domain,
                    website = excluded.website,
                    industry = excluded.industry,
                    business_category = excluded.business_category,
                    company_overview = excluded.company_overview,
                    products_services_json = excluded.products_services_json,
                    target_customers = excluded.target_customers,
                    potential_needs_json = excluded.potential_needs_json,
                    verified_facts_json = excluded.verified_facts_json,
                    inferred_observations_json = excluded.inferred_observations_json,
                    unknown_fields_json = excluded.unknown_fields_json,
                    sources_json = excluded.sources_json,
                    fit_score = excluded.fit_score,
                    fit_tier = excluded.fit_tier,
                    fit_reasons_json = excluded.fit_reasons_json,
                    potential_use_cases_json = excluded.potential_use_cases_json,
                    recommended_action = excluded.recommended_action,
                    confidence = excluded.confidence,
                    is_sample = excluded.is_sample,
                    researched_at = excluded.researched_at,
                    updated_at = excluded.updated_at
                """,
                (
                    rid,
                    record.get("prospect_id", ""),
                    record.get("company_id", ""),
                    record.get("company_name", "Unknown Company"),
                    (record.get("domain", "") or "").strip().lower(),
                    record.get("website", ""),
                    record.get("industry", "Unknown"),
                    record.get("business_category", "Unknown"),
                    record.get("company_overview", ""),
                    products_json,
                    record.get("target_customers", "Unknown"),
                    needs_json,
                    facts_json,
                    inferred_json,
                    unknown_json,
                    sources_json,
                    int(record.get("fit_score", 0)),
                    record.get("fit_tier", "Unscored"),
                    fit_reasons_json,
                    use_cases_json,
                    record.get("recommended_action", ""),
                    float(record.get("confidence", 0.0)),
                    int(bool(record.get("is_sample", False))),
                    record.get("researched_at", now),
                    now,
                ),
            )
            await db.commit()
        return rid

    async def get_all_lead_research(self, limit: int = 50) -> list[dict]:
        """Get recent research & enrichment records."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM lead_research ORDER BY researched_at DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._parse_research_row(r) for r in rows]


    # ── Lead Discovery Review & CRM Pipeline Automation (v18) ──

    async def update_prospect_pipeline(
        self,
        prospect_id: str,
        stage: str | None = None,
        notes: str | None = None,
        next_action: str | None = None,
        follow_up_date: str | None = None,
    ) -> dict:
        """Update CRM pipeline stage, notes, next action, and follow-up date."""
        if stage and stage not in CRM_PIPELINE_STAGES:
            raise ValueError(f"Invalid pipeline stage '{stage}'. Must be one of: {CRM_PIPELINE_STAGES}")

        now = _utcnow().isoformat()
        fields = []
        values = []

        if stage is not None:
            fields.append("pipeline_stage = ?")
            values.append(stage)
            # Sync status if applicable
            if stage == "Contacted":
                fields.append("status = CASE WHEN status = 'new' THEN 'contacted' ELSE status END")
            elif stage == "Interested":
                fields.append("status = 'interested'")
            elif stage == "Meeting":
                fields.append("status = 'meeting'")
            elif stage == "Lost":
                fields.append("status = 'closed'")

        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)
        if next_action is not None:
            fields.append("next_action = ?")
            values.append(next_action)
        if follow_up_date is not None:
            fields.append("follow_up_date = ?")
            values.append(follow_up_date)

        fields.append("updated_at = ?")
        values.append(now)
        values.append(prospect_id)

        sql = f"UPDATE prospects SET {', '.join(fields)} WHERE id = ?"

        async with self._connect() as db:
            await db.execute(sql, tuple(values))
            await db.commit()

        # Retrieve updated prospect
        prospect = await self.get_prospect(prospect_id)
        if not prospect:
            raise ValueError(f"Prospect {prospect_id} not found")

        await self.log_action("pipeline_stage_changed" if stage else "pipeline_lead_updated", prospect_id, {
            "prospect_id": prospect_id,
            "pipeline_stage": prospect.pipeline_stage,
            "notes": prospect.notes,
            "next_action": prospect.next_action,
            "follow_up_date": prospect.follow_up_date,
        })

        return prospect.model_dump() if hasattr(prospect, "model_dump") else prospect.dict()

    async def update_prospect_pipeline_stage(
        self, prospect_id: str, stage: str, notes: str | None = None
    ) -> dict:
        """Alias for update_prospect_pipeline, mapping common labels like 'Meeting Booked' to 'Meeting'."""
        target = "Meeting" if stage == "Meeting Booked" else stage
        return await self.update_prospect_pipeline(prospect_id, stage=target, notes=notes)

    async def get_sent_count(self) -> int:
        """Alias for count_outbox_sent."""
        return await self.count_outbox_sent()

    async def approve_outbox_item(self, item_id: str | None = None) -> int:
        """Alias for approve_outbox."""
        return await self.approve_outbox(item_id)

    async def get_pipeline_leads(
        self,
        stage: str | None = None,
        search: str | None = None,
        sort_by: str = "score",
    ) -> dict:
        """Return leads in CRM pipeline with company details, stage breakdown, and search."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            sql = """
                SELECT p.*,
                       c.name AS company_name,
                       c.domain AS company_domain,
                       c.website AS company_website,
                       c.location AS company_location,
                       c.industry AS company_industry,
                       c.company_size AS company_size_val
                FROM prospects p
                LEFT JOIN companies c ON c.id = p.company_id
                ORDER BY p.score DESC, p.created_at DESC
            """
            async with db.execute(sql) as cursor:
                rows = await cursor.fetchall()

        all_leads = []
        stage_counts = {s: 0 for s in CRM_PIPELINE_STAGES}

        for r in rows:
            d = dict(r)
            pstg = d.get("pipeline_stage") or "New"
            if pstg not in stage_counts:
                pstg = "New"
            d["pipeline_stage"] = pstg
            stage_counts[pstg] += 1
            all_leads.append(d)

        filtered = all_leads

        if stage and stage != "all":
            filtered = [l for l in filtered if l.get("pipeline_stage") == stage]

        if search and search.strip():
            q = search.strip().lower()
            filtered = [
                l for l in filtered
                if q in (l.get("first_name", "") + " " + l.get("last_name", "")).lower()
                or q in (l.get("company_name") or l.get("company") or "").lower()
                or q in (l.get("email") or "").lower()
                or q in (l.get("title") or "").lower()
                or q in (l.get("notes") or "").lower()
            ]

        if sort_by == "score":
            filtered.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
        elif sort_by == "name":
            filtered.sort(key=lambda x: (x.get("first_name") or "").lower())
        elif sort_by == "updated_at":
            filtered.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
        elif sort_by == "follow_up":
            filtered.sort(key=lambda x: str(x.get("follow_up_date") or "9999"))

        return {
            "total": len(filtered),
            "stages": stage_counts,
            "leads": filtered,
        }

    async def check_lead_duplicates(
        self,
        domain: str | None = None,
        email: str | None = None,
        company_name: str | None = None,
    ) -> dict:
        """Check if a company or prospect already exists in CRM."""
        matches = []
        is_dup = False
        reasons = []

        norm_domain = (domain or "").strip().lower()
        norm_email = (email or "").strip().lower()
        norm_name = (company_name or "").strip().lower()

        if norm_domain:
            comp = await self.get_company_by_domain(norm_domain)
            if comp:
                is_dup = True
                matches.append({
                    "type": "domain",
                    "entity": "company",
                    "id": comp.id,
                    "name": comp.name,
                    "detail": comp.domain,
                })
                reasons.append(f"Company already in CRM (Domain: {comp.domain})")

        if norm_email:
            prosp = await self.get_prospect_by_email(norm_email)
            if prosp:
                is_dup = True
                matches.append({
                    "type": "email",
                    "entity": "prospect",
                    "id": prosp.id,
                    "name": prosp.full_name(),
                    "detail": prosp.email,
                })
                reasons.append(f"Prospect already in CRM (Email: {prosp.email})")

        if norm_name and not is_dup:
            async with self._connect() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, name, domain FROM companies WHERE LOWER(name) = ?",
                    (norm_name,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        is_dup = True
                        matches.append({
                            "type": "company_name",
                            "entity": "company",
                            "id": row["id"],
                            "name": row["name"],
                            "detail": row["domain"],
                        })
                        reasons.append(f"Company with same name already in CRM ({row['name']})")

        return {
            "is_duplicate": is_dup,
            "matches": matches,
            "primary_reason": reasons[0] if reasons else "",
            "all_reasons": reasons,
        }

    async def update_discovered_lead_status(
        self,
        lead_id: str,
        status: str,
        rejection_reason: str = "",
    ) -> bool:
        """Update candidate review status ('pending', 'approved', 'rejected', 'skipped')."""
        valid_statuses = {"pending", "approved", "rejected", "skipped"}
        if status not in valid_statuses:
            raise ValueError(f"Invalid candidate status '{status}'. Must be one of {valid_statuses}")

        async with self._connect() as db:
            await db.execute(
                """UPDATE discovered_leads
                   SET candidate_status = ?, rejection_reason = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (status, rejection_reason, lead_id),
            )
            await db.commit()
        return True

    async def update_lead_qualification_override(
        self,
        lead_id: str,
        is_qualified: bool,
        reason: str = "",
    ) -> bool:
        """Manually override lead qualification status (qualified vs disqualified)."""
        candidate_status = "approved" if is_qualified else "rejected"
        tier = "High Fit" if is_qualified else "Low Fit"
        async with self._connect() as db:
            await db.execute(
                """UPDATE discovered_leads
                   SET is_qualified = ?, candidate_status = ?, tier = ?, rejection_reason = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (1 if is_qualified else 0, candidate_status, tier, reason, lead_id),
            )
            await db.commit()
        await self.log_action(
            action_type="lead_manually_qualified" if is_qualified else "lead_manually_disqualified",
            agent="user",
            details={"lead_id": lead_id, "is_qualified": is_qualified, "reason": reason},
        )
        return True

    async def import_discovered_lead_to_crm(
        self,
        lead_id: str,
        target_stage: str = "New",
        notes: str = "",
    ) -> dict:
        """Review & import an approved candidate lead into the CRM pipeline."""
        lead = await self.get_discovered_lead(lead_id)
        if not lead:
            raise ValueError(f"Discovered lead not found: {lead_id}")

        domain = _norm(lead.get("domain") or "")
        dm = lead.get("decision_maker") or {}
        email = _norm(dm.get("email") or "")
        company_name = lead.get("company_name") or "Unknown Company"

        # Check existing company
        existing_company = await self.get_company_by_domain(domain) if domain else None
        if not existing_company:
            async with self._connect() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (company_name,)
                ) as c:
                    r = await c.fetchone()
                    if r:
                        existing_company = self._company_from_row(r)

        created_company = False
        if existing_company:
            company_id = existing_company.id
        else:
            comp = Company(
                name=company_name,
                domain=domain,
                website=lead.get("website") or (f"https://{domain}" if domain else ""),
                description=lead.get("description") or "",
                industry=lead.get("industry") or "",
                company_size=lead.get("company_size") or "",
                location=lead.get("location") or "",
                source=f"discovery:{lead.get('profile_id', 'sample')}",
            )
            company_id = await self.add_company(comp)
            created_company = True

        # Check existing prospect
        existing_prospect = await self.get_prospect_by_email(email) if email else None
        created_prospect = False
        prospect_id = ""

        if existing_prospect:
            prospect_id = existing_prospect.id
            # Update stage if provided
            if target_stage:
                await self.update_prospect_pipeline(prospect_id, stage=target_stage, notes=notes or None)
        elif dm.get("name") or email:
            full_name = dm.get("name") or ""
            parts = full_name.split(None, 1)
            first_name = parts[0] if parts else ""
            last_name = parts[1] if len(parts) > 1 else ""

            prosp = Prospect(
                company_id=company_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                email_status=dm.get("email_status") or "verified",
                email_verified=bool(dm.get("email_status") == "verified"),
                title=dm.get("title") or "Decision Maker",
                linkedin_url=dm.get("linkedin_url") or "",
                company=company_name,
                industry=lead.get("industry") or "",
                company_size=lead.get("company_size") or "",
                source=f"discovery:{lead.get('profile_id', 'sample')}",
                status="new",
                score=int(lead.get("score") or 0),
                pipeline_stage=target_stage or "New",
                notes=notes or "",
            )
            prospect_id = await self.add_prospect(prosp)
            created_prospect = True

        # Mark candidate as approved and in CRM
        async with self._connect() as db:
            await db.execute(
                """UPDATE discovered_leads
                   SET added_to_crm = 1, candidate_status = 'approved', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (lead_id,),
            )
            await db.commit()

        # Seed research record in lead_research if not present so research is immediately accessible
        if prospect_id:
            existing_research = await self.get_lead_research(prospect_id)
            if not existing_research:
                research_rec = {
                    "prospect_id": prospect_id,
                    "company_id": company_id,
                    "company_name": company_name,
                    "domain": domain,
                    "website": lead.get("website") or (f"https://{domain}" if domain else ""),
                    "industry": lead.get("industry") or "Unknown",
                    "business_category": lead.get("business_type") or lead.get("industry") or "Unknown",
                    "company_overview": lead.get("description") or "",
                    "products_services": lead.get("target_use_cases") or [],
                    "target_customers": lead.get("industry") or "Unknown",
                    "potential_needs": lead.get("potential_needs") or [],
                    "verified_facts": [
                        f"Company Name: {company_name}",
                        f"Domain: {domain}" if domain else "Domain unverified",
                        f"Contact: {dm.get('name', 'Unknown')} ({dm.get('title', 'Unknown')})",
                        f"Email: {email}" if email else "Email unverified",
                    ],
                    "inferred_observations": lead.get("reasons") or [],
                    "unknown_fields": lead.get("missing_fields") or [],
                    "sources": lead.get("sources") or [],
                    "fit_score": int(lead.get("score") or 0),
                    "fit_tier": lead.get("tier") or "Unscored",
                    "fit_reasons": lead.get("reasons") or [],
                    "potential_use_cases": lead.get("potential_needs") or [],
                    "recommended_action": f"Begin discovery sequence targeting {dm.get('title', 'Decision Maker')}",
                    "confidence": float(lead.get("confidence") or 0.8),
                    "is_sample": bool(lead.get("is_sample", True)),
                }
                await self.save_lead_research(research_rec)

        await self.log_action("lead_imported_to_crm", lead_id, {
            "lead_id": lead_id,
            "company_id": company_id,
            "prospect_id": prospect_id,
            "pipeline_stage": target_stage,
            "created_company": created_company,
            "created_prospect": created_prospect,
        })

        return {
            "lead_id": lead_id,
            "company_id": company_id,
            "prospect_id": prospect_id,
            "pipeline_stage": target_stage,
            "created_company": created_company,
            "created_prospect": created_prospect,
            "already_existed": not (created_company or created_prospect),
        }

    async def batch_import_discovered_leads(
        self,
        lead_ids: list[str],
        target_stage: str = "New",
    ) -> dict:
        """Batch import multiple approved candidates into CRM."""
        results = []
        imported_count = 0
        for lid in lead_ids:
            try:
                res = await self.import_discovered_lead_to_crm(lid, target_stage=target_stage)
                results.append(res)
                imported_count += 1
            except Exception as e:
                results.append({"lead_id": lid, "error": str(e)})

        return {
            "success": True,
            "imported_count": imported_count,
            "total_requested": len(lead_ids),
            "results": results,
        }

    async def batch_update_discovered_leads_status(
        self,
        lead_ids: list[str],
        status: str,
        reason: str = "",
    ) -> dict:
        """Batch update review status for candidate leads."""
        updated = 0
        for lid in lead_ids:
            try:
                ok = await self.update_discovered_lead_status(lid, status, rejection_reason=reason)
                if ok:
                    updated += 1
            except Exception:
                pass
        return {"success": True, "updated_count": updated, "status": status}


    # ── AI-Powered Personalized Outreach Drafts (v20) ──

    @staticmethod
    def _parse_outreach_draft_row(row: Any) -> dict:
        d = dict(row)
        for col, clean_k in (
            ("research_sources_json", "research_sources"),
            ("verified_facts_json", "verified_facts"),
            ("missing_fields_json", "missing_fields"),
        ):
            raw = d.get(col)
            try:
                d[clean_k] = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                d[clean_k] = []
        return d

    async def save_outreach_draft(self, draft: dict[str, Any]) -> str:
        """Create or update an outreach draft."""
        draft_id = draft.get("id") or f"draft_{uuid.uuid4().hex[:12]}"
        now = _utcnow().isoformat()

        sources_json = json.dumps(draft.get("research_sources") or draft.get("sources") or [])
        facts_json = json.dumps(draft.get("verified_facts") or [])
        missing_json = json.dumps(draft.get("missing_fields") or [])

        query = """
        INSERT INTO outreach_drafts (
            id, lead_id, prospect_id, company_id, profile_id,
            recipient_name, recipient_email, email_status,
            company_name, subject, body, value_proposition, call_to_action,
            personalization_explanation, research_sources_json, verified_facts_json,
            missing_fields_json, status, outbox_id, generation_method, error_message,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            recipient_name = excluded.recipient_name,
            recipient_email = excluded.recipient_email,
            email_status = excluded.email_status,
            company_name = excluded.company_name,
            subject = excluded.subject,
            body = excluded.body,
            value_proposition = excluded.value_proposition,
            call_to_action = excluded.call_to_action,
            personalization_explanation = excluded.personalization_explanation,
            research_sources_json = excluded.research_sources_json,
            verified_facts_json = excluded.verified_facts_json,
            missing_fields_json = excluded.missing_fields_json,
            status = excluded.status,
            outbox_id = excluded.outbox_id,
            generation_method = excluded.generation_method,
            error_message = excluded.error_message,
            updated_at = excluded.updated_at
        """

        async with self._connect() as db:
            await db.execute(query, (
                draft_id,
                draft.get("lead_id") or "",
                draft.get("prospect_id") or "",
                draft.get("company_id") or "",
                draft.get("profile_id") or "",
                draft.get("recipient_name") or "",
                draft.get("recipient_email") or "",
                draft.get("email_status") or "",
                draft.get("company_name") or "",
                draft.get("subject") or "",
                draft.get("body") or "",
                draft.get("value_proposition") or "",
                draft.get("call_to_action") or "",
                draft.get("personalization_explanation") or "",
                sources_json,
                facts_json,
                missing_json,
                draft.get("status") or "draft",
                draft.get("outbox_id") or "",
                draft.get("generation_method") or "ai",
                draft.get("error_message") or "",
                draft.get("created_at") or now,
                now,
            ))
            await db.commit()

        await self.log_action("outreach_draft_saved", draft_id, {
            "lead_id": draft.get("lead_id"),
            "prospect_id": draft.get("prospect_id"),
            "status": draft.get("status") or "draft",
            "subject": draft.get("subject", "")[:60],
        })
        return draft_id

    async def get_outreach_draft(self, draft_id: str) -> dict | None:
        """Fetch a specific outreach draft by ID."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM outreach_drafts WHERE id = ?", (draft_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return self._parse_outreach_draft_row(row)

    async def get_outreach_drafts(
        self,
        lead_id: str | None = None,
        prospect_id: str | None = None,
        company_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Fetch outreach drafts filtered by lead, prospect, company, or status."""
        clauses = []
        params = []
        if lead_id:
            clauses.append("lead_id = ?")
            params.append(lead_id)
        if prospect_id:
            clauses.append("prospect_id = ?")
            params.append(prospect_id)
        if company_id:
            clauses.append("company_id = ?")
            params.append(company_id)
        if status:
            clauses.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM outreach_drafts {where} ORDER BY created_at DESC"

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._parse_outreach_draft_row(r) for r in rows]

    async def update_outreach_draft(self, draft_id: str, **kwargs) -> bool:
        """Update specific columns of an outreach draft."""
        allowed = {
            "subject", "body", "recipient_name", "recipient_email",
            "email_status", "value_proposition", "call_to_action",
            "personalization_explanation", "status", "outbox_id",
            "error_message",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        updates["updated_at"] = _utcnow().isoformat()
        set_clauses = [f"{k} = ?" for k in updates]
        params = list(updates.values()) + [draft_id]

        async with self._connect() as db:
            cursor = await db.execute(
                f"UPDATE outreach_drafts SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
            await db.commit()
            return cursor.rowcount > 0

    async def cancel_outreach_draft(self, draft_id: str, reason: str = "") -> bool:
        """Cancel an outreach draft and its corresponding pending outbox item."""
        draft = await self.get_outreach_draft(draft_id)
        if not draft:
            return False

        outbox_id = draft.get("outbox_id")
        async with self._connect() as db:
            await db.execute(
                "UPDATE outreach_drafts SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (_utcnow().isoformat(), draft_id),
            )
            if outbox_id:
                # Cancel pending outbox item safely without touching pilot drafts
                await db.execute(
                    "UPDATE outbox SET status = 'cancelled', error = ? WHERE id = ? AND status = 'pending_review'",
                    (reason or "Draft cancelled by operator", outbox_id),
                )
            await db.commit()

        await self.log_action("outreach_draft_cancelled", draft_id, {
            "reason": reason,
            "outbox_id": outbox_id,
        })
        return True

    # ── Migration 23: Integrations & Provider Audits ──

    async def log_provider_request(
        self,
        provider: str,
        endpoint: str = "",
        method: str = "GET",
        status_code: int = 200,
        success: bool = True,
        request_summary: str = "",
        response_summary: str = "",
        latency_ms: int = 0,
        cost_usd: float = 0.0,
        error_message: str = "",
    ) -> str:
        """Log an external provider API call for usage and cost audit."""
        import uuid
        req_id = f"plog_{uuid.uuid4().hex[:12]}"
        now_iso = _utcnow().isoformat()
        try:
            async with self._connect() as db:
                await db.execute(
                    """
                    INSERT INTO provider_logs (
                        id, provider, endpoint, method, status_code, success,
                        request_summary, response_summary, latency_ms, cost_usd,
                        error_message, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        req_id, provider, endpoint, method, status_code,
                        1 if success else 0, request_summary, response_summary,
                        latency_ms, cost_usd, error_message, now_iso
                    ),
                )
                await db.execute(
                    """
                    UPDATE integrations
                    SET usage_count = usage_count + 1,
                        estimated_cost_usd = estimated_cost_usd + ?,
                        last_tested_at = ?,
                        last_test_success = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE provider = ?
                    """,
                    (cost_usd, now_iso, 1 if success else 0, error_message or None, now_iso, provider)
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to log provider request for {provider}: {e}")
        return req_id

    async def update_integration_status(
        self,
        provider: str,
        status: str,
        last_error: str | None = None,
        increment_usage: bool = False,
        cost: float = 0.0,
    ) -> bool:
        """Update provider connection status in integrations table."""
        now_iso = _utcnow().isoformat()
        try:
            async with self._connect() as db:
                if increment_usage:
                    await db.execute(
                        """
                        UPDATE integrations
                        SET status = ?, last_error = ?,
                            usage_count = usage_count + 1,
                            estimated_cost_usd = estimated_cost_usd + ?,
                            last_tested_at = ?,
                            last_test_success = ?,
                            updated_at = ?
                        WHERE provider = ?
                        """,
                        (status, last_error, cost, now_iso, 1 if not last_error else 0, now_iso, provider)
                    )
                else:
                    await db.execute(
                        """
                        UPDATE integrations
                        SET status = ?, last_error = ?,
                            last_tested_at = ?,
                            last_test_success = ?,
                            updated_at = ?
                        WHERE provider = ?
                        """,
                        (status, last_error, now_iso, 1 if not last_error else 0, now_iso, provider)
                    )
                await db.commit()
                return True
        except Exception as e:
            logger.warning(f"Failed to update integration status for {provider}: {e}")
            return False

    async def get_integration_statuses(self) -> list[dict[str, Any]]:
        """Return all provider rows from integrations table."""
        try:
            async with self._connect() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM integrations ORDER BY integration_type, display_name") as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Failed to fetch integration statuses: {e}")
            return []

    async def save_discovery_run(self, run_dict: dict[str, Any]) -> str:
        """Insert or update a discovery run record."""
        import uuid
        run_id = run_dict.get("id") or f"drun_{uuid.uuid4().hex[:12]}"
        now_iso = _utcnow().isoformat()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO discovery_runs (
                    id, profile_id, provider, geography, industry, keywords,
                    total_found, new_leads, duplicates_skipped,
                    estimated_cost_usd, actual_cost_usd, status,
                    error_message, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    total_found = excluded.total_found,
                    new_leads = excluded.new_leads,
                    duplicates_skipped = excluded.duplicates_skipped,
                    actual_cost_usd = excluded.actual_cost_usd,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    completed_at = excluded.completed_at
                """,
                (
                    run_id,
                    run_dict.get("profile_id", ""),
                    run_dict.get("provider", ""),
                    run_dict.get("geography", ""),
                    run_dict.get("industry", ""),
                    run_dict.get("keywords", ""),
                    run_dict.get("total_found", 0),
                    run_dict.get("new_leads", 0),
                    run_dict.get("duplicates_skipped", 0),
                    run_dict.get("estimated_cost_usd", 0.0),
                    run_dict.get("actual_cost_usd", 0.0),
                    run_dict.get("status", "complete"),
                    run_dict.get("error_message"),
                    run_dict.get("started_at", now_iso),
                    run_dict.get("completed_at", now_iso),
                )
            )
            await db.commit()
        return run_id

    async def save_research_cache(
        self,
        domain: str,
        company_name: str,
        research_json: str,
        sources_json: str,
        confidence: float = 0.0,
        is_sample: bool = False,
    ) -> bool:
        """Cache research facts for a domain to avoid duplicate web scrapes."""
        now_iso = _utcnow().isoformat()
        clean_domain = domain.lower().strip()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO research_cache (
                    domain, company_name, research_json, sources_json,
                    confidence, is_sample, researched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_domain,
                    company_name,
                    research_json,
                    sources_json,
                    confidence,
                    1 if is_sample else 0,
                    now_iso,
                )
            )
            await db.commit()
        return True

    async def get_research_cache(self, domain: str) -> dict[str, Any] | None:
        """Retrieve cached research facts for a domain if available."""
        clean_domain = domain.lower().strip()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM research_cache WHERE domain = ?", (clean_domain,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return dict(row)
        return None


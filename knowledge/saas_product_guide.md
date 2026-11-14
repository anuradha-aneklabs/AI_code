# Harvey AI B2B SaaS Sales Acceleration Guide

## Verified Capabilities & Features
- **Outbound Prospect Qualification:** Harvey identifies high-fit ICP accounts across modern technology and B2B SaaS verticals based on headcount, modern tech stack adoption, and verified leadership roles.
- **Signal-Grounded Correspondence:** Outreach sequences reference publicly observable technical signals, funding announcements, and role titles without hallucinating private corporate metrics.
- **Operator Outbox Staging:** Staged Step 1 drafts remain held in the canonical Outbox awaiting human operator approval. Step 2 and Step 3 remain scheduled sequences and are never transmitted automatically.
- **Local-First Data Governance:** All CRM records, account signals, and prospect correspondence are persisted in the organization's local SQLite database. No proprietary account data is shared with public third-party LLMs.

## Explicit Limitations & Out-of-Scope Capabilities
- Harvey does NOT scrape private internal databases or bypass authentication paywalls.
- Harvey does NOT automatically send live cold outreach sequences without an operator explicitly reviewing and approving each draft.
- Harvey does NOT guarantee closed revenue or replacement of human account executives; it functions as an autonomous research and draft-staging assistant.


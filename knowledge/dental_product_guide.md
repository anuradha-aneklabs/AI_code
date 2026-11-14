# Harvey AI Dental Practice Automation Guide

## Verified Capabilities & Features
- **Patient Inquiry Response:** Harvey triages inbound messages from prospective patients regarding standard clinic hours, general services offered, and accepted insurance plans.
- **Appointment Intake Request Form:** Patients can submit preferred appointment windows and reason for visit. The clinic front-desk staff receives and confirms the scheduled slot.
- **HIPAA Local-First Storage:** All clinic records, lead notes, and patient inquiries are persisted locally on the practice's private SQLite database with token-protected loopback authentication. No clinical notes or patient inquiries are uploaded to third-party clouds.
- **Manual Human Approval Gate:** No outbound email or follow-up campaign is sent without explicit one-click review and operator approval in the Outbox.

## Explicit Limitations & Out-of-Scope Capabilities
- Harvey does NOT offer real-time calendar synchronization or direct booking into third-party EHR software.
- Harvey does NOT diagnose dental conditions, prescribe medications, or offer clinical dental advice.
- Harvey does NOT guarantee pricing estimates; all treatment fees require in-person dental consultation and insurance pre-authorization.


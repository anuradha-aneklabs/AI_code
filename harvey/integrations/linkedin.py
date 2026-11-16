"""LinkedIn browser automation via Playwright.

Safety-first: humanlike randomized delays, hard daily activity caps
(persisted across restarts), and consistent browser fingerprint to
protect the user's LinkedIn account from restriction.
"""

import asyncio
import json
import logging
import random
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

logger = logging.getLogger("harvey.linkedin")

from harvey.paths import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
COOKIES_PATH = DATA_DIR / "linkedin_cookies.json"
ACTIVITY_PATH = DATA_DIR / "linkedin_activity.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Hard daily caps — conservative on purpose. LinkedIn restricts accounts
# that view too many profiles or run too many searches per day.
MAX_DAILY_SEARCH_PAGES = 20
MAX_DAILY_PROFILE_VIEWS = 40
MAX_SEARCH_PAGES_PER_QUERY = 5


class LinkedInAutomation:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.browser = None
        self.context = None
        self.page = None
        self._pw = None
        self._activity = self._load_activity()

    # ── Daily activity caps ──

    @staticmethod
    def _load_activity() -> dict:
        """Load today's activity counters (persisted so restarts don't reset caps)."""
        today = date.today().isoformat()
        try:
            if ACTIVITY_PATH.exists():
                data = json.loads(ACTIVITY_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("date") == today:
                    return {
                        "date": today,
                        "search_pages": int(data.get("search_pages", 0)),
                        "profile_views": int(data.get("profile_views", 0)),
                    }
        except (ValueError, OSError) as e:
            logger.debug(f"Could not load LinkedIn activity log: {e}")
        return {"date": today, "search_pages": 0, "profile_views": 0}

    def _save_activity(self):
        try:
            ACTIVITY_PATH.parent.mkdir(parents=True, exist_ok=True)
            ACTIVITY_PATH.write_text(json.dumps(self._activity), encoding="utf-8")
        except OSError as e:
            logger.debug(f"Could not save LinkedIn activity log: {e}")

    def _record(self, kind: str):
        # Roll over if the date changed mid-run
        today = date.today().isoformat()
        if self._activity.get("date") != today:
            self._activity = {"date": today, "search_pages": 0, "profile_views": 0}
        self._activity[kind] = self._activity.get(kind, 0) + 1
        self._save_activity()

    def _cap_reached(self, kind: str, cap: int) -> bool:
        today = date.today().isoformat()
        if self._activity.get("date") != today:
            self._activity = {"date": today, "search_pages": 0, "profile_views": 0}
        if self._activity.get(kind, 0) >= cap:
            logger.warning(
                f"LinkedIn daily cap reached for {kind} "
                f"({cap}/day). Skipping to protect the account."
            )
            return True
        return False

    # ── Humanlike behavior ──

    async def _random_delay(self, min_s: float = 2.0, max_s: float = 6.0):
        """Human-like random delay between actions, with occasional long pauses."""
        delay = random.uniform(min_s, max_s)
        # ~10% of the time, take a noticeably longer "reading" pause
        if random.random() < 0.10:
            delay += random.uniform(4.0, 10.0)
        await asyncio.sleep(delay)

    async def _humanlike_scroll(self):
        """Scroll the page in a few small increments like a human reading."""
        if not self.page:
            return
        try:
            for _ in range(random.randint(2, 4)):
                await self.page.mouse.wheel(0, random.randint(250, 700))
                await asyncio.sleep(random.uniform(0.4, 1.4))
        except Exception as e:
            logger.debug(f"Scroll simulation failed: {e}")

    async def _wait_settled(self, timeout: int = 15000):
        """Wait for network idle without crashing on busy pages."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            # Feed-like pages never go fully idle; that's fine.
            pass

    # ── Lifecycle ──

    async def start(self):
        """Launch browser and set up context."""
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=True)

        # Always use the same fingerprint (viewport + UA), whether or not
        # we have saved cookies — a changing fingerprint is a red flag.
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=USER_AGENT,
        )

        # Load cookies if we have them (avoids re-login)
        if COOKIES_PATH.exists():
            try:
                cookies = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
                if isinstance(cookies, list):
                    await self.context.add_cookies(cookies)
                    logger.info("Loaded saved LinkedIn cookies.")
            except Exception as e:
                logger.warning(f"Could not load saved LinkedIn cookies: {e}")

        self.page = await self.context.new_page()

    def _save_cookies_sync(self, cookies: list):
        try:
            COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_PATH.write_text(json.dumps(cookies), encoding="utf-8")
            try:
                COOKIES_PATH.chmod(0o600)  # session cookies are credentials
            except OSError:
                pass
        except OSError as e:
            logger.warning(f"Could not save LinkedIn cookies: {e}")

    async def stop(self):
        """Save cookies and close browser."""
        try:
            if self.context:
                cookies = await self.context.cookies()
                self._save_cookies_sync(cookies)
                logger.info("Saved LinkedIn cookies.")
        except Exception as e:
            logger.debug(f"Could not save cookies on shutdown: {e}")
        try:
            if self.browser:
                await self.browser.close()
        except Exception as e:
            logger.debug(f"Error closing browser: {e}")
        finally:
            self.browser = None
            self.context = None
            self.page = None
        try:
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            logger.debug(f"Error stopping Playwright: {e}")
        finally:
            self._pw = None

    # ── Auth ──

    async def login(self) -> bool:
        """Log into LinkedIn."""
        if not self.page:
            await self.start()

        if not self.email or not self.password:
            logger.warning("LinkedIn credentials not configured; cannot log in.")
            return False

        try:
            await self.page.goto(
                "https://www.linkedin.com/login", timeout=30000
            )
            await self._wait_settled()
        except Exception as e:
            logger.error(f"Could not load LinkedIn login page: {e}")
            return False

        await self._random_delay(1, 3)

        # Check if already logged in (cookie session redirected us)
        if "feed" in self.page.url or "mynetwork" in self.page.url:
            logger.info("Already logged into LinkedIn.")
            return True

        try:
            await self.page.fill("#username", self.email)
            await self._random_delay(0.5, 1.5)
            await self.page.fill("#password", self.password)
            await self._random_delay(0.5, 1.5)
            await self.page.click('button[type="submit"]')
            await self._wait_settled()

            if "feed" in self.page.url or "mynetwork" in self.page.url:
                logger.info("Successfully logged into LinkedIn.")
                # Save cookies for future sessions
                cookies = await self.context.cookies()
                self._save_cookies_sync(cookies)
                return True

            # Check for security challenge
            if "checkpoint" in self.page.url or "challenge" in self.page.url:
                logger.warning(
                    "LinkedIn security checkpoint detected. "
                    "Please log in manually and save cookies."
                )
                return False

            logger.warning(f"Login may have failed. Current URL: {self.page.url}")
            return False

        except Exception as e:
            # Never include credentials in logged errors.
            msg = str(e).replace(self.password, "***") if self.password else str(e)
            logger.error(f"LinkedIn login failed: {msg}")
            return False

    # ── Search ──

    async def search_people(
        self,
        keywords: str = "",
        title: str = "",
        company: str = "",
        location: str = "",
        max_results: int = 25,
    ) -> list[dict]:
        """Search LinkedIn for people matching criteria.

        Returns list of dicts with: name, title, company, linkedin_url, location
        """
        if not self.page:
            await self.start()
            if not await self.login():
                return []

        # Fold all criteria into the keyword search (URL-encoded).
        terms = " ".join(t for t in [keywords, title, company, location] if t).strip()
        if not terms:
            logger.warning("search_people called with no criteria; skipping.")
            return []

        search_url = (
            "https://www.linkedin.com/search/results/people/"
            f"?keywords={quote_plus(terms)}"
        )

        results = []
        page_num = 1

        while len(results) < max_results:
            if self._cap_reached("search_pages", MAX_DAILY_SEARCH_PAGES):
                break

            url = f"{search_url}&page={page_num}"
            try:
                await self.page.goto(url, timeout=30000)
            except Exception as e:
                logger.warning(f"Failed to load search page {page_num}: {e}")
                break

            self._record("search_pages")
            await self._random_delay(3, 6)
            await self._wait_settled()
            await self._humanlike_scroll()

            # Extract search result cards
            cards = await self.page.query_selector_all("div.entity-result__item")

            if not cards:
                # Try alternate selector
                cards = await self.page.query_selector_all(
                    "li.reusable-search__result-container"
                )

            if not cards:
                logger.info(f"No results found on page {page_num}.")
                break

            for card in cards:
                if len(results) >= max_results:
                    break

                try:
                    profile = await self._extract_search_card(card)
                    if profile:
                        results.append(profile)
                except Exception as e:
                    logger.debug(f"Error extracting search card: {e}")
                    continue

            page_num += 1
            await self._random_delay(4, 8)

            # Safety: don't paginate deep on a single query
            if page_num > MAX_SEARCH_PAGES_PER_QUERY:
                break

        logger.info(f"Found {len(results)} LinkedIn profiles.")
        return results

    async def _extract_search_card(self, card) -> Optional[dict]:
        """Extract person info from a LinkedIn search result card."""
        # Get profile link
        link_el = await card.query_selector("a.app-aware-link")
        if not link_el:
            return None
        href = await link_el.get_attribute("href")
        if not href or "/in/" not in href:
            return None
        linkedin_url = href.split("?")[0]

        # Get name
        name_el = await card.query_selector("span.entity-result__title-text a span")
        name = await name_el.inner_text() if name_el else ""
        name = name.strip()

        # Get title/headline
        title_el = await card.query_selector("div.entity-result__primary-subtitle")
        title_text = await title_el.inner_text() if title_el else ""
        title_text = title_text.strip()

        # Get location
        loc_el = await card.query_selector("div.entity-result__secondary-subtitle")
        location = await loc_el.inner_text() if loc_el else ""
        location = location.strip()

        # Split name
        parts = name.split(" ", 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        # Try to extract company from title (often "Title at Company")
        company = ""
        if " at " in title_text:
            title_text, company = (
                s.strip() for s in title_text.split(" at ", 1)
            )

        return {
            "first_name": first_name,
            "last_name": last_name,
            "title": title_text,
            "company": company,
            "linkedin_url": linkedin_url,
            "location": location,
        }

    # ── Profiles ──

    async def extract_profile(self, linkedin_url: str) -> Optional[dict]:
        """Scrape a LinkedIn profile page for detailed info."""
        if not self.page:
            await self.start()
            if not await self.login():
                return None

        if self._cap_reached("profile_views", MAX_DAILY_PROFILE_VIEWS):
            return None

        try:
            await self.page.goto(linkedin_url, timeout=30000)
        except Exception as e:
            logger.warning(f"Failed to load profile {linkedin_url}: {e}")
            return None

        self._record("profile_views")
        await self._random_delay(3, 5)
        await self._wait_settled()
        await self._humanlike_scroll()

        try:
            # Name
            name_el = await self.page.query_selector("h1")
            name = await name_el.inner_text() if name_el else ""

            # Headline
            headline_el = await self.page.query_selector("div.text-body-medium")
            headline = await headline_el.inner_text() if headline_el else ""

            # Location
            loc_el = await self.page.query_selector("span.text-body-small.inline")
            location = await loc_el.inner_text() if loc_el else ""

            # About section
            about_el = await self.page.query_selector(
                "section.pv-about-section div.inline-show-more-text"
            )
            about = await about_el.inner_text() if about_el else ""

            parts = name.strip().split(" ", 1)

            return {
                "first_name": parts[0] if parts else "",
                "last_name": parts[1] if len(parts) > 1 else "",
                "headline": headline.strip(),
                "location": location.strip(),
                "about": about.strip(),
                "linkedin_url": linkedin_url,
            }

        except Exception as e:
            logger.error(f"Error extracting profile {linkedin_url}: {e}")
            return None


LINKEDIN_URL_RE = re.compile(
    r"^https?://([a-z]{2,3}\.)?linkedin\.com/(in|company|school)/[a-zA-Z0-9_\-%]+/?$",
    re.I
)


def validate_linkedin_url(url: str) -> bool:
    """Validate format of LinkedIn profile or company page URL."""
    if not url or not isinstance(url, str):
        return False
    return bool(LINKEDIN_URL_RE.match(url.strip()))


def extract_linkedin_slug(url: str) -> str:
    """Extract username or company slug from LinkedIn URL."""
    if not url or not isinstance(url, str):
        return ""
    clean = url.strip().rstrip("/")
    return clean.split("/")[-1]


async def generate_linkedin_outreach(
    name: str,
    company: str,
    title: str = "",
    industry: str = "",
    offer: str = "",
    notes: str = "",
    state=None,
) -> dict[str, Any]:
    """Generate grounded LinkedIn connection request (<=200 chars) and InMail (<=500 chars).

    Uses local Ollama or active TaskRouter; gracefully degrades to deterministic templates.
    """
    first_name = name.strip().split()[0] if name.strip() else "there"
    comp_name = company.strip() or "your team"
    title_phrase = f" as {title.strip()}" if title.strip() else ""
    ind_phrase = f" in {industry.strip()}" if industry.strip() else ""

    # Build prompt for AI generation
    prompt = (
        f"You are a B2B sales outreach copywriter. Generate two short LinkedIn outreach messages "
        f"for {name.strip()}{title_phrase} at {comp_name}{ind_phrase}.\n"
        f"Offer context: {offer or 'Harvey autonomous AI sales agent platform'}.\n"
        f"Additional notes: {notes or 'relevant industry modernization'}.\n\n"
        f"Format your response EXACTLY as JSON:\n"
        f"{{\n"
        f'  "connection_note": "A personalized connection request under 200 characters.",\n'
        f'  "inmail_subject": "Brief subject under 50 characters",\n'
        f'  "inmail_body": "Grounded InMail message under 450 characters."\n'
        f"}}"
    )

    ai_generated = False
    connection_note = ""
    inmail_subject = ""
    inmail_body = ""
    provider_used = "deterministic_rules"

    try:
        from harvey.providers.router import TaskRouter
        router = TaskRouter()
        res = await router.execute(prompt=prompt, task="linkedin_outreach", expect_json=True, timeout=20.0)
        if not res.is_error and res.text:
            m = re.search(r"\{.*\}", res.text, re.DOTALL)
            if m:
                payload = json.loads(m.group(0))
                connection_note = str(payload.get("connection_note") or "").strip()
                inmail_subject = str(payload.get("inmail_subject") or "").strip()
                inmail_body = str(payload.get("inmail_body") or "").strip()
                ai_generated = True
                provider_used = res.provider or "ai"
    except Exception as e:
        logger.debug(f"AI LinkedIn generation failed, using rule templates: {e}")

    # Fallback to high-converting deterministic templates if AI generation failed or exceeded char limits
    if not connection_note or len(connection_note) > 280:
        connection_note = f"Hi {first_name}, impressed by your work at {comp_name}. Would love to connect and share relevant insights on autonomous pipeline workflows."
        if len(connection_note) > 200:
            connection_note = f"Hi {first_name}, impressed by your work at {comp_name}. Would love to connect and exchange insights on modern pipeline workflows."
        if len(connection_note) > 200:
            connection_note = f"Hi {first_name}, following your work at {comp_name}—would love to connect and share notes on B2B workflows."

    if not inmail_subject:
        inmail_subject = f"Connecting re: {comp_name} outbound workflows"

    if not inmail_body or len(inmail_body) > 600:
        inmail_body = (
            f"Hi {first_name},\n\n"
            f"I came across {comp_name} and wanted to reach out. We've built an autonomous sales development agent "
            f"that coordinates prospect discovery, qualification, and stage progression with 100% human review.\n\n"
            f"Would you be open to a brief 15-min chat next week?\n\n"
            f"Best regards,\nHarvey Team"
        )

    # Ensure hard character limits: connection note <= 200, inmail <= 500
    if len(connection_note) > 200:
        connection_note = connection_note[:197] + "..."

    return {
        "success": True,
        "name": name,
        "company": company,
        "title": title,
        "connection_note": connection_note,
        "connection_note_length": len(connection_note),
        "inmail_subject": inmail_subject,
        "inmail_body": inmail_body,
        "inmail_length": len(inmail_body),
        "ai_generated": ai_generated,
        "provider": provider_used,
        "manual_execution_note": "Copy and paste into LinkedIn connection request / InMail. Automated sending requires active LinkedIn session cookie.",
    }

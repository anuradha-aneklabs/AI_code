from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.goto('http://127.0.0.1:5555/#token=REPLACE_WITH_LOCAL_DASHBOARD_TOKEN')
    time.sleep(2)
    page.evaluate("showTab('companies')")
    time.sleep(1.5)
    page.evaluate("showCompanyContacts(0)")
    time.sleep(2)
    page.screenshot(path='reports/screenshots/08b_company_profile_drawer.png')
    print('Captured 08b_company_profile_drawer.png successfully!')

    # Test prospect drawer
    page.evaluate("showTab('prospects')")
    time.sleep(1.5)
    page.evaluate("showProspect(0)")
    time.sleep(2)
    page.screenshot(path='reports/screenshots/02b_prospect_drawer.png')
    print('Captured 02b_prospect_drawer.png successfully!')

    browser.close()

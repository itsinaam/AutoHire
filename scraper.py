import json
import os
import asyncio
import urllib.parse
from dotenv import load_dotenv

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
if os.name == "nt" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


load_dotenv()


# CONFIG

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")

SESSION_FILE = "linkedin_session.json"
OUTPUT_FILE = "linkedin_profiles.json"
PARALLEL_WORKERS = 3



# LOGIN
async def login(page, context):

    print("Opening LinkedIn Login...")

    await page.goto(
        "https://www.linkedin.com/login",
        wait_until="domcontentloaded"
    )

    await page.fill("#username", LINKEDIN_EMAIL)
    await page.fill("#password", LINKEDIN_PASSWORD)

    await page.click('button[type="submit"]')

    # wait after login
    await page.wait_for_timeout(15000)

    current_url = page.url

    # if login success
    if "feed" in current_url or "checkpoint" not in current_url:

        print("Login successful")

        await context.storage_state(path=SESSION_FILE)

        print("Session saved")
        return True

    else:
        print("Login failed / verification required")
        return False


# SEARCH PROFILES
async def search_profiles(page, search_keyword, location_id, limit):

    print("Opening LinkedIn Search...")

    # =========================
    # BUILD SEARCH URL
    # =========================
    keyword = urllib.parse.quote(search_keyword)

    search_url = (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={keyword}"
        "&origin=FACETED_SEARCH"
        f"&geoUrn=%5B%22{location_id}%22%5D"
    )

    # =========================
    # OPEN PAGE
    # =========================
    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

    await page.wait_for_timeout(5000)

    # =========================
    # SCRAPE PROFILE LINKS
    # =========================
    profile_links = []

    try:
        cards = page.locator('a[href*="/in/"]')

        await cards.first.wait_for(timeout=15000)

        total = await cards.count()
        print(f"Found {total} links")

        for i in range(total):
            try:
                href = await cards.nth(i).get_attribute("href")

                if href and "/in/" in href:
                    clean_url = href.split("?")[0]

                    if clean_url not in profile_links:
                        profile_links.append(clean_url)

                if len(profile_links) >= limit:
                    break

            except Exception:
                continue

    except PlaywrightTimeoutError:
        print("No profiles loaded or page blocked / slow response")

    return profile_links


# SCRAPE PROFILE

async def scrape_profile(context, profile_url):

    print(f"\nOpening: {profile_url}")

    page = await context.new_page()

    try:

        await page.goto(
            profile_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(5000)

        # -------------------------
        # NAME
        # -------------------------

        name = ""

        try:
            # Find h2 inside the profile link (a[href*="/in/"])
            name = await page.locator('a[href*="/in/"] h2').first.inner_text()
            name = name.strip()
            print("Name:", name)
        except:
            pass

        # -------------------------
        # HEADLINE
        # -------------------------

        headline = ""

        try:
            # Skip the connection level indicator ("· 3rd") and get the actual headline
            # Get all p tags and take the first meaningful one (usually 2nd p after profile info)
            paragraphs = page.locator('p')
            p_count = await paragraphs.count()
            
            if p_count > 1:
                # Get second p (index 1) which is usually the job title/headline
                headline = await paragraphs.nth(1).inner_text()
                headline = headline.strip()
        except:
            pass

        # -------------------------
        # LOCATION
        # -------------------------

        location = ""

        try:
            location = await page.locator(
                "xpath=//div[contains(@class,'text-body-small')]/span | //p[contains(text(), ',')]"
            ).first.inner_text()
            location = location.strip()
            print("Location:", location)
        except:
            pass

        # -------------------------
        # ABOUT
        # -------------------------

        about = ""

        try:
            about_section = page.locator(
                'span[data-testid="expandable-text-box"]'
            )

            if await about_section.count() > 0:
                about = await about_section.first.inner_text()
                about = about.strip()

        except:
            pass

        # -------------------------
        # EXPERIENCE
        # -------------------------

        experience_url = profile_url.rstrip("/") + "/details/experience/"

        await page.goto(
            experience_url,
            wait_until="domcontentloaded"
        )

        await page.wait_for_timeout(3000)

        experiences = []

        cards = page.locator(
            'div[componentkey^="entity-collection-item"]'
        )

        # Wait for the first card to be visible
        try:
            await cards.first.wait_for(timeout=10000)
        except:
            print(f"No experience cards found for {profile_url}")
            count = 0

        # Scroll down to load all experiences
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)

        count = await cards.count()

        for i in range(count):

            try:

                card = cards.nth(i)

                all_p = card.locator("p")
                p_count = await all_p.count()

                title = await all_p.nth(0).inner_text() if p_count > 0 else ""
                company = await all_p.nth(1).inner_text() if p_count > 1 else ""
                duration = await all_p.nth(2).inner_text() if p_count > 2 else ""
                exp_location = await all_p.nth(3).inner_text() if p_count > 3 else ""

                experiences.append({
                    "title": title.strip() if title else "",
                    "company": company.strip() if company else "",
                    "duration": duration.strip() if duration else "",
                    "location": exp_location.strip() if exp_location else ""
                })

            except Exception as e:
                print("Experience error:", e)

        data = {
            "profile_url": profile_url,
            "name": name,
            "headline": headline,
            "location": location,
            "about": about,
            "experience": experiences
        }

        await page.close()

        return data

    except Exception as e:

        print("Profile scrape error:", e)

        await page.close()

        return None


def save_profiles(profiles, output_file):

    with open(output_file, "w", encoding="utf-8") as file_handle:
        json.dump(
            profiles,
            file_handle,
            indent=4,
            ensure_ascii=False
        )


async def run_scraper(
    search_keyword,
    location_id,
    limit,
    output_file=OUTPUT_FILE,
    parallel_workers=PARALLEL_WORKERS,
    headless=False,
):

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=headless,
            slow_mo=500
        )

        try:
            if not os.path.exists(SESSION_FILE):

                print("No session found, creating new context for login...")

                context = await browser.new_context(
                    viewport={"width": 1400, "height": 900}
                )
                page = await context.new_page()
                page.set_default_timeout(60000)

                login_successful = await login(page, context)

                await page.close()
                await context.close()

                if not login_successful:
                    raise RuntimeError(
                        "LinkedIn login failed or verification is required."
                    )

            context = await browser.new_context(
                storage_state=SESSION_FILE,
                viewport={"width": 1400, "height": 900}
            )
            page = await context.new_page()
            page.set_default_timeout(60000)

            profile_links = await search_profiles(
                page,
                search_keyword,
                location_id,
                limit,
            )

            print("\nCollected Profiles:")
            print(profile_links)

            await page.close()
            await context.close()

            print(f"\nStarting parallel scraping with {parallel_workers} workers...")

            all_profiles = []
            semaphore = asyncio.Semaphore(parallel_workers)

            async def scrape_with_semaphore(profile_url, index):
                async with semaphore:
                    profile_context = None
                    try:
                        profile_context = await browser.new_context(
                            storage_state=SESSION_FILE,
                            viewport={"width": 1400, "height": 900}
                        )

                        data = await scrape_profile(profile_context, profile_url)

                        if data:
                            all_profiles.append(data)
                            print(f"Profile {index + 1} completed")
                        else:
                            print(f"Profile {index + 1} failed")

                        return data

                    except Exception as exc:
                        print(f"Error scraping profile {index + 1}: {exc}")
                        return None

                    finally:
                        if profile_context is not None:
                            try:
                                await profile_context.close()
                            except Exception as exc:
                                print(f"Error closing profile context {index + 1}: {exc}")

            tasks = [
                scrape_with_semaphore(profile_url, index)
                for index, profile_url in enumerate(profile_links)
            ]

            if tasks:
                await asyncio.gather(*tasks)

            print(f"Saving {len(all_profiles)} scraped profiles to {output_file}")
            save_profiles(all_profiles, output_file)

            result = {
                "search_keyword": search_keyword,
                "location_id": location_id,
                "limit": limit,
                "saved_to": output_file,
                "total_profiles": len(all_profiles),
                "profiles": all_profiles,
            }
            print(f"Scraper finished. Returning {len(all_profiles)} profiles to caller")

            return result
        finally:
            print("Closing browser...")
            try:
                await asyncio.wait_for(browser.close())
                print("Browser closed")
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                print(f"Browser close failed: {exc}")


def run_scraper_sync(
    search_keyword,
    location_id,
    limit,
    output_file=OUTPUT_FILE,
    parallel_workers=PARALLEL_WORKERS,
    headless=False,
):
    return asyncio.run(
        run_scraper(
            search_keyword=search_keyword,
            location_id=location_id,
            limit=limit,
            output_file=output_file,
            parallel_workers=parallel_workers,
            headless=headless,
        )
    )



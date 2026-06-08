import json
import os
import asyncio
import urllib.parse
from dotenv import load_dotenv


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

    # Scroll down to ensure form is visible
    await page.evaluate("window.scrollBy(0, 300)")
    await page.wait_for_timeout(1000)

    # Wait for inputs to be visible and fill them
    await page.wait_for_selector('input[autocomplete="username webauthn"]', timeout=15000)
    await page.fill('input[autocomplete="username webauthn"]', LINKEDIN_EMAIL)
    
    # Scroll down more to make password field visible
    await page.evaluate("window.scrollBy(0, 200)")
    await page.wait_for_timeout(1000)
    
    # Use .last to select the second password input (login field)
    password_input = page.locator('input[autocomplete="current-password"]').last
    await password_input.wait_for(state="attached", timeout=15000)
    await page.wait_for_timeout(500)
    await password_input.fill(LINKEDIN_PASSWORD)

    # Scroll down significantly to make the Sign in button visible
    await page.evaluate("window.scrollBy(0, 500)")
    await page.wait_for_timeout(2000)

    # Wait for the button to be visible and clickable
    sign_in_button = page.locator('button[type="button"]:has-text("Sign in")').last
    await sign_in_button.scroll_into_view_if_needed()
    await sign_in_button.wait_for(state="visible", timeout=10000)
    await page.wait_for_timeout(500)
    
    # Click the Sign in button
    await sign_in_button.click()

    # Wait for navigation to complete (wait for URL to change from /login)
    try:
        await page.wait_for_url(
            lambda url: "/login" not in url,
            timeout=30000
        )
    except PlaywrightTimeoutError:
        print("Login page URL didn't change - might be stuck on login or verification")

    # Additional wait for page to fully load
    await page.wait_for_timeout(3000)

    current_url = page.url
    print(f"Current URL after login: {current_url}")

    # Check if on checkpoint (verification) page
    if "checkpoint" in current_url:
        print("⚠️ LinkedIn requires verification! Waiting for you to complete verification...")
        print("Waiting up to 5 minutes for verification to complete...")
        
        # Wait for verification to be completed (URL should change away from checkpoint)
        try:
            await page.wait_for_url(
                lambda url: "checkpoint" not in url,
                timeout=300000  # 5 minutes
            )
            current_url = page.url
            print(f"✓ Verification completed! New URL: {current_url}")
            await page.wait_for_timeout(3000)
        except PlaywrightTimeoutError:
            print("❌ Verification timeout - verification was not completed in time")
            return False

    # if login success - should NOT be on login page and NOT on checkpoint
    if "/login" not in current_url and "checkpoint" not in current_url:

        print("✓ Login successful!")

        await context.storage_state(path=SESSION_FILE)

        print("✓ Session saved")
        return True

    else:
        print(f"❌ Login failed / verification required. Current URL: {current_url}")
        return False


# SEARCH PROFILES
async def search_profiles(page, search_keyword, location_text, industry_text, limit, connection_degree=None):

    print("Opening LinkedIn Search...")

    # =========================
    # BUILD INITIAL SEARCH URL
    # =========================
    keyword = urllib.parse.quote(search_keyword)

    # Start with base URL
    search_url = (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={keyword}"
        "&origin=FACETED_SEARCH"
    )
    
    # Add network filter (connection degree) to URL if specified
    # F = 1st degree, S = 2nd degree, O = 3rd+ degree
    if connection_degree:
        network_map = {
            "1st": "F",
            "2nd": "S",
            "3rd": "O",
        }
        network_code = network_map.get(connection_degree)
        if network_code:
            # URL encode ["F"] or ["S"] or ["O"]
            # safe='' ensures quotes are also encoded as %22
            network_param = urllib.parse.quote(f'["{network_code}"]', safe='')
            search_url += f"&network={network_param}"
            print(f"Added network filter to URL: {connection_degree} -> {network_code} -> {network_param}")

    # =========================
    # OPEN PAGE
    # =========================
    print("Loading LinkedIn search page...")
    
    try:
        # Try with domcontentloaded first (faster)
        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        
        # Check if we're redirected to login/checkpoint
        current_url = page.url
        print(f"Current URL: {current_url}")
        
        if "login" in current_url or "checkpoint" in current_url or "authwall" in current_url:
            print("⚠️ Session expired or verification required!")
            print("Please login manually or wait for verification...")
            
            if "checkpoint" in current_url:
                print("Waiting for verification to complete (5 minutes max)...")
                try:
                    await page.wait_for_url(
                        lambda url: "checkpoint" not in url and "login" not in url,
                        timeout=300000
                    )
                    print("✓ Verification completed!")
                    # Navigate to search page again
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                except PlaywrightTimeoutError:
                    print("❌ Verification timeout - please complete manually")
                    raise
            else:
                raise Exception("Session expired - please run scraper again to re-login")
        
    except PlaywrightTimeoutError as e:
        print(f"⚠️ Page load timeout error")
        print(f"Current URL: {page.url}")
        print("This usually means:")
        print("  1. Network connection issue")
        print("  2. LinkedIn is blocking/redirecting")
        print("  3. Session expired")
        raise
    
    # Wait for page to fully load and settle
    print("Waiting for page to completely load...")
    try:
        await page.wait_for_load_state("networkidle", timeout=30000)
    except:
        print("Network idle timeout, continuing...")
    
    # Wait for search results container to appear
    print("Waiting for search results to load...")
    try:
        await page.wait_for_selector('div.search-results-container, .search-results', timeout=15000)
    except:
        print("Search results container not found, continuing...")
    
    # Additional wait to ensure everything is rendered
    await page.wait_for_timeout(3000)
    print("✓ Page fully loaded")

    # =========================
    # ADD LOCATION FILTER
    # =========================
    if location_text:
        print(f"\nAdding location filter: {location_text}")
        
        try:
            # First, try to open the filters panel/dropdown
            filter_buttons = [
                'button:has-text("Locations")',
                'button:has-text("All filters")',
                'button[aria-label*="location" i]',
                'button:has-text("Show all filters")',
            ]
            
            filter_opened = False
            print("Trying to open location filter panel...")
            
            for selector in filter_buttons:
                try:
                    filter_btn = page.locator(selector)
                    count = await filter_btn.count()
                    print(f"  Checking: {selector} - Found: {count}")
                    
                    if count > 0:
                        first_btn = filter_btn.first
                        
                        # Wait for button to be visible
                        await first_btn.wait_for(state="visible", timeout=5000)
                        is_visible = await first_btn.is_visible()
                        print(f"    Button visible: {is_visible}")
                        
                        if is_visible:
                            # Scroll into view and click
                            await first_btn.scroll_into_view_if_needed()
                            await page.wait_for_timeout(500)
                            await first_btn.click()
                            await page.wait_for_timeout(2000)
                            
                            filter_opened = True
                            print(f"✓ Opened filter panel using: {selector}")
                            break
                except Exception as e:
                    print(f"    Error: {e}")
                    continue
            
            if not filter_opened:
                print("⚠️ Could not open filter panel, continuing anyway...")
            
            # Now find and interact with the location input field
            location_input = page.locator('input[placeholder="Add a location"]')
            
            # Wait for it to be visible with longer timeout
            print("Waiting for location input field...")
            await location_input.wait_for(state="visible", timeout=15000)
            
            # Scroll into view and click
            await location_input.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await location_input.click()
            await page.wait_for_timeout(1000)
            
            # Type the location
            await location_input.fill(location_text)
            print("Location typed, waiting for dropdown suggestions...")
            await page.wait_for_timeout(3500)  # Wait longer for suggestions to appear
            
            # Always select the first suggestion
            suggestion_clicked = False
            
            # Try multiple selectors for suggestions
            suggestion_selectors = [
                'div[role="listbox"] button',
                'div[role="listbox"] li', 
                'ul[role="listbox"] li',
                '.search-reusables__typeahead-list li',
                'div.basic-typeahead__selectable',
            ]
            
            print("Looking for location suggestions...")
            for selector in suggestion_selectors:
                try:
                    suggestions = page.locator(selector)
                    count = await suggestions.count()
                    
                    if count > 0:
                        print(f"  Found {count} suggestions with: {selector}")
                        
                        # Try to click the first suggestion with multiple methods
                        first_suggestion = suggestions.first
                        
                        try:
                            # Method 1: Wait for visible and normal click
                            print("  Attempting normal click...")
                            await first_suggestion.wait_for(state="visible", timeout=5000)
                            await first_suggestion.scroll_into_view_if_needed()
                            await page.wait_for_timeout(500)
                            await first_suggestion.click(timeout=5000)
                            print("  ✓ Clicked with normal click")
                            suggestion_clicked = True
                            await page.wait_for_timeout(2000)
                            break
                        except:
                            try:
                                # Method 2: Force click
                                print("  Normal click failed, trying force click...")
                                await first_suggestion.click(force=True, timeout=5000)
                                print("  ✓ Clicked with force click")
                                suggestion_clicked = True
                                await page.wait_for_timeout(2000)
                                break
                            except:
                                try:
                                    # Method 3: Get bounding box and click coordinates
                                    print("  Force click failed, trying coordinate click...")
                                    box = await first_suggestion.bounding_box()
                                    if box:
                                        await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                                        print("  ✓ Clicked with coordinates")
                                        suggestion_clicked = True
                                        await page.wait_for_timeout(2000)
                                        break
                                except:
                                    print(f"  All click methods failed for: {selector}")
                                    continue
                        
                except Exception as e:
                    print(f"  Error with {selector}: {str(e)[:100]}")
                    continue
            
            # Fallback: Use keyboard navigation
            if not suggestion_clicked:
                print("⚠️ Could not click suggestion, trying keyboard navigation...")
                try:
                    await location_input.press("ArrowDown")
                    await page.wait_for_timeout(500)
                    await location_input.press("Enter")
                    print("  ✓ Selected using keyboard (Arrow Down + Enter)")
                    await page.wait_for_timeout(2000)
                    suggestion_clicked = True
                except:
                    print("  Keyboard navigation also failed")
            
            if not suggestion_clicked:
                print("⚠️ All methods failed, pressing Enter")
                await location_input.press("Enter")
                await page.wait_for_timeout(2000)
            
            # Wait a bit longer for the modal to update after selection
            await page.wait_for_timeout(1500)
            
            # Click "Show results" button - more specific and robust
            button_clicked = False
            
            # Try different button selectors in order of specificity
            button_selectors = [
                'button:has-text("Show results")',
                'button:text-is("Show results")',
                'a:has-text("Show results")',
                '.artdeco-modal button:has-text("Show")',
                'button.artdeco-button--primary:has-text("Show")',
                'button:has-text("Show")',
                'button:has-text("Apply")',
                'button.artdeco-button--primary',
            ]
            
            print("Looking for 'Show results' button...")
            for selector in button_selectors:
                try:
                    btn = page.locator(selector)
                    count = await btn.count()
                    
                    if count > 0:
                        print(f"  Found button with: {selector}")
                        first_btn = btn.first
                        
                        # Wait for button to be visible
                        await first_btn.wait_for(state="visible", timeout=5000)
                        await first_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(800)
                        
                        # Try clicking
                        try:
                            await first_btn.click(timeout=5000)
                            button_clicked = True
                            print(f"✓ Clicked 'Show results' button")
                            await page.wait_for_timeout(5000)
                            break
                        except:
                            # Try force click
                            try:
                                await first_btn.click(force=True)
                                button_clicked = True
                                print(f"✓ Force clicked 'Show results' button")
                                await page.wait_for_timeout(5000)
                                break
                            except:
                                continue
                except:
                    continue
            
            if not button_clicked:
                print("⚠️ Could not click 'Show results' button")
                # Take screenshot for debugging
                try:
                    await page.screenshot(path="show_results_button_error.png")
                    print("Screenshot saved: show_results_button_error.png")
                except:
                    pass
                await page.wait_for_timeout(3000)
                
        except Exception as e:
            print(f"Could not add location filter: {e}")
            # Take screenshot for debugging
            try:
                await page.screenshot(path="location_filter_error.png")
                print("Screenshot saved: location_filter_error.png")
            except:
                pass
            # Take a screenshot for debugging
            try:
                await page.screenshot(path="location_filter_error.png")
                print("Screenshot saved as location_filter_error.png")
            except:
                pass
    
    # =========================
    # ADD INDUSTRY FILTER
    # =========================
    if industry_text:
        print(f"\nAdding industry filter: {industry_text}")
        
        try:
            # First, click "All filters" button to open the filters modal
            all_filters_selectors = [
                'button:has-text("All filters")',
                'button:text-is("All filters")',
                'button[aria-label*="filter" i]',
            ]
            
            filters_opened = False
            print("Opening 'All filters' modal...")
            
            for selector in all_filters_selectors:
                try:
                    filter_btn = page.locator(selector)
                    count = await filter_btn.count()
                    
                    if count > 0:
                        print(f"  Found 'All filters' button with: {selector}")
                        first_btn = filter_btn.first
                        await first_btn.wait_for(state="visible", timeout=5000)
                        await first_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(500)
                        await first_btn.click()
                        await page.wait_for_timeout(2500)
                        
                        filters_opened = True
                        print("✓ Opened 'All filters' modal")
                        break
                except Exception as e:
                    print(f"    Error: {e}")
                    continue
            
            if not filters_opened:
                print("⚠️ Could not open 'All filters' modal")
                raise Exception("Could not open filters modal")
            
            # Wait for modal container to be visible
            print("Waiting for modal to load...")
            await page.wait_for_timeout(2000)
            
            # Scroll down within the modal to find Industries section
            print("Scrolling down to find Industries section...")
            modal_scrolled = False
            
            # Try to find and scroll the modal content area
            modal_selectors = [
                '.artdeco-modal__content',
                'div[role="dialog"]',
                '.search-reusables__filters-modal',
            ]
            
            for modal_selector in modal_selectors:
                try:
                    modal = page.locator(modal_selector).first
                    if await modal.count() > 0:
                        print(f"  Found modal with: {modal_selector}")
                        # Scroll down gradually with smaller increments
                        # Scroll 7 times max with 200px each = 1400px total
                        for i in range(5):
                            await modal.evaluate('el => el.scrollBy(0, 200)')
                            await page.wait_for_timeout(500)
                            
                            # Check if input is visible after each scroll
                            industry_input_check = page.locator('input[placeholder="Add an industry"]')
                            try:
                                is_visible = await industry_input_check.is_visible()
                                if is_visible:
                                    print(f"  ✓ Found input field after {i+1} scroll(s) ({(i+1)*200}px)")
                                    modal_scrolled = True
                                    break
                            except:
                                pass
                        
                        if not modal_scrolled:
                            print(f"  Completed {7} scrolls (1400px total)")
                            modal_scrolled = True
                        break
                except Exception as e:
                    print(f"  Error with {modal_selector}: {e}")
                    continue
            
            # Additional wait for content to settle
            await page.wait_for_timeout(1500)
            
            # Try to find Industries heading and scroll to it
            print("Looking for Industries section...")
            try:
                industries_heading = page.locator('h2:has-text("Industry"), h3:has-text("Industry"), h2:text-is("Industry")')
                if await industries_heading.count() > 0:
                    await industries_heading.scroll_into_view_if_needed()
                    print("✓ Found and scrolled to Industry section")
                    await page.wait_for_timeout(1000)
            except Exception as e:
                print(f"  Industry heading not found: {str(e)[:80]}")
            
            # Click on "+ Add an industry" button to open the input field
            print("Looking for '+ Add an industry' button...")
            add_industry_clicked = False
            
            add_industry_selectors = [
                'button:has-text("Add an industry")',
                'a:has-text("Add an industry")',
                'div:has-text("Add an industry")',
                '[aria-label*="Add an industry" i]',
            ]
            
            for selector in add_industry_selectors:
                try:
                    add_btn = page.locator(selector).first
                    count = await add_btn.count()
                    
                    if count > 0:
                        print(f"  Found button with: {selector}")
                        await add_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(500)
                        
                        try:
                            await add_btn.click(timeout=5000)
                            print("✓ Clicked '+ Add an industry' button")
                            add_industry_clicked = True
                            await page.wait_for_timeout(2000)
                            break
                        except:
                            try:
                                await add_btn.click(force=True)
                                print("✓ Force clicked '+ Add an industry' button")
                                add_industry_clicked = True
                                await page.wait_for_timeout(2000)
                                break
                            except:
                                continue
                except:
                    continue
            
            if not add_industry_clicked:
                print("⚠️ Could not click '+ Add an industry' button, trying to find input directly...")
            
            # Now find the industry search input field
            print("Looking for industry input field...")
            industry_input = None
            input_selectors = [
                'input[placeholder="Add an industry"]',
                'input[aria-label*="industry" i]',
                'input[placeholder*="industry" i]',
            ]
            
            for selector in input_selectors:
                try:
                    temp_input = page.locator(selector).first
                    if await temp_input.count() > 0:
                        await temp_input.scroll_into_view_if_needed()
                        await page.wait_for_timeout(1000)
                        
                        if await temp_input.is_visible():
                            industry_input = temp_input
                            print(f"✓ Found visible input with: {selector}")
                            break
                except:
                    continue
            
            if not industry_input:
                raise Exception("Could not find industry input field after clicking button")
            
            # Click and type in the input field
            print("Clicking industry input field...")
            await industry_input.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await industry_input.click()
            await page.wait_for_timeout(1000)
            
            # Type the industry
            print(f"Typing industry: {industry_text}")
            await industry_input.fill(industry_text)
            await page.wait_for_timeout(3000)
            print("✓ Industry typed, waiting for dropdown...")
            
            # Wait for dropdown suggestions and click first one (like location filter)
            suggestion_clicked = False
            
            # Try multiple selectors for dropdown suggestions/checkboxes
            suggestion_selectors = [
                'div[role="checkbox"]',  # Checkboxes in industry list
                'div[role="listbox"] button',
                'div[role="listbox"] li', 
                'ul[role="listbox"] li',
            ]
            
            print("Looking for dropdown suggestions...")
            for selector in suggestion_selectors:
                try:
                    suggestions = page.locator(selector)
                    count = await suggestions.count()
                    
                    if count > 0:
                        print(f"  Found {count} suggestions/checkboxes with: {selector}")
                        
                        # Click first suggestion
                        first_suggestion = suggestions.first
                        
                        try:
                            # Method 1: Normal click
                            print("  Attempting normal click...")
                            await first_suggestion.wait_for(state="visible", timeout=5000)
                            await first_suggestion.scroll_into_view_if_needed()
                            await page.wait_for_timeout(500)
                            await first_suggestion.click(timeout=5000)
                            print("  ✓ Clicked first suggestion with normal click")
                            suggestion_clicked = True
                            await page.wait_for_timeout(2000)
                            break
                        except:
                            try:
                                # Method 2: Force click
                                print("  Normal click failed, trying force click...")
                                await first_suggestion.click(force=True, timeout=5000)
                                print("  ✓ Clicked first suggestion with force click")
                                suggestion_clicked = True
                                await page.wait_for_timeout(2000)
                                break
                            except:
                                try:
                                    # Method 3: Coordinate click
                                    print("  Force click failed, trying coordinate click...")
                                    box = await first_suggestion.bounding_box()
                                    if box:
                                        await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                                        print("  ✓ Clicked first suggestion with coordinates")
                                        suggestion_clicked = True
                                        await page.wait_for_timeout(2000)
                                        break
                                except:
                                    print("  All click methods failed, trying next selector...")
                                    
                except Exception as e:
                    print(f"  Error with selector {selector}: {str(e)[:100]}")
                    continue
                
                # Exit loop if suggestion was clicked
                if suggestion_clicked:
                    break
            
            # Fallback: Use keyboard navigation
            if not suggestion_clicked:
                print("⚠️ Could not click suggestion, trying keyboard navigation...")
                try:
                    await industry_input.press("ArrowDown")
                    await page.wait_for_timeout(500)
                    await industry_input.press("Enter")
                    print("  ✓ Selected using keyboard (Arrow Down + Enter)")
                    await page.wait_for_timeout(2000)
                    suggestion_clicked = True
                except:
                    print("  Keyboard navigation also failed")
            
            if not suggestion_clicked:
                print("⚠️ All methods failed, pressing Enter")
                await industry_input.press("Enter")
                await page.wait_for_timeout(2000)
            
            # Wait for selection to register
            await page.wait_for_timeout(1500)
            
            # Click "Show results" button
            button_clicked = False
            button_selectors = [
                'button:has-text("Show results")',
                'button:text-is("Show results")',
                'a:has-text("Show results")',
                '.artdeco-modal button:has-text("Show")',
                'button.artdeco-button--primary:has-text("Show")',
                'button:has-text("Show")',
                'button:has-text("Apply")',
                'button.artdeco-button--primary',
            ]
            
            print("Looking for 'Show results' button...")
            for selector in button_selectors:
                try:
                    btn = page.locator(selector)
                    count = await btn.count()
                    
                    if count > 0:
                        print(f"  Found button with: {selector}")
                        first_btn = btn.first
                        
                        await first_btn.wait_for(state="visible", timeout=5000)
                        await first_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(800)
                        
                        try:
                            await first_btn.click(timeout=5000)
                            button_clicked = True
                            print(f"✓ Clicked 'Show results' button")
                            await page.wait_for_timeout(5000)
                            break
                        except:
                            try:
                                await first_btn.click(force=True)
                                button_clicked = True
                                print(f"✓ Force clicked 'Show results' button")
                                await page.wait_for_timeout(5000)
                                break
                            except:
                                continue
                except:
                    continue
            
            if not button_clicked:
                print("⚠️ Could not click 'Show results' button")
                try:
                    await page.screenshot(path="industry_show_results_error.png")
                    print("Screenshot saved: industry_show_results_error.png")
                except:
                    pass
                await page.wait_for_timeout(3000)
                
        except Exception as e:
            print(f"Could not add industry filter: {e}")
            try:
                await page.screenshot(path="industry_filter_error.png")
                print("Screenshot saved: industry_filter_error.png")
            except:
                pass
    
    await page.wait_for_timeout(5000)

    # Note: Connection degree filter is now applied via URL parameter
    # No manual clicking needed!

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
            name = await page.locator('a[href*="/in/"] h2').first.inner_text()
            name = name.strip()
            print("Name:", name)
        except:
            pass

        # -------------------------
        # PROFILE PICTURE
        # -------------------------

        profile_picture = ""

        try:
            imgs = page.locator("section img")

            for i in range(await imgs.count()):
                img = imgs.nth(i)
                src = await img.get_attribute("src")

                if not src:
                    continue

                src = src.replace("&amp;", "&")

                # ❌ reject background image
                if "profile-displaybackgroundimage" in src:
                    continue

                # ✅ accept profile images only
                if "profile-framedphoto" in src or "profile-displayphoto" in src:
                    profile_picture = src
                    break

            print("Profile Picture:", profile_picture)

        except Exception as e:
            print("Image error:", e)

        # -------------------------
        # HEADLINE
        # -------------------------

        headline = ""

        try:
            card = page.locator("section").nth(1) 
            headline = await card.locator("p").first.inner_text()
            print("Headline:", headline)

        except Exception as e:
            print("Headline error:", e)

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
            "experience": experiences,
            "profile_picture": profile_picture
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
    location_text,
    industry_text,
    limit,
    connection_degree=None,
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
                location_text,
                industry_text,
                limit,
                connection_degree,
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
                "location_text": location_text,
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
                await asyncio.wait_for(browser.close(),timeout=5)
                print("Browser closed")
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                print(f"Browser close failed: {exc}")


def run_scraper_sync(
    search_keyword,
    location_text,
    industry,
    limit,
    connection_degree=None,
    output_file=OUTPUT_FILE,
    parallel_workers=PARALLEL_WORKERS,
    headless=False,
):
    return asyncio.run(
        run_scraper(
            search_keyword=search_keyword,
            location_text=location_text,
            industry_text=industry,
            connection_degree=connection_degree,
            limit=limit,
            output_file=output_file,
            parallel_workers=parallel_workers,
            headless=headless,
        )
    )


if __name__ == "__main__":
    # You can customize these parameters
    SEARCH_KEYWORD = "District Manager"
    LOCATION_TEXT = "Los Angeles, California, United States"
    INDUSTRY_TEXT = "Oil and Gas"
    CONNECTION_DEGREE = "3rd"  # Options: "1st", "2nd", "3rd", or None
    LIMIT = 5
    HEADLESS = False
    
    print("=" * 60)
    print("LinkedIn Profile Scraper")
    print("=" * 60)
    print(f"Search Keyword: {SEARCH_KEYWORD}")
    print(f"Location: {LOCATION_TEXT}")
    print(f"Industry: {INDUSTRY_TEXT}")
    print(f"Connection Degree: {CONNECTION_DEGREE}")
    print(f"Limit: {LIMIT}")
    print(f"Headless: {HEADLESS}")
    print("=" * 60)
    
    result = run_scraper_sync(
        search_keyword=SEARCH_KEYWORD,
        location_text=LOCATION_TEXT,
        industry=INDUSTRY_TEXT,
        connection_degree=CONNECTION_DEGREE,
        limit=LIMIT,
        headless=HEADLESS
    )
    
    print("\n" + "=" * 60)
    print("SCRAPING COMPLETED!")
    print("=" * 60)
    print(f"Total profiles scraped: {result['total_profiles']}")
    print(f"Results saved to: {result['saved_to']}")
    print("=" * 60)



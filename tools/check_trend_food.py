import os
from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
url = os.environ.get("CHECK_URL", "http://127.0.0.1:8091/")
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get("CHROME_PATH"), headless=True
    )
    for width in [1440, 390]:
        page = browser.new_page(viewport={"width": width, "height": 1000})
        errors = []
        page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
        page.route(
            "**/api/explore/nearby?**",
            lambda r: r.fulfill(
                json={
                    "places": [
                        {
                            "id": "ordinary",
                            "name": "일반 음식점",
                            "category": "restaurant",
                            "lat": 22.28,
                            "lng": 114.15,
                            "distance_m": 100,
                            "source": "QA",
                            "rating": 4.7,
                            "review_count": 100,
                        }
                    ],
                    "notice": "QA nearby",
                }
            ),
        )
        page.goto(url + "#food", wait_until="networkidle")
        page.wait_for_function('document.querySelector("#foodCity").options.length>1')
        page.evaluate(
            "()=>{const make=L.map;L.map=function(...args){const m=make.apply(this,args);"
            "if(args[0]==='foodMap')window.foodTestMap=m;return m;};}"
        )
        page.select_option("#foodCollection", "trend")
        page.wait_for_function('document.querySelectorAll("[data-food]").length===8')
        assert page.locator("#nearRadius").is_disabled()
        assert page.locator("#foodNotice").inner_text().find("실시간 인스타 순위") >= 0
        assert page.locator("#foodResults .rating").count() == 0
        page.select_option("#trendKind", "sns")
        assert page.locator("[data-food]").count() == 2
        assert page.locator("#foodMap .nearpin").count() == 2
        assert "Zero One" in page.locator("#foodResults").inner_text()
        assert "Milk Bar" in page.locator("#foodResults").inner_text()
        page.locator('[data-focus-food="trend-hk-zero-one"]').click()
        page.wait_for_function("Math.abs(foodTestMap.getCenter().lat-22.283656)<0.0001")
        assert page.locator("#foodMap .leaflet-popup").count() == 1
        page.select_option("#trendKind", "meal")
        assert page.locator("[data-food]").count() == 5
        page.select_option("#foodCity", "tokyo")
        page.wait_for_function('document.querySelectorAll("[data-food]").length===0')
        assert page.locator("#foodMap .nearpin").count() == 0
        page.select_option("#foodCity", "hong-kong")
        page.wait_for_function('document.querySelectorAll("[data-food]").length===5')
        page.select_option("#trendKind", "all")
        page.select_option("#trendScope", "nearby")
        page.wait_for_function('document.querySelector("#foodNotice").textContent.startsWith("검색 반경 내")')
        assert page.locator("[data-food]").count() == 0
        page.select_option("#nearRadius", "3000")
        page.wait_for_function('document.querySelectorAll("[data-food]").length>0')
        page.select_option("#trendScope", "city")
        page.wait_for_function('document.querySelectorAll("[data-food]").length===8')
        page.locator("#nearbySection").scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("scrollTo(0,0)")
        page.screenshot(path=str(root / f".local/trend-food-{width}.png"))
        page.evaluate(
            "()=>{navigator.geolocation.getCurrentPosition = ok => "
            "ok({coords:{latitude:22.283656,longitude:114.154419}});}"
        )
        page.locator("#findNearby").click()
        assert page.locator("#trendScope").input_value() == "nearby"
        assert page.locator("#nearRadius").is_enabled()
        assert page.locator('[data-food="trend-hk-zero-one"]').count() == 1
        page.select_option("#foodCollection", "all")
        page.wait_for_function('document.querySelectorAll("[data-food=ordinary]").length===1')
        assert page.locator("#trendControls").is_hidden()
        assert page.locator("#foodMap .nearpin").count() == 1
        assert not errors, errors
        page.close()
    page = browser.new_page()
    page.route("**/data/trending-food.json", lambda r: r.fulfill(status=503, body="unavailable"))
    page.goto(url + "#food", wait_until="networkidle")
    page.select_option("#foodCollection", "trend")
    page.wait_for_function('document.querySelector("#foodNotice").textContent.includes("불러오지 못했습니다")')
    assert page.locator("[data-food]").count() == 0
    page.close()
    browser.close()
print(
    "PASS: desktop/mobile curated, SNS/meal filters, city/radius, map sync/focus, "
    "missing ratings, standard nearby, no overflow or JS errors"
)

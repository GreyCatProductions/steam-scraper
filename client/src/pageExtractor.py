import json
import re
from typing import Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

from shared.schema.steamPage import DLC, SysReq, GamePage


def _extract_appid(soup: BeautifulSoup) -> int:
    og_url = soup.find("meta", property="og:url")
    if isinstance(og_url, Tag):
        m = re.search(r"/app/(\d+)/", str(og_url.get("content", "")))
        if m:
            return int(m.group(1))
    return 0


def _extract_title(soup: BeautifulSoup) -> str:
    name_el = soup.find(id="appHubAppName_responsive")
    if name_el:
        return name_el.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag:
        return re.sub(r"^Save \d+% on |on Steam$", "", title_tag.get_text(strip=True)).strip()
    return ""


def _extract_short_description(soup: BeautifulSoup) -> str:
    meta_desc = soup.find("meta", attrs={"name": "Description"})
    if meta_desc:
        return str(meta_desc.get("content", ""))
    snippet = soup.find(class_="game_description_snippet")
    if snippet:
        return snippet.get_text(strip=True)
    return ""


def _extract_description(soup: BeautifulSoup) -> str:
    desc_div = soup.find(id="game_area_description")
    return desc_div.get_text(" ", strip=True) if desc_div else ""


def _extract_release_date(soup: BeautifulSoup) -> str:
    date_div = soup.find(class_="release_date")
    if date_div:
        date_el = date_div.find(class_="date")
        if date_el:
            return date_el.get_text(strip=True)
    return ""


def _extract_early_access_date(soup: BeautifulSoup) -> str:
    details_block = soup.find(id="genresAndManufacturer")
    if details_block:
        m = re.search(r"Early Access Release Date:\s*(.+?)(?:\n|$)", details_block.get_text())
        if m:
            return m.group(1).strip()
    return ""


def _extract_developers(soup: BeautifulSoup) -> list[str]:
    dev_el = soup.find(id="developers_list")
    if dev_el:
        return [a.get_text(strip=True) for a in dev_el.find_all("a")]
    return []


def _extract_publishers(soup: BeautifulSoup) -> list[str]:
    for row in soup.find_all(class_="dev_row"):
        label = row.find("b")
        if label and "Publisher" in label.get_text():
            return [a.get_text(strip=True) for a in row.find_all("a")]
    return []


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    details_block = soup.find(id="genresAndManufacturer")
    if details_block:
        genre_span = details_block.find("span", attrs={"data-panel": True})
        if genre_span:
            return [a.get_text(strip=True) for a in genre_span.find_all("a")]
    return []


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    tags_div = soup.find(class_="glance_tags popular_tags")
    if tags_div:
        return [a.get_text(strip=True) for a in tags_div.find_all("a", class_="app_tag")]
    return []


def _extract_platforms(soup: BeautifulSoup) -> list[str]:
    subid_input = soup.find("input", attrs={"name": "subid"})
    platform_section = (
        soup.find(id="game_area_purchase_section_add_to_cart_" + str(subid_input["value"]))
        if subid_input
        else None
    ) or soup.find(class_="game_area_purchase_game")
    if platform_section:
        return [
            c
            for span in platform_section.find_all("span", class_="platform_img")
            for c in (span.get("class") or [])
            if c != "platform_img"
        ]
    return []


def _extract_price(soup: BeautifulSoup) -> tuple[Optional[int], Optional[int], int, str]:
    price_final_cents: Optional[int] = None
    price_original_cents: Optional[int] = None
    discount_pct = 0
    discount_ends: str = ""
    main_purchase = soup.find(class_="game_area_purchase_game")
    if main_purchase:
        discount_block = main_purchase.find(class_="discount_block")
        if isinstance(discount_block, Tag):
            price_final_cents = int(str(discount_block.get("data-price-final", "0")))
            discount_pct = int(str(discount_block.get("data-discount", "0")))
            orig_el = discount_block.find(class_="discount_original_price")
            if orig_el:
                digits = re.sub(r"[^\d]", "", orig_el.get_text())
                price_original_cents = int(digits) if digits else None
        countdown = main_purchase.find(class_="game_purchase_discount_countdown")
        if countdown:
            discount_ends = countdown.get_text(strip=True)
    return price_final_cents, price_original_cents, discount_pct, discount_ends


def _extract_reviews(soup: BeautifulSoup) -> tuple[
    str, Optional[int], Optional[int],
    str, Optional[int], Optional[int],
]:
    summary_recent: str = ""
    summary_all: str = ""
    count_recent = pct_recent = None
    count_all = pct_all = None
    reviews_div = soup.find(id="userReviews")
    if reviews_div:
        for row in reviews_div.find_all("a", class_="user_reviews_summary_row"):
            if not isinstance(row, Tag):
                continue
            label = row.find(class_="subtitle")
            summary_el = row.find(class_="game_review_summary")
            tooltip = str(row.get("data-tooltip-html", ""))
            pct_m = re.search(r"(\d+)%", tooltip)
            count_m = re.search(r"([\d,]+) user reviews", tooltip)
            pct = int(pct_m.group(1)) if pct_m else None
            count = int(count_m.group(1).replace(",", "")) if count_m else None
            summary = summary_el.get_text(strip=True) if summary_el else ""
            if label and "Recent" in label.get_text():
                summary_recent, count_recent, pct_recent = summary, count, pct
            else:
                summary_all, count_all, pct_all = summary, count, pct
    return summary_recent, count_recent, pct_recent, summary_all, count_all, pct_all


def _extract_metacritic(soup: BeautifulSoup) -> tuple[Optional[int], str]:
    meta_block = soup.find(id="game_area_metascore")
    if meta_block:
        score: Optional[int] = None
        score_el = meta_block.find(class_="score")
        if score_el:
            try:
                score = int(score_el.get_text(strip=True))
            except ValueError:
                pass
        link = meta_block.find("a")
        return score, (str(link.get("href")) if link else "")
    return None, ""


def _extract_header_image(soup: BeautifulSoup) -> str:
    img = soup.find(class_="game_header_image_full")
    return str(img.get("src")) if img else ""


def _extract_website_and_social(soup: BeautifulSoup) -> tuple[str, dict[str, str]]:
    website: str = ""
    social_links: dict[str, str] = {}
    links_block = soup.find(id="appDetailsUnderlinedLinks")
    if links_block:

        for a in links_block.find_all("a"):
            if not isinstance(a, Tag):
                continue
            text = a.get_text(strip=True)
            href = str(a.get("href", ""))
            lf_m = re.search(r"linkfilter/\?u=(.+)", href)
            if lf_m:
                href = unquote(lf_m.group(1))
            social_span = a.find(class_="social_account")
            if social_span:
                social_links[social_span.get_text(strip=True)] = href
            elif "Visit the website" in text:
                website = href
    return website, social_links


def _extract_steam_awards(soup: BeautifulSoup) -> list[str]:
    return [
        el.get_text(" ", strip=True)
        for el in soup.find_all(class_=re.compile(r"steamawards\d*_app_banner_header"))
    ]


def _extract_press_reviews(soup: BeautifulSoup) -> list[dict]:
    reviews: list[dict] = []
    section = soup.find(id="game_area_reviews")
    if section:
        raw = section.get_text("\n", strip=True)
        for m in re.finditer(r'"([^"]+)"\s*\n\s*([\d/]+)\s*[–-]\s*\n\s*(.+)', raw):
            reviews.append({
                "quote": m.group(1).strip(),
                "score": m.group(2).strip(),
                "outlet": m.group(3).strip(),
            })
    return reviews


def _extract_dlc(soup: BeautifulSoup) -> list[DLC]:
    dlc_list: list[DLC] = []
    for row in soup.find_all("a", class_="game_area_dlc_row"):
        if not isinstance(row, Tag):
            continue
        try:
            dlc_appid = int(str(row.get("data-ds-appid", "0")))
        except ValueError:
            continue
        name_el = row.find(class_="game_area_dlc_name")
        if name_el:
            for badge in name_el.find_all(class_="dlc_highlight_reason_container"):
                badge.decompose()
        dlc_name = name_el.get_text(strip=True) if name_el else ""
        dlc_price_final: Optional[int] = None
        dlc_price_orig: Optional[int] = None
        dlc_discount = 0
        price_el = row.find(class_="game_area_dlc_price")
        if price_el:
            db = price_el.find(class_="discount_block")
            if db:
                dlc_price_final = int(str(db.get("data-price-final", "0")))
                dlc_discount = int(str(db.get("data-discount", "0")))
                orig = db.find(class_="discount_original_price")
                if orig:
                    digits = re.sub(r"[^\d]", "", orig.get_text())
                    dlc_price_orig = int(digits) if digits else None
        dlc_list.append(DLC(
            appid=dlc_appid,
            name=dlc_name,
            price_final_cents=dlc_price_final,
            price_original_cents=dlc_price_orig,
            discount_pct=dlc_discount,
            url=str(row.get("href", "")),
        ))
    return dlc_list


def _extract_sys_req(soup: BeautifulSoup, os: str) -> Optional[SysReq]:
    container = soup.select_one(f'.sysreq_content[data-os="{os}"]')
    if not container:
        return None
    left = container.find(class_="game_area_sys_req_leftCol")
    right = container.find(class_="game_area_sys_req_rightCol")
    return SysReq(
        minimum=left.get_text(" ", strip=True) if left else "",
        recommended=right.get_text(" ", strip=True) if right else "",
    )


def _extract_ai_disclosure(soup: BeautifulSoup) -> str:
    content_desc = soup.find(id="game_area_content_descriptors")
    if content_desc:
        p = content_desc.find("i")
        if p:
            return p.get_text(strip=True)
    return ""


def _extract_bundle_count(soup: BeautifulSoup) -> int:
    bundle_link = soup.find("a", href=re.compile(r"/bundlelist/"))
    if bundle_link:
        m = re.search(r"(\d+) bundles", bundle_link.get_text())
        if m:
            return int(m.group(1))
    return 0


def extract(html: str) -> GamePage:
    '''
        Extracts all data from the html and returns GamePage object. The html is expected to be an english language steam store page.
        
        Throws exception on error.
    '''
    soup = BeautifulSoup(html, "html.parser")

    review_summary_recent, review_count_recent, review_pct_recent, \
        review_summary_all, review_count_all, review_pct_all = _extract_reviews(soup)

    price_final_cents, price_original_cents, discount_pct, discount_ends = _extract_price(soup)
    metacritic_score, metacritic_url = _extract_metacritic(soup)
    website, social_links = _extract_website_and_social(soup)

    return GamePage(
        appid=_extract_appid(soup),
        title=_extract_title(soup),
        short_description=_extract_short_description(soup),
        description=_extract_description(soup),
        release_date=_extract_release_date(soup),
        early_access_release_date=_extract_early_access_date(soup),
        developer=_extract_developers(soup),
        publisher=_extract_publishers(soup),
        genres=_extract_genres(soup),
        tags=_extract_tags(soup),
        platforms=_extract_platforms(soup),
        price_final_cents=price_final_cents,
        price_original_cents=price_original_cents,
        discount_pct=discount_pct,
        discount_ends=discount_ends,
        review_summary_recent=review_summary_recent,
        review_count_recent=review_count_recent,
        review_pct_recent=review_pct_recent,
        review_summary_all=review_summary_all,
        review_count_all=review_count_all,
        review_pct_all=review_pct_all,
        metacritic_score=metacritic_score,
        metacritic_url=metacritic_url,
        header_image=_extract_header_image(soup),
        website=website,
        social_links=social_links,
        steam_awards=_extract_steam_awards(soup),
        press_reviews=_extract_press_reviews(soup),
        dlc=_extract_dlc(soup),
        sys_req_windows=_extract_sys_req(soup, "win"),
        sys_req_mac=_extract_sys_req(soup, "mac"),
        ai_content_disclosure=_extract_ai_disclosure(soup),
        bundle_count=_extract_bundle_count(soup),
    )


def main():
    import argparse
    import dataclasses
    import sys

    parser = argparse.ArgumentParser(description="Extract game info from a Steam store HTML page.")
    parser.add_argument("file", nargs="?", help="Path to HTML file (defaults to stdin)")
    parser.add_argument("--field", metavar="FIELD", help="Print a single field value instead of full JSON")
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            html = f.read()
    else:
        html = sys.stdin.read()

    page = extract(html)

    if args.field:
        value = getattr(page, args.field, None)
        if value is None:
            parser.error(f"Unknown field: {args.field}")
        print(value)
    else:
        print(json.dumps(dataclasses.asdict(page), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

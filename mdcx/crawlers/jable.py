#!/usr/bin/env python3
import re
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from lxml import etree

from ..config.enums import Website
from ..config.manager import manager
from ..models.enums import FileMode
from ..models.log_buffer import LogBuffer


def get_actor_photo(actor: str):
    return {name: "" for name in [w.strip() for w in actor.split(",") if w.strip()]}


def normalize_code(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").upper())


def get_browser_headers(referer: str = "") -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
    }
    if referer:
        headers["Referer"] = referer
    return headers


def iter_request_urls(url: str):
    seen = set()
    parts = urlsplit(url)
    for scheme in (parts.scheme, "http" if parts.scheme == "https" else ""):
        if not scheme:
            continue
        candidate = urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def with_lang_zh(url: str) -> str:
    if not url:
        return url
    if "lang=" in url:
        return url
    parts = urlsplit(url)
    query = parts.query
    query = f"{query}&lang=zh" if query else "lang=zh"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


async def fetch_text(url: str, *, referer: str = "") -> tuple[str | None, str, str]:
    last_error = ""
    for candidate_url in iter_request_urls(url):
        headers = get_browser_headers(referer)
        html, error = await manager.computed.async_client.get_text(candidate_url, headers=headers)
        if html is not None:
            return html, "", candidate_url
        last_error = error
        if manager.config.use_proxy:
            html, error = await manager.computed.async_client.get_text(candidate_url, headers=headers, use_proxy=False)
            if html is not None:
                return html, "", candidate_url
            last_error = error
    return None, last_error or "请求失败", url


def extract_slug(url: str) -> str:
    match = re.search(r"/videos/([^/?#]+)/?", url)
    return unquote(match.group(1)).strip() if match else ""


def slug_to_number(slug: str) -> str:
    return (slug or "").replace("_", "-").strip().upper()


def normalize_release_date(value: str) -> tuple[str, str]:
    value = (value or "").strip().replace("/", "-").replace(".", "-")
    if not value:
        return "", ""
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if not match:
        year = re.search(r"\d{4}", value)
        return "", year.group() if year else ""
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    return f"{year}-{month:02d}-{day:02d}", year


def extract_search_candidates(html, base_url: str) -> list[str]:
    result = []
    seen = set()
    for href in html.xpath('//h6[contains(@class, "title")]/a/@href | //a[contains(@href, "/videos/")]/@href'):
        href = (href or "").strip()
        if not href:
            continue
        full_url = urljoin(base_url.rstrip("/") + "/", href)
        if "/videos/" not in full_url:
            continue
        dedup_key = re.sub(r"[?#].*$", "", full_url).rstrip("/")
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        result.append(full_url)
        if len(result) >= 3:
            break
    return result


def extract_title(html) -> str:
    title = "".join(html.xpath('//meta[@property="og:title"]/@content')).strip()
    if title:
        title = re.sub(r"\s*-\s*Jable\.TV.*$", "", title, flags=re.I).strip()
        return title
    title = "".join(html.xpath('//div[contains(@class, "info-header")]//h4[1]/text()')).strip()
    if title:
        return title
    title = "".join(html.xpath("//title/text()")).strip()
    return re.sub(r"\s*-\s*Jable\.TV.*$", "", title, flags=re.I).strip()


def extract_actor_list(html) -> list[str]:
    actors = []
    for node in html.xpath('//div[contains(@class, "models")]//a[contains(@class, "model")]'):
        name = (node.get("title") or "").strip()
        if not name:
            name = "".join(node.xpath('.//img/@title')).strip()
        if not name:
            name = "".join(node.xpath('.//img/@data-original-title')).strip()
        if not name:
            name = "".join(node.xpath('.//span/@title')).strip()
        if not name:
            name = "".join(node.xpath('.//text()')).strip()
        if name and name not in actors:
            actors.append(name)
    return actors


def extract_release(html_content: str) -> tuple[str, str]:
    match = re.search(r"上市於\s*(\d{4}-\d{2}-\d{2})", html_content)
    if match:
        return normalize_release_date(match.group(1))
    return "", ""


def extract_tags(html) -> list[str]:
    tags = []
    for text in html.xpath('//h5[contains(@class, "tags")]//a/text()'):
        text = (text or "").strip()
        if text and text not in tags:
            tags.append(text)
    return tags


def extract_cover_url(html) -> str:
    return "".join(html.xpath('//meta[@property="og:image"]/@content')).strip()


def extract_trailer(html_content: str) -> str:
    match = re.search(r"var\s+hlsUrl\s*=\s*'([^']+)'", html_content)
    return match.group(1).strip() if match else ""


def extract_canonical_url(html, fallback_url: str) -> str:
    canonical = "".join(html.xpath('//link[@rel="canonical"]/@href')).strip()
    return canonical or fallback_url


def extract_number(html, detail_url: str) -> str:
    title = extract_title(html)
    if match := re.search(r"\b([A-Za-z]{2,}\d*-\d+(?:-[A-Za-z0-9]+)?)\b", title):
        return match.group(1).upper()
    slug_number = slug_to_number(extract_slug(detail_url))
    return slug_number


def parse_detail_page(html_content: str, detail_url: str) -> dict:
    html = etree.fromstring(html_content, etree.HTMLParser())
    number = extract_number(html, detail_url)
    actors = extract_actor_list(html)
    full_title = extract_title(html)
    title = full_title
    if number:
        title = re.sub(rf"^\s*{re.escape(number)}\s*", "", title, flags=re.I).strip()
    if actors:
        for actor in actors[::-1]:
            pos = title.rfind(actor)
            if pos >= 0:
                title = title[:pos].strip(" .-_/:|")
                break
    release, year = extract_release(html_content)
    tags = extract_tags(html)
    cover_url = extract_cover_url(html)
    trailer_url = extract_trailer(html_content)
    canonical_url = extract_canonical_url(html, detail_url)

    return {
        "number": number,
        "title": title or full_title,
        "originaltitle": title or full_title,
        "actor": ",".join(actors),
        "outline": "",
        "originalplot": "",
        "tag": ",".join(tags),
        "release": release,
        "year": year,
        "runtime": "",
        "score": "",
        "series": "",
        "director": "",
        "studio": "",
        "publisher": "",
        "source": "jable",
        "website": canonical_url,
        "actor_photo": get_actor_photo(",".join(actors)),
        "thumb": cover_url,
        "poster": cover_url,
        "extrafanart": [],
        "trailer": trailer_url,
        "image_download": False,
        "image_cut": "no",
        "mosaic": "有码",
        "wanted": "",
    }


async def main(
    number,
    appoint_url="",
    file_path="",
    appoint_number="",
    file_mode=FileMode.Default,
    **kwargs,
):
    start_time = time.time()
    website_name = "jable"
    LogBuffer.req().write(f"-> {website_name}")
    LogBuffer.info().write(" \n    🌐 jable")
    web_info = "\n       "
    debug_info = ""

    jable_url = manager.config.get_site_url(Website.JABLE, "https://jable.tv")
    target_number = (appoint_number or number or "").strip()
    normalized_target = normalize_code(target_number)
    real_url = ""

    try:
        if appoint_url and "/videos/" in appoint_url:
            real_url = appoint_url
            debug_info = f"番号地址: {real_url} "
            LogBuffer.info().write(web_info + debug_info)
        else:
            query = target_number or extract_slug(appoint_url)
            if appoint_url and "/search/" in appoint_url:
                search_url = appoint_url
            else:
                search_url = f"{jable_url}/search/{quote(query)}/"
            search_url = with_lang_zh(search_url)
            debug_info = f"搜索地址: {search_url} "
            LogBuffer.info().write(web_info + debug_info)
            html_search, error, final_search_url = await fetch_text(search_url, referer=jable_url + "/")
            if html_search is None:
                raise Exception(f"网络请求错误: {error}")
            html = etree.fromstring(html_search, etree.HTMLParser())
            candidates = extract_search_candidates(html, final_search_url)
            if not candidates:
                raise Exception("没有匹配的搜索结果")
            for candidate in candidates[:3]:
                slug_number = slug_to_number(extract_slug(candidate))
                if normalized_target and normalize_code(slug_number) == normalized_target:
                    real_url = candidate
                    break
            if not real_url:
                raise Exception(f"没有匹配到番号: {target_number}")

        real_url = with_lang_zh(real_url)
        html_detail, error, final_detail_url = await fetch_text(real_url, referer=jable_url + "/")
        if html_detail is None:
            raise Exception(f"网络请求错误: {error}")

        data = parse_detail_page(html_detail, final_detail_url)
        detail_number = data.get("number", "")
        detail_number_normalized = normalize_code(detail_number)
        trust_manual_detail = bool(appoint_url and "/videos/" in appoint_url)
        if not trust_manual_detail and normalized_target and detail_number_normalized != normalized_target:
            raise Exception(f"详情页番号不匹配: {detail_number}")
        if trust_manual_detail and not detail_number:
            data["number"] = target_number

        debug_info = "数据获取成功！"
        LogBuffer.info().write(web_info + debug_info)
        dic = {website_name: {"zh_cn": data, "zh_tw": data, "jp": data}}

    except Exception as e:
        LogBuffer.error().write(str(e))
        dic = {website_name: {"zh_cn": {"title": "", "thumb": "", "website": ""}, "zh_tw": {"title": "", "thumb": "", "website": ""}, "jp": {"title": "", "thumb": "", "website": ""}}}

    LogBuffer.req().write(f"({round(time.time() - start_time)}s) ")
    return dic

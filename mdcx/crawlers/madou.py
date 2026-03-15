#!/usr/bin/env python3
import json
import re
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from lxml import etree

from ..config.enums import Website
from ..config.manager import manager
from ..models.enums import FileMode
from ..models.flags import Flags
from ..models.log_buffer import LogBuffer
from ..web_async import normalize_image_bytes


def get_actor_photo(actor: str):
    data = {}
    for name in [w.strip() for w in actor.split(",") if w.strip()]:
        data[name] = ""
    return data


def normalize_code(text: str) -> str:
    return re.sub(r"[\s._-]+", "", (text or "").upper())


def normalize_release_date(value: str) -> tuple[str, str]:
    value = (value or "").strip().replace("/", "-").replace(".", "-")
    if not value:
        return "", ""
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if not match:
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})", value)
    if not match:
        year = re.search(r"\d{4}", value)
        return "", year.group() if year else ""
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    return f"{year}-{month:02d}-{day:02d}", year


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


def parse_page_data(html_content: str) -> dict:
    match = re.search(r"var\s+dataJson\s*=\s*'(.*?)';", html_content, re.S)
    if not match:
        return {}
    try:
        json_text = json.loads(f'"{match.group(1)}"')
        return json.loads(json_text).get("data", {})
    except Exception:
        return {}


def extract_search_query(url: str) -> str:
    match = re.search(r"/searchvideo/([^/?#]+)/?", url)
    return unquote(match.group(1)).strip() if match else ""


def extract_search_candidates(html, base_url: str) -> list[str]:
    candidates = []
    seen = set()
    for node in html.xpath('//a[contains(@href, "/archives/")]'):
        href = (node.get("href") or "").strip()
        if not href:
            continue
        full_url = urljoin(base_url.rstrip("/") + "/", href)
        if not re.search(r"/archives/\d+/?(?:[?#].*)?$", full_url):
            continue
        if full_url.rstrip("/").endswith("/0"):
            continue
        dedup_key = re.sub(r"[?#].*$", "", full_url).rstrip("/")
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        candidates.append(full_url)
        if len(candidates) >= 3:
            break
    return candidates


def find_labeled_value(html, keyword: str) -> str:
    for node in html.xpath('//div[contains(@class, "vd-infos")]/p'):
        text = "".join(node.xpath(".//text()")).strip()
        if keyword in text:
            parts = re.split(r"[：:]", text, maxsplit=1)
            if len(parts) == 2:
                return parts[1].strip()
    return ""


def extract_actors(html) -> list[str]:
    actors = []
    for node in html.xpath('//div[@id="related-gls"]//a[contains(@class, "related-gls__item")]//h5'):
        name = "".join(node.xpath("./text()")).strip()
        if name and name not in actors:
            actors.append(name)
    return actors


def extract_title(html, page_data: dict) -> str:
    video_data = page_data.get("video", {}) if isinstance(page_data, dict) else {}
    title = (video_data.get("show_title") or video_data.get("title") or "").strip()
    if title:
        return title
    result = "".join(html.xpath('//h2[contains(@class, "vdetail-title")][1]/text()')).strip()
    if not result:
        result = "".join(html.xpath('//div[contains(@class, "vdetail-intro")]//h1[1]/text()')).strip()
    if result:
        return result
    title_tag = "".join(html.xpath("//title/text()")).strip()
    return re.sub(r"\s*[|｜]\s*麻豆传媒.*$", "", title_tag).strip()


def extract_tags(html, page_data: dict) -> list[str]:
    video_data = page_data.get("video", {}) if isinstance(page_data, dict) else {}
    tags = [str(tag.get("name", "")).strip() for tag in video_data.get("tags", []) if str(tag.get("name", "")).strip()]
    if tags:
        return list(dict.fromkeys(tags))
    tags = [w.strip() for w in html.xpath('//div[contains(@class, "vd-tags")]//span[contains(@class, "tags")]/a/text()') if w.strip()]
    return list(dict.fromkeys(tags))


def extract_outline(html, page_data: dict) -> str:
    outline = "".join(html.xpath('//p[contains(@class, "vd-infos__desc")]//text()')).strip()
    if outline:
        return re.sub(r"^影片简介[：:]\s*", "", outline).strip()
    video_data = page_data.get("video", {}) if isinstance(page_data, dict) else {}
    return str(video_data.get("desc") or "").strip()


def extract_cover_candidates(page_data: dict) -> list[str]:
    video_data = page_data.get("video", {}) if isinstance(page_data, dict) else {}
    nested_video = video_data.get("video", {}) if isinstance(video_data, dict) else {}
    result = []
    # 页面展示封面优先使用 coverImg，其次使用移动端封面；播放器 cover 更像首帧预览图，最后再兜底。
    for key in (video_data.get("coverImg"), video_data.get("bannerImageMobile"), nested_video.get("cover")):
        if not isinstance(key, str):
            continue
        key = key.strip().replace("\\/", "/")
        if key and key not in result:
            result.append(key)
    return result


def extract_canonical_url(html, fallback_url: str) -> str:
    canonical = "".join(html.xpath('//link[@rel="canonical"]/@href')).strip()
    return canonical or fallback_url


def extract_series(number: str) -> str:
    number = (number or "").strip().upper()
    if not number:
        return ""
    head = re.split(r"[-_.\s]+", number, maxsplit=1)[0].strip()
    if head and not head.isdigit():
        return head
    match = re.match(r"^([A-Z0-9]+?)(?=\d{3,})", number)
    return match.group(1) if match else head


def trim_title_after_actor(full_title: str, actors: list[str]) -> str:
    full_title = (full_title or "").strip()
    last_end = -1
    for actor in actors:
        actor = actor.strip()
        if not actor:
            continue
        pos = full_title.rfind(actor)
        if pos >= 0:
            last_end = max(last_end, pos + len(actor))
    if last_end < 0:
        return full_title
    trimmed = full_title[last_end:].strip(" .-_/:|")
    return trimmed or full_title


def parse_detail_page(html_content: str, detail_url: str) -> dict:
    html = etree.fromstring(html_content, etree.HTMLParser())
    page_data = parse_page_data(html_content)

    number = find_labeled_value(html, "番号")
    release_text = find_labeled_value(html, "发行日期")
    release, year = normalize_release_date(release_text)
    publisher = find_labeled_value(html, "发行商")
    actors = extract_actors(html)
    full_title = extract_title(html, page_data)
    title = trim_title_after_actor(full_title, actors)
    outline = extract_outline(html, page_data)
    tags = extract_tags(html, page_data)
    canonical_url = extract_canonical_url(html, detail_url)
    cover_candidates = extract_cover_candidates(page_data)

    return {
        "number": number,
        "title": title,
        "originaltitle": title,
        "full_title": full_title,
        "actor": ",".join(actors),
        "all_actor": ",".join(actors),
        "outline": outline,
        "originalplot": outline,
        "tag": ",".join(tags),
        "release": release,
        "year": year,
        "series": extract_series(number),
        "studio": publisher,
        "publisher": publisher,
        "website": canonical_url,
        "thumb": "",
        "thumb_candidates": cover_candidates,
    }


async def get_valid_cover_url(candidates: list[str], *, referer: str = "") -> str:
    for url in candidates:
        content, error = await manager.computed.async_client.get_content(url, headers=get_browser_headers(referer))
        if content is None:
            continue
        if normalize_image_bytes(url, content) is not None:
            return url
    return ""


def build_result(website_name: str, data: dict) -> dict:
    actor = data.get("actor", "")
    dic = {
        "number": data.get("number", ""),
        "title": data.get("title", ""),
        "originaltitle": data.get("originaltitle", ""),
        "actor": actor,
        "all_actor": data.get("all_actor", actor),
        "outline": data.get("outline", ""),
        "originalplot": data.get("originalplot", ""),
        "tag": data.get("tag", ""),
        "release": data.get("release", ""),
        "year": data.get("year", ""),
        "runtime": "",
        "score": "",
        "series": data.get("series", ""),
        "country": "CN",
        "director": "",
        "studio": data.get("studio", ""),
        "publisher": data.get("publisher", ""),
        "source": website_name,
        "website": data.get("website", ""),
        "actor_photo": get_actor_photo(actor),
        "thumb": data.get("thumb", ""),
        "poster": "",
        "extrafanart": [],
        "trailer": "",
        "image_download": False,
        "image_cut": "no",
        "mosaic": "国产",
        "wanted": "",
    }
    return {website_name: {"zh_cn": dic, "zh_tw": dic, "jp": dic}}


async def main(number, appoint_url="", file_path="", appoint_number="", **kwargs):
    del file_path, kwargs
    start_time = time.time()
    website_name = "madou"
    LogBuffer.req().write(f"-> {website_name}")
    LogBuffer.info().write(" \n    🌐 madou")
    web_info = "\n       "

    madou_url = manager.config.get_site_url(Website.MADOU, "https://madou.com")
    target_number = (appoint_number or number or "").strip()
    real_url = appoint_url.strip()
    trust_manual_detail_url = Flags.file_mode == FileMode.Single and bool(real_url and "/archives/" in real_url)
    dic = {"title": "", "thumb": "", "website": ""}

    try:
        if real_url and "/archives/" in real_url:
            LogBuffer.info().write(web_info + f"番号地址: {real_url}")
            html_content, error, fetch_url = await fetch_text(real_url, referer=madou_url.rstrip("/") + "/")
            if html_content is None:
                raise Exception(f"网络请求错误: {error}")
            detail = parse_detail_page(html_content, fetch_url)
            detail_number = detail.get("number", "")
            if (
                not trust_manual_detail_url
                and target_number
                and detail_number
                and normalize_code(detail_number) != normalize_code(target_number)
            ):
                raise Exception(f"详情页番号不匹配: {detail_number}")
            if not detail_number:
                if target_number and not trust_manual_detail_url:
                    raise Exception("详情页未找到番号")
                detail["number"] = target_number
                detail["series"] = extract_series(target_number)
            detail["thumb"] = await get_valid_cover_url(detail.get("thumb_candidates", []), referer=fetch_url)
            if not detail["thumb"] and detail.get("thumb_candidates"):
                LogBuffer.info().write(web_info + "封面源无有效图片，已跳过图片下载")
            LogBuffer.info().write(web_info + "数据获取成功！")
            dic = build_result(website_name, detail)
            LogBuffer.req().write(f"({round(time.time() - start_time)}s) ")
            return dic

        query_number = target_number or extract_search_query(real_url)
        if not query_number:
            raise Exception("madou 搜索缺少番号")

        search_url = real_url or f"{madou_url}/searchvideo/{quote(query_number, safe='')}/"
        LogBuffer.info().write(web_info + f"搜索地址: {search_url}")
        search_html_content, error, fetch_search_url = await fetch_text(search_url, referer=madou_url.rstrip("/") + "/")
        if search_html_content is None:
            raise Exception(f"网络请求错误: {error}")
        search_page = etree.fromstring(search_html_content, etree.HTMLParser())
        candidates = extract_search_candidates(search_page, fetch_search_url)
        if not candidates:
            raise Exception("没有匹配的搜索结果")

        normalized_target = normalize_code(query_number)
        last_error = ""
        for index, detail_url in enumerate(candidates[:3], start=1):
            LogBuffer.info().write(web_info + f"候选详情[{index}]: {detail_url}")
            detail_html_content, detail_error, fetch_detail_url = await fetch_text(detail_url, referer=fetch_search_url)
            if detail_html_content is None:
                last_error = detail_error
                continue
            detail = parse_detail_page(detail_html_content, fetch_detail_url)
            detail_number = detail.get("number", "")
            if not detail_number:
                last_error = "详情页未找到番号"
                continue
            if normalize_code(detail_number) != normalized_target:
                last_error = f"详情页番号不匹配: {detail_number}"
                continue
            detail["thumb"] = await get_valid_cover_url(detail.get("thumb_candidates", []), referer=fetch_detail_url)
            if not detail["thumb"] and detail.get("thumb_candidates"):
                LogBuffer.info().write(web_info + "封面源无有效图片，已跳过图片下载")
            LogBuffer.info().write(web_info + "数据获取成功！")
            dic = build_result(website_name, detail)
            LogBuffer.req().write(f"({round(time.time() - start_time)}s) ")
            return dic

        raise Exception(last_error or "前 3 个候选详情页均未匹配番号")

    except Exception as e:
        LogBuffer.error().write(str(e))

    LogBuffer.req().write(f"({round(time.time() - start_time)}s) ")
    return {website_name: {"zh_cn": dic, "zh_tw": dic, "jp": dic}}

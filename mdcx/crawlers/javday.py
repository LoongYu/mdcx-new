#!/usr/bin/env python3
import re
import time
from urllib.parse import quote, urljoin

from lxml import etree

from ..config.enums import Website
from ..config.manager import manager
from ..models.log_buffer import LogBuffer
from .guochan import get_actor_list, get_lable_list, get_number_list


def get_actor_photo(actor):
    actor = actor.split(",")
    data = {}
    for i in actor:
        actor_photo = {i: ""}
        data.update(actor_photo)
    return data


def get_title(html):
    result = html.xpath('//*[@id="videoInfo"]/div/h1')
    return result[0].text if result else ""


def get_some_info(html, title, file_path):
    series_list = html.xpath('//*[@id="videoInfo"]/div/div/p[3]/span[2]/a/text()')
    tag_list = html.xpath('//*[@id="videoInfo"]/div/div/p[1]/span[2]/a/text()')
    actor_list = html.xpath('//*[@id="videoInfo"]/div/div/p[1]/span[2]/a/text()')

    # 未找到演员时，看热门演员是否在标题和各种信息里
    series = series_list[0] if series_list else ""
    tag = ",".join(tag_list)
    actor_fake_name = any("未知" in item for item in actor_list)
    actor_list = [] if actor_fake_name else actor_list
    if not actor_list:
        all_info = title + series + tag + file_path
        all_actor = get_actor_list()
        for each in all_actor:
            if each in all_info:
                actor_list.append(each)
    new_actor_list = []
    [new_actor_list.append(i) for i in actor_list if i and i not in new_actor_list]

    # # 去除标签里的演员
    # for each in actor_list:
    #     if each in tag_list:
    #         tag_list.remove(each)
    # new_tag_list = []
    # [new_tag_list.append(i) for i in tag_list if i and i not in new_tag_list]

    return series, ",".join(tag_list), ",".join(new_actor_list)


def get_studio(series, tag, lable_list):
    word_list = [series]
    word_list.extend(tag.split(","))
    for word in word_list:
        if word in lable_list:
            return word
    return ""


# def get_real_url(html, number, javday_url, file_path,):
#     real_url = ''
#     a = re.search(r'(\d*[A-Z]{2,})\s*(\d{3,})', number)
#     real_number = number
#     if a:
#         real_number = a[1] + '-' + a[2]
#     result = html.xpath('//h4[@class="post-title"]')
#     cd = re.findall(r'((AV|EP)\d{1})', file_path.upper())
#     for each in result:
#         title = each.xpath('a/@title')[0].upper()
#         href = each.xpath('a/@href')[0]
#         title_1 = title.replace('.', '').replace('-', '').replace(' ', '')
#         number_1 = number.replace('.', '').replace('-', '').replace(' ', '')
#         if number in title or real_number in title or number_1 in title_1:
#             real_url = javday_url + href
#             if cd:
#                 if cd[0][0] in title_1.upper():
#                     break
#             else:
#                 break
#     return real_url


def get_cover(html, javday_url):
    result = html.xpath(
        '//meta[@property="og:image"]/@content | //meta[@name="og:image"]/@content | //meta[@name="twitter:image"]/@content'
    )
    if not result:
        result = html.xpath("/html/head/meta[8]/@content")
    if result:
        result = result[0]
        if result and "http" not in result:
            result = urljoin(javday_url + "/", result)
    return result if result else ""


def get_tag(html):  # 获取演员
    result = html.xpath('//div[@class="category"]/a[contains(@href, "/class/")]/text()')
    return ",".join(result)


def _normalize_release_date(value: str) -> tuple[str, str]:
    """
    将站点时间文本归一化为 release(YYYY-MM-DD) 和 year(YYYY)。
    """
    value = (value or "").strip()
    if not value:
        return "", ""
    value = value.replace("/", "-").replace(".", "-")
    m = re.search(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})", value)
    if not m:
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if not m:
        y = re.search(r"\d{4}", value)
        year = y.group() if y else ""
        return "", year
    year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{year}-{month:02d}-{day:02d}", year


def get_release_from_upload_time(html, html_content: str) -> tuple[str, str]:
    """
    优先从「上傳時間/发布时间」提取发布日期。
    """
    # 1) 页面字段: <p class="list-item"><span>上傳時間：</span><span>2026-01-29 23:40:06</span></p>
    pairs = html.xpath('//p[contains(@class,"list-item")]')
    for p in pairs:
        key = "".join(p.xpath("./span[1]//text()")).strip()
        if any(x in key for x in ["上傳時間", "上传时间", "發佈時間", "发布时间", "上架時間", "上架时间"]):
            value = "".join(p.xpath("./span[last()]//text()")).strip()
            release, year = _normalize_release_date(value)
            if release or year:
                return release, year

    # 2) meta
    meta_values = html.xpath(
        '//meta[@property="article:published_time"]/@content | '
        '//meta[@name="article:published_time"]/@content | '
        '//meta[@property="og:published_time"]/@content | '
        '//meta[@name="og:published_time"]/@content'
    )
    for value in meta_values:
        release, year = _normalize_release_date(value)
        if release or year:
            return release, year

    # 3) JSON-LD
    scripts = html.xpath('//script[@type="application/ld+json"]/text()')
    for text in scripts:
        if not text:
            continue
        m = re.search(r'"(?:datePublished|uploadDate)"\s*:\s*"([^"]+)"', text)
        if m:
            release, year = _normalize_release_date(m.group(1))
            if release or year:
                return release, year

    # 4) 原始 HTML 兜底（包含注释区）
    if html_content:
        m = re.search(
            r"(?:上傳時間|上传时间|發佈時間|发布时间)[^0-9]{0,30}"
            r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}:\d{2})?)",
            html_content,
        )
        if m:
            release, year = _normalize_release_date(m.group(1))
            if release or year:
                return release, year

    return "", ""


def get_real_number_title(
    number,
    title,
    number_list,
    appoint_number,
    appoint_url,
    lable_list,
    tag,
    actor,
    series,
):
    # 指定番号时，使用指定番号
    if appoint_number:
        number = appoint_number
        temp_title = title.replace(number, "")
        if len(temp_title) > 4:
            title = temp_title
    else:
        # 当从文件名没有获取番号时或者指定网址时，尝试从标题里获取番号
        if number not in number_list or appoint_url:
            title_number_list, filename_list = get_number_list(number, appoint_number, title)
            if title_number_list:
                number = title_number_list[0]
                number_list = title_number_list

        # 从文件名或标题中获取到番号时，对番号进行处理：番号里面没有横线时加横线
        if number in number_list:
            if number != title:
                title = title.replace(number, "").replace(number.lower(), "")
            if "-" not in number:
                if re.search(r"[A-Z]{4,}\d{2,}", number):
                    result = re.search(r"([A-Z]{4,})(\d{2,})", number)
                    number = result[1] + "-" + result[2]
                else:
                    result = re.search(r"\d{3,}", number)
                    if result:
                        number = number.replace(result[0], "-" + result[0])
            if number != title:
                title = title.replace(number, "")
        # 否则使用标题作为番号
        else:
            number = title
    temp_title = get_real_title(title, number_list, lable_list, tag, actor, series)
    if number == title:
        number = temp_title

    # 添加分集标识
    cd = re.findall(r"((AV|EP)\d{1})", title.upper())
    if cd and cd[0][0] not in number:
        number = number + " " + cd[0][0]

    return number, temp_title


def get_real_title(
    title,
    number_list,
    lable_list,
    tag,
    actor,
    series,
):
    # 去除标题里的番号
    for number in number_list:
        title = title.replace(number, "")

    # 去除标题后的发行商
    title_list = re.split("[. ]", title)
    if len(title_list) > 1:
        for key in lable_list:
            for each in title_list:
                if key in each:
                    title_list.remove(each)
        if title_list[-1].lower() == "x":
            title_list.pop()
        title = " ".join(title_list)
    for each in tag.split(","):
        if each:
            title = title.replace("" + each, "")
    for each in actor.split(","):
        if each:
            title = title.replace(" " + each, "")
    title = title.lstrip(series + " ").replace("..", ".").replace("  ", " ")

    return title.replace(" x ", "").replace(" X ", "").strip(" -.")


def _norm_text(text: str) -> str:
    return re.sub(r"[\s._-]+", "", text.upper())


def _norm_code(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (text or "").upper())


def is_cloudflare_blocked(html: str) -> bool:
    html_lower = (html or "").lower()
    return (
        ("just a moment" in html_lower and ("cf_chl_opt" in html_lower or "cloudflare" in html_lower))
        or "attention required" in html_lower
        or "checking your browser before accessing" in html_lower
    )


async def fetch_text_with_fallback(url: str, *, referer: str = "") -> tuple[str | None, str]:
    """
    获取网页文本：
    1) 默认请求
    2) 直连（仅启用代理时）
    3) 带浏览器风格请求头
    4) 直连 + 浏览器风格请求头（仅启用代理时）
    """
    headers_like_browser = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers_like_browser["Referer"] = referer

    plans: list[tuple[bool, dict[str, str] | None, str]] = [
        (True, None, "默认请求"),
    ]
    if manager.config.use_proxy:
        plans.append((False, None, "直连请求"))
    plans.append((True, headers_like_browser, "浏览器头请求"))
    if manager.config.use_proxy:
        plans.append((False, headers_like_browser, "直连+浏览器头请求"))

    last_error = ""
    for use_proxy, headers, plan_name in plans:
        html, error = await manager.computed.async_client.get_text(url, headers=headers, use_proxy=use_proxy)
        if html is None:
            if error:
                last_error = f"{error} [{plan_name}]"
            continue
        if is_cloudflare_blocked(html):
            last_error = f"触发 Cloudflare 验证 [{plan_name}]"
            continue
        return html, ""

    return None, last_error or "请求失败"


def get_detail_slugs(number: str) -> list[str]:
    """
    生成 /videos/{slug}/ 的候选 slug.
    javday 对部分番号要求去掉分隔符（如 MD0356、MDSR00131）。
    """
    raw = str(number or "").strip().upper()
    if not raw:
        return []
    slugs: list[str] = []

    def add(v: str):
        v = v.strip().strip("/")
        if v and v not in slugs:
            slugs.append(v)

    # 原样（统一 _ 与空格）
    normalized = re.sub(r"[\s_]+", "-", raw)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    add(normalized)

    # 去掉分隔符（命中率更高）
    compact = _norm_code(raw)
    add(compact)

    # 兜底：去掉空白和下划线但保留中划线
    add(raw.replace("_", "-").replace(" ", ""))

    return slugs


def get_real_url(search_html: str, search_number: str, file_number: str, javday_url: str) -> str:
    """从搜索页中提取最可能的详情页链接."""
    html = etree.fromstring(search_html, etree.HTMLParser())
    link_nodes = html.xpath(
        '//h4[contains(@class,"post-title")]/a | '
        '//h3[contains(@class,"post-title")]/a | '
        '//h2[contains(@class,"post-title")]/a | '
        '//h2[contains(@class,"entry-title")]/a | '
        '//a[contains(@href,"/videos/")]'
    )
    if not link_nodes:
        return ""

    # 候选匹配词：当前搜索词 + 原始番号
    wanted = []
    for raw in (search_number, file_number):
        raw = (raw or "").strip()
        if not raw:
            continue
        wanted.append(raw)
        if m := re.search(r"(\d*[A-Z]{2,})\s*-?\s*(\d{2,})(?:\s*-\s*(\d+))?", raw.upper()):
            part = f"{m[1]}-{m[2]}"
            if m[3]:
                part = f"{part}-{m[3]}"
            wanted.append(part)
    wanted_norm = [_norm_text(w) for w in wanted if w]

    links = []
    for node in link_nodes:
        href = (node.get("href") or "").strip()
        if not href:
            continue
        title = (node.get("title") or "".join(node.xpath(".//text()")) or "").strip()
        abs_url = urljoin(javday_url + "/", href)
        links.append((abs_url, title))

    # 去重保序
    unique_links = []
    seen = set()
    for url, title in links:
        if url in seen:
            continue
        seen.add(url)
        unique_links.append((url, title))

    # 优先命中标题包含目标番号的结果
    for url, title in unique_links:
        title_norm = _norm_text(title)
        if any(w and w in title_norm for w in wanted_norm):
            return url

    # 兜底：第一个 /videos/ 链接
    for url, _ in unique_links:
        if "/videos/" in url:
            return url
    return unique_links[0][0] if unique_links else ""


def get_search_candidates(number, number_list, filename_list):
    """
    生成 javday /videos/{id}/ 的候选 ID 列表。
    优先使用番号候选；仅在番号候选为空时才回退文件名候选，避免误用路径分词导致超时。
    """
    # 优先使用外部识别到的番号（避免被文件名噪声词抢到前面）
    candidates = []
    if number:
        candidates.append(number)

    # 当存在明确番号时，仅保留与其“编码相近”的候选，过滤掉如 CCTV666 这类来源于域名的噪声
    norm_number = _norm_code(number) if number else ""
    for n in number_list:
        n = str(n or "").strip()
        if not n:
            continue
        norm_n = _norm_code(n)
        if norm_number and not (norm_number in norm_n or norm_n in norm_number):
            continue
        if n not in candidates:
            candidates.append(n)
    # 从文件名候选中提取“像番号”的片段，补充搜索项（避免纯标题噪声）。
    for each in filename_list:
        each = str(each).upper()
        if not each:
            continue
        # 例如: MNSC-MB-133 / MDSR-0013-1 / MD-0240
        found = re.findall(r"\b([A-Z]{2,6}(?:-[A-Z]{1,4})?-\d{2,5}(?:-\d{1,2})?)\b", each)
        for item in found:
            if item not in candidates:
                candidates.append(item)
    if not candidates:
        candidates = [n for n in filename_list if n]

    result = []
    for each in candidates:
        each = str(each).strip()
        if not each:
            continue
        # 去除明显不是番号的路径片段
        if "\\" in each or "/" in each:
            continue
        # javday 的 videos 路径对空格不稳定，优先使用连字符版本
        normalized = each.replace("_", "-")
        normalized = re.sub(r"\s+", "-", normalized)
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        if normalized and normalized not in result:
            result.append(normalized)
        if each not in result and len(each) <= 60:
            result.append(each)
    return result


async def main(
    number,
    appoint_url="",
    file_path="",
    appoint_number="",
    **kwargs,
):
    lable_list = get_lable_list()
    start_time = time.time()
    website_name = "javday"
    LogBuffer.req().write(f"-> {website_name}")
    web_info = "\n       "
    LogBuffer.info().write(" \n    🌐 javday")
    debug_info = ""

    javday_url = manager.config.get_site_url(Website.JAVDAY, "https://javday.app")
    real_url = appoint_url
    real_html_content = ""
    try:
        # 处理番号
        number_list, filename_list = get_number_list(number, appoint_number, file_path)
        if real_url:
            debug_info = f"指定网址: {real_url}"
            LogBuffer.info().write(web_info + debug_info)
            real_html_content, error = await fetch_text_with_fallback(real_url, referer=javday_url.rstrip("/") + "/")
            if real_html_content is None:
                debug_info = f"网络请求错误: {error}"
                LogBuffer.info().write(web_info + debug_info)
                raise Exception(debug_info)
        else:
            search_list = get_search_candidates(number, number_list, filename_list)
            for search_number in search_list:
                # 优先详情直连（当前环境下 search 容易被 Cloudflare 403）
                detail_ok = False
                for detail_slug in get_detail_slugs(search_number):
                    detail_try_url = javday_url.rstrip("/") + f"/videos/{quote(detail_slug, safe='')}/"
                    debug_info = f"详情直连尝试: {detail_try_url}"
                    LogBuffer.info().write(web_info + debug_info)
                    html_content, detail_error = await fetch_text_with_fallback(
                        detail_try_url, referer=javday_url.rstrip("/") + "/"
                    )
                    if html_content is None:
                        debug_info = f"网络请求错误: {detail_error}"
                        LogBuffer.info().write(web_info + debug_info)
                        continue
                    if "你似乎來到了沒有視頻存在的荒原" in html_content:
                        debug_info = f"找不到番号: {detail_slug}"
                        LogBuffer.info().write(web_info + debug_info)
                        continue
                    debug_info = f"详情直连成功: {detail_try_url}"
                    LogBuffer.info().write(web_info + debug_info)
                    real_url = detail_try_url
                    real_html_content = html_content
                    detail_ok = True
                    break

                if detail_ok:
                    break

                search_url = javday_url.rstrip("/") + f"/search/?wd={quote(search_number, safe='')}"
                debug_info = f'搜索地址: {search_url} {{"wd": {search_number}}}'
                LogBuffer.info().write(web_info + debug_info)
                search_html, error = await fetch_text_with_fallback(search_url, referer=javday_url.rstrip("/") + "/")
                if search_html is None:
                    # 兼容: search 被 Cloudflare 拦截时，回退到详情页直连尝试
                    detail_try_url = javday_url.rstrip("/") + f"/videos/{search_number}/"
                    debug_info = f"网络请求错误: {error}，尝试详情直连: {detail_try_url}"
                    LogBuffer.info().write(web_info + debug_info)
                    html_content, detail_error = await fetch_text_with_fallback(detail_try_url, referer=search_url)
                    if html_content is None:
                        debug_info = f"网络请求错误: {detail_error}"
                        LogBuffer.info().write(web_info + debug_info)
                        continue
                    if "你似乎來到了沒有視頻存在的荒原" in html_content:
                        debug_info = f"找不到番号: {search_number}"
                        LogBuffer.info().write(web_info + debug_info)
                        continue
                    debug_info = f"搜索受限，详情直连成功: {detail_try_url}"
                    LogBuffer.info().write(web_info + debug_info)
                    real_url = detail_try_url
                    real_html_content = html_content
                    break

                detail_url = get_real_url(search_html, search_number, number, javday_url)
                if not detail_url:
                    debug_info = f"找不到番号: {search_number}"
                    LogBuffer.info().write(web_info + debug_info)
                    continue
                html_content, error = await fetch_text_with_fallback(detail_url, referer=search_url)
                if html_content is None:
                    debug_info = f"网络请求错误: {error}"
                    LogBuffer.info().write(web_info + debug_info)
                else:
                    if "你似乎來到了沒有視頻存在的荒原" in html_content:
                        debug_info = f"找不到番号: {search_number}"
                        LogBuffer.info().write(web_info + debug_info)
                        continue
                    debug_info = f"找到网页: {detail_url}"
                    real_url = detail_url
                    real_html_content = html_content
                    break
            else:
                raise Exception(debug_info)

        if real_url:
            html_info = etree.fromstring(real_html_content, etree.HTMLParser())
            title = get_title(html_info)  # 获取标题
            if not title:
                debug_info = "数据获取失败: 未获取到title！"
                LogBuffer.info().write(web_info + debug_info)
                raise Exception(debug_info)
            series, tag, actor = get_some_info(html_info, title, file_path)
            actor_photo = get_actor_photo(actor)
            cover_url = get_cover(html_info, javday_url)  # 获取cover
            release, year = get_release_from_upload_time(html_info, real_html_content)
            studio = get_studio(series, tag, lable_list)
            number, title = get_real_number_title(
                number, title, number_list, appoint_number, appoint_url, lable_list, tag, actor, series
            )

            try:
                dic = {
                    "number": number,
                    "title": title,
                    "originaltitle": title,
                    "actor": actor,
                    "outline": "",
                    "originalplot": "",
                    "tag": tag,
                    "release": release,
                    "year": year,
                    "runtime": "",
                    "score": "",
                    "series": series,
                    "country": "CN",
                    "director": "",
                    "studio": studio,
                    "publisher": studio,
                    "source": "javday",
                    "website": real_url,
                    "actor_photo": actor_photo,
                    "thumb": cover_url,
                    "poster": "",
                    "extrafanart": [],
                    "trailer": "",
                    "image_download": False,
                    "image_cut": "no",
                    "mosaic": "国产",
                    "wanted": "",
                }
                debug_info = "数据获取成功！"
                LogBuffer.info().write(web_info + debug_info)

            except Exception as e:
                debug_info = f"数据生成出错: {str(e)}"
                LogBuffer.info().write(web_info + debug_info)
                raise Exception(debug_info)

    except Exception as e:
        # print(traceback.format_exc())
        LogBuffer.error().write(str(e))
        dic = {
            "title": "",
            "thumb": "",
            "website": "",
        }
    dic = {website_name: {"zh_cn": dic, "zh_tw": dic, "jp": dic}}
    LogBuffer.req().write(f"({round(time.time() - start_time)}s) ")
    return dic


if __name__ == "__main__":
    # yapf: disable
    # print(main('Md0165-4'))
    # print(main('GDCM-018'))
    # print(main('MKY-JB-010'))
    # print(main('PMC-085', file_path='PMC/PMC-085.雪霏.出差借宿小姨子乱伦姐夫.特别照顾的肉体答谢.蜜桃影像传媒.ts'))
    # print(main('TM-0165', file_path='TM0165.王小妮.妈妈的性奴之路.性感少妇被儿子和同学调教成性奴.天美传媒'))
    # print(main('mini06.全裸家政.只為弟弟的學費打工.被玩弄的淫亂家政小妹.mini傳媒'))
    # print(main('mini06', file_path='mini06.全裸家政.只為弟弟的學費打工.被玩弄的淫亂家政小妹.mini傳媒'))
    # print(main('mini06.全裸家政.只为弟弟的学费打工.被玩弄的淫乱家政小妹.mini传媒'))
    # print(main('', file_path='夏日回忆 贰'))
    # print(main('MDX-0016'))
    # print(main('MDSJ-0004'))
    # print(main('RS-020'))
    # print(main('PME-018.雪霏.禽兽小叔迷奸大嫂.性感身材任我玩弄.蜜桃影像传媒', file_path='PME-018.雪霏.禽兽小叔迷奸大嫂.性感身材任我玩弄.蜜桃影像传媒'))
    # print(main('老公在外出差家里的娇妻被入室小偷强迫性交 - 美酱'))
    # print(main('', file_path='夏日回忆 贰 HongKongDoll玩偶姐姐.短篇集.夏日回忆 贰.Summer Memories.Part 2.mp4'))
    # print(main('', file_path='HongKongDoll玩偶姐姐.短篇集.夏日回忆 贰.Summer Memories.Part 2.mp4'))
    # print(main('', file_path="【HongKongDoll玩偶姐姐.短篇集.情人节特辑.Valentine's Day Special-cd2"))
    # print(main('', file_path='PMC-062 唐茜.綠帽丈夫連同新弟怒操出軌老婆.強拍淫蕩老婆被操 唐茜.ts'))
    # print(main('', file_path='MKY-HS-004.周寗.催情民宿.偷下春药3P干爆夫妇.麻豆传媒映画'))
    # print(main('淫欲游戏王.EP6', appoint_number='淫欲游戏王.EP5', file_path='淫欲游戏王.EP6.情欲射龙门.性爱篇.郭童童.李娜.双英战龙根3P混战.麻豆传媒映画.ts')) # EP不带.才能搜到
    # print(main('', file_path='PMS-003.职场冰与火.EP3设局.宁静.苏文文.设局我要女人都臣服在我胯下.蜜桃影像传媒'))
    # print(main('', file_path='PMS-001 性爱公寓EP04 仨人.蜜桃影像传媒.ts'))
    # print(main('', file_path='PMS-001.性爱公寓EP03.ts'))
    # print(main('', file_path='MDX-0236-02.沈娜娜.青梅竹马淫乱3P.麻豆传媒映画x逼哩逼哩blibli.ts'))
    # print(main('', file_path='淫欲游戏王.EP6.情欲射龙门.性爱篇.郭童童.李娜.双英战龙根3P混战.麻豆传媒映画.ts'))
    # print(main('', file_path='麻豆傳媒映畫原版 兔子先生 我的女友是女優 女友是AV女優是怎樣的體驗-美雪樱'))   # 简体搜不到
    # print(main('', file_path='麻豆傳媒映畫原版 兔子先生 拉麵店搭訕超可愛少女下-柚木结爱.TS'))
    # '麻豆傳媒映畫原版 兔子先生 拉麵店搭訕超可愛少女下-柚木結愛', '麻豆傳媒映畫原版 兔子先生 拉麵店搭訕超可愛少女下-', ' 兔子先生 拉麵店搭訕超可愛少女下-柚木結愛']
    # print(main('', file_path='麻豆傳媒映畫原版 兔子先生 我的女友是女優 女友是AV女優是怎樣的體驗-美雪樱.TS'))
    # print(main('', file_path='PMS-001 性爱公寓EP02 女王 蜜桃影像传媒 -莉娜乔安.TS'))
    # print(main('91CM-081', file_path='91CM-081.田恬.李琼.继母与女儿.三.爸爸不在家先上妹妹再玩弄母亲.果冻传媒.mp4'))
    # print(main('91CM-081', file_path='MDJ-0001.EP3.陈美惠.淫兽寄宿家庭.我和日本父子淫乱的一天.麻豆传媒映画.mp4'))
    # print(main('91CM-081', file_path='MDJ0001 EP2  AV 淫兽鬼父 陈美惠  .TS'))
    # print(main('91CM-081', file_path='MXJ-0005.EP1.弥生美月.小恶魔高校生.与老师共度的放浪补课.麻豆传媒映画.TS'))
    # print(main('91CM-081', file_path='MKY-HS-004.周寗.催情民宿.偷下春药3P干爆夫妇.麻豆传媒映画.TS'))
    # print(main('91CM-081', file_path='PH-US-002.色控.音乐老师全裸诱惑.麻豆传媒映画.TS'))
    # print(main('91CM-081', file_path='MDX-0236-02.沈娜娜.青梅竹马淫乱3P.麻豆传媒映画x逼哩逼哩blibli.TS'))
    # print(main('91CM-081', file_path='MD-0140-2.蜜苏.家有性事EP2.爱在身边.麻豆传媒映画.TS'))
    # print(main('91CM-081', file_path='MDUS系列[中文字幕].LAX0025.性感尤物渴望激情猛操.RUCK ME LIKE A SEX DOLL.麻豆传媒映画.TS'))
    # print(main('91CM-081', file_path='REAL野性派001-朋友的女友讓我最上火.TS'))
    # print(main('91CM-081', file_path='MDS-009.张芸熙.巨乳旗袍诱惑.搔首弄姿色气满点.麻豆传媒映画.TS'))
    # print(main('91CM-081', file_path='MDS005 被雇主强上的熟女家政妇 大声呻吟被操到高潮 杜冰若.mp4.TS'))
    # print(main('91CM-081', file_path='TT-005.孟若羽.F罩杯性感巨乳DJ.麻豆出品x宫美娱乐.TS'))
    # print(main('91CM-081', file_path='台湾第一女优吴梦梦.OL误上痴汉地铁.惨遭多人轮番奸玩.麻豆传媒映画代理出品.TS'))
    # print(main('91CM-081', file_path='PsychoPorn色控.找来大奶姐姐帮我乳交.麻豆传媒映画.TS'))
    # print(main('91CM-081', file_path='鲍鱼游戏SquirtGame.吸舔碰糖.失败者屈辱凌辱.TS'))
    # print(main('91CM-081', file_path='导演系列 外卖员的色情体验 麻豆传媒映画.TS'))  # 标题去除系列
    # print(main('91CM-081', file_path='MDS007 骚逼女友在作妖-硬上男友当玩具 叶一涵.TS'))
    # print(main('', file_path='WTB-075 酒店妹包养软饭男 为了让他振作只好以身相许 乌托邦.ts'))    # 标题里有\t
    # print(main('', file_path='杏吧八戒1 - 3000约操18岁大一新生，苗条身材白嫩紧致.ts'))  # 分词匹配，带标点或者整个标题去匹配
    # print(main('', file_path='萝莉社 女大学生找模特兼职 被要求裸露拍摄 被套路内射.ts'))  # 分词匹配，带标点或者整个标题去匹配
    print(main('',
               file_path='/sp/sp6/国产测试/MD-0240 周處除三嗨.mp4'))  # print(main('MDM-002')) # 去掉标题最后的发行商  # print(main('MDS-0007')) # 数字要四位才能搜索到，即 MDS-0007 MDJ001 EP1 我的女优物语陈美惠.TS  # print(main('MDS-007', file_path='MDJ001 EP1 我的女优物语陈美惠.TS')) # 数字要四位才能搜索到，即 MDJ-0001.EP1  # print(main('91CM-090')) # 带横线才能搜到  # print(main('台湾SWAG chloebabe 剩蛋特辑 干爆小鹿'))   # 带空格才能搜到  # print(main('淫欲游戏王EP2'))  # 不带空格才能搜到  # print(main('台湾SWAG-chloebabe-剩蛋特輯-幹爆小鹿'))  # print(main('MD-0020'))  # print(main('mds009'))  # print(main('女王的SM调教'))  # print(main('91CM202'))  # print(main('必射客 没钱买披萨只好帮外送员解决问题 大象传媒.ts', file_path='必射客 没钱买披萨只好帮外送员解决问题 大象传媒.ts'))  # print(main('', file_path='素人自制舒舒 富婆偷情被偷拍 亏大了！50W买个视频还被操.ts'))  # print(main('', file_path='/sp/sp3/国产/2021年10月份 國產原創原版合集/20211003 91CM-191 你好同学ep5 MSD011/[c0e0.com]实战现场 .TS'))

import re
from typing import override
from urllib.parse import quote, urljoin

from parsel import Selector

from ..config.manager import manager
from ..config.models import Website
from ..models.types import CrawlerResult
from .base import BaseCrawler, CralwerException, CrawlerData, DetailPageParser, extract_all_texts, extract_text


def has_class(class_name: str) -> str:
    return f'contains(concat(" ", normalize-space(@class), " "), " {class_name} ")'


class Parser(DetailPageParser):
    async def number(self, ctx, html: Selector) -> str:
        result = extract_text(html, f"//a[{has_class('copy-to-clipboard')}]/@data-clipboard-text")
        return result or ctx.input.number

    async def title(self, ctx, html: Selector) -> str:
        return extract_text(
            html,
            f"string(//h2[{has_class('title')} and {has_class('is-4')}]/strong[{has_class('current-title')}])",
        )

    async def originaltitle(self, ctx, html: Selector) -> str:
        return extract_text(
            html,
            f"string(//h2[{has_class('title')} and {has_class('is-4')}]/span[{has_class('origin-title')}])",
        )

    async def actors(self, ctx, html: Selector) -> list[str]:
        # parsel css 不支持 :has() 中的多个选择器, 这是一个已知问题: https://github.com/scrapy/cssselect/issues/138
        return (
            html.css("span:has(strong.female)")
            .xpath(".//strong[contains(@class, 'female')]/preceding-sibling::a[1]/text()")
            .getall()
        )

    async def all_actors(self, ctx, html: Selector) -> list[str]:
        return html.css("span:has(strong.female), span:has(strong.male)").xpath("a/text()").getall()

    async def studio(self, ctx, html: Selector) -> str:
        return extract_text(
            html,
            '//strong[contains(text(),"片商:")]/../span/a/text()',
            '//strong[contains(text(),"Maker:")]/../span/a/text()',
        )

    async def publisher(self, ctx, html: Selector) -> str:
        return extract_text(
            html,
            '//strong[contains(text(),"發行:")]/../span/a/text()',
            '//strong[contains(text(),"Publisher:")]/../span/a/text()',
        )

    async def runtime(self, ctx, html: Selector) -> str:
        result = extract_text(
            html,
            '//strong[contains(text(),"時長")]/../span/text()',
            '//strong[contains(text(),"Duration:")]/../span/text()',
        )
        return result.replace(" 分鍾", "").replace(" minute(s)", "")

    async def series(self, ctx, html: Selector) -> str:
        return extract_text(
            html,
            '//strong[contains(text(),"系列:")]/../span/a/text()',
            '//strong[contains(text(),"Series:")]/../span/a/text()',
        )

    async def release(self, ctx, html: Selector) -> str:
        return extract_text(
            html,
            '//strong[contains(text(),"日期:")]/../span/text()',
            '//strong[contains(text(),"Released Date:")]/../span/text()',
        )

    async def year(self, ctx, html: Selector) -> str:
        release_date = await self.release(ctx, html)
        try:
            result = re.search(r"\d{4}", release_date)
            return result.group() if result else release_date
        except Exception:
            return release_date

    async def tags(self, ctx, html: Selector) -> list[str]:
        tags = extract_all_texts(
            html,
            '//strong[contains(text(),"類別:")]/../span/a/text()',
            '//strong[contains(text(),"Tags:")]/../span/a/text()',
        )
        tags = [tag.replace("\\xa0", "").replace("'", "").replace(" ", "").strip() for tag in tags if tag.strip()]
        return list(dict.fromkeys(tags))

    async def thumb(self, ctx, html: Selector) -> str:
        return extract_text(html, f"//img[{has_class('video-cover')}]/@src")

    async def extrafanart(self, ctx, html: Selector) -> list[str]:
        return extract_all_texts(
            html,
            f"//div[{has_class('tile-images')} and {has_class('preview-images')}]//a[{has_class('tile-item')}]/@href",
        )

    async def trailer(self, ctx, html: Selector) -> str:
        return extract_text(html, "//video[@id='preview-video']/source/@src")

    async def directors(self, ctx, html: Selector) -> list[str]:
        return extract_all_texts(
            html,
            '//strong[contains(text(),"導演:")]/../span/a/text()',
            '//strong[contains(text(),"Director:")]/../span/a/text()',
        )

    async def score(self, ctx, html: Selector) -> str:
        result = extract_text(html, f"string(//span[{has_class('score-stars')}]/..)")
        try:
            score_match = re.search(r"(\d{1}\.\d+)(分|,)", result)
            return score_match.group(1) if score_match else ""
        except Exception:
            return ""

    async def wanted(self, ctx, html: Selector) -> str:
        html_text = html.get()
        result = re.search(r"(\d+)(人想看| want to watch it)", html_text)
        return result.group(1) if result else ""

    async def image_cut(self, ctx, html: Selector) -> str:
        return "right"

    async def image_download(self, ctx, html: Selector) -> bool:
        return False


class JavdbCrawler(BaseCrawler):
    parser = Parser()

    @classmethod
    @override
    def site(cls) -> Website:
        return Website.JAVDB

    @classmethod
    @override
    def base_url_(cls) -> str:
        return "https://javdb.com"

    @override
    def _get_headers(self, ctx) -> dict[str, str] | None:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
        }

    @override
    def _get_cookies(self, ctx) -> dict[str, str] | None:
        cookies = {"over18": "1", "locale": "zh"}
        if manager.config.javdb:
            for item in manager.config.javdb.split(";"):
                if "=" not in item:
                    continue
                key, value = item.strip().split("=", 1)
                if key:
                    cookies[key] = value
        return cookies

    def _normalize_number(self, number: str) -> str:
        number = number.strip()

        # 处理日期格式的番号
        if "." in number:
            old_date = re.findall(r"\D+(\d{2}\.\d{2}\.\d{2})$", number)
            if old_date:
                old_date = old_date[0]
                new_date = "20" + old_date
                number = number.replace(old_date, new_date)
        return number

    @override
    async def _generate_search_url(self, ctx) -> list[str]:
        number = self._normalize_number(ctx.input.number)
        search_url = f"{self.base_url}/search?q={quote(number)}&locale=zh"
        ctx.debug(f"搜索地址: {search_url}")
        return [search_url]

    @override
    async def _parse_search_page(self, ctx, html: Selector, search_url: str) -> list[str] | None:
        html_text = html._text or ""
        if "/over18" in html_text and "modal" in html_text:
            raise CralwerException("搜索结果: JavDB 返回年龄确认页，请在设置中更新可用 Cookie 后重试！")
        if "/login" in html_text and "video-title" not in html_text and "movie-list" not in html_text:
            raise CralwerException("搜索结果: JavDB 返回登录页，请在设置中更新可用 Cookie 后重试！")
        if "The owner of this website has banned your access based on your browser's behaving" in html_text:
            raise CralwerException(f"由于请求过多，javdb网站暂时禁止了你当前IP的访问！！点击 {search_url} 查看详情！")
        if "Due to copyright restrictions" in html_text:
            raise CralwerException(
                f"由于版权限制，javdb网站禁止了日本IP的访问！！请更换日本以外代理！点击 {search_url} 查看详情！"
            )
        if "ray-id" in html_text:
            raise CralwerException("搜索结果: 被 Cloudflare 5 秒盾拦截！请尝试更换cookie！")

        # 获取搜索结果
        res_list = html.xpath(f"//a[{has_class('box')}]")
        if not res_list:
            return None

        info_list = []
        for each in res_list:
            href = extract_text(each, "@href")
            number_in_list = extract_text(each, f".//div[{has_class('video-title')}]/strong/text()")

            if href and number_in_list:
                info_list.append([href, number_in_list])

        # 精确匹配
        number = self._normalize_number(ctx.input.number).upper()
        for href, number_in_list in info_list:
            if number == number_in_list.upper():
                return [urljoin(self.base_url, href)]

        # 模糊匹配（去掉特殊字符）
        clean_number = number.replace(".", "").replace("-", "").replace(" ", "")
        for href, number_in_list in info_list:
            clean_number_in_list = number_in_list.upper().replace(".", "").replace("-", "").replace(" ", "")
            if clean_number == clean_number_in_list:
                return [urljoin(self.base_url, href)]

        return None

    @override
    async def _parse_detail_page(self, ctx, html: Selector, detail_url: str) -> CrawlerData | None:
        html_text = html._text or ""
        if "/over18" in html_text and "modal" in html_text:
            raise CralwerException("详情页: JavDB 返回年龄确认页，请在设置中更新可用 Cookie 后重试！")
        if "/login" in html_text and "current-title" not in html_text:
            raise CralwerException("详情页: JavDB 返回登录页，请在设置中更新可用 Cookie 后重试！")

        # 提取 javdbid
        javdbid = ""
        if r := re.search(r"/v/([a-zA-Z0-9]+)", detail_url):
            javdbid = r.group(1)
        data = await self.parser.parse(ctx, html, external_id=javdbid)
        if not data.title and not data.number and not data.thumb:
            page_title = extract_text(html, "string(//title)")
            raise CralwerException(f"详情页未解析到有效数据，请检查 JavDB 页面结构或 Cookie。页面标题: {page_title}")
        return data

    @override
    async def post_process(self, ctx, res: CrawlerResult) -> CrawlerResult:
        if not res.originaltitle:
            res.originaltitle = res.title
        res.poster = res.thumb.replace("/covers/", "/thumbs/")
        res.mosaic = "无码" if any(keyword in res.title for keyword in ["無碼", "無修正", "Uncensored"]) else "有码"
        if res.trailer.startswith("//"):
            res.trailer = "https:" + res.trailer
        return res

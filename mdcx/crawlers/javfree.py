import re
from typing import override
from urllib.parse import quote, urljoin, urlsplit

from parsel import Selector

from ..config.models import Website
from .base import BaseCrawler, CralwerException, CrawlerData, DetailPageParser, extract_all_texts, extract_text


def normalize_number(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (text or "").upper())


def format_number(raw: str) -> str:
    value = (raw or "").strip().upper()
    if not value:
        return ""
    value = value.replace("_", "-").replace(" ", "")
    if "-" in value:
        return value
    match = re.match(r"^([A-Z]+)(\d{3,})$", value)
    if match:
        prefix, digits = match.groups()
        if len(digits) > 3 and digits.startswith("0"):
            digits = digits[-3:]
        return f"{prefix}-{digits}"
    return value


def split_content_blocks(html: Selector) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    info: dict[str, str] = {}
    outline_parts: list[str] = []
    download_links: list[str] = []
    image_urls: list[str] = []

    content = html.xpath('//div[@class="entry-content"]')
    if not content:
        return info, outline_parts, download_links, image_urls

    blocks = content.xpath('./*')
    for block in blocks:
        tag_name = (block.root.tag if getattr(block, 'root', None) is not None else '').lower()
        text = " ".join(t.strip() for t in block.xpath('.//text()').getall() if t.strip())
        links = [u.strip() for u in block.xpath('.//a/@href').getall() if u.strip()]
        imgs = [u.strip() for u in block.xpath('.//img/@src').getall() if u.strip()]

        if tag_name == 'blockquote':
            paragraphs = block.xpath('./p')
            if paragraphs:
                first_texts = [t.strip() for t in paragraphs[0].xpath('.//text()').getall() if t.strip()]
                for line in first_texts:
                    if '：' not in line:
                        continue
                    key, value = line.split('：', 1)
                    key = key.strip()
                    value = value.strip()
                    if key and value:
                        info[key] = value

                for paragraph in paragraphs[1:]:
                    para_text = " ".join(t.strip() for t in paragraph.xpath('.//text()').getall() if t.strip())
                    if para_text:
                        outline_parts.append(para_text)
            continue

        if links:
            download_links.extend(links)
        if imgs:
            image_urls.extend(imgs)

    return info, outline_parts, download_links, image_urls


class Parser(DetailPageParser):
    async def number(self, ctx, html: Selector) -> str:
        title = extract_text(html, 'string(//h1[@class="entry-title"])')
        if match := re.search(r"\[\s*([A-Za-z]{2,}\d*-\d+(?:-[A-Za-z0-9]+)?)\s*\]", title):
            return match.group(1).upper()

        info, _, _, _ = split_content_blocks(html)
        number = info.get('品番', '')
        formatted = format_number(number)
        return formatted or ctx.input.number

    async def title(self, ctx, html: Selector) -> str:
        title = extract_text(html, 'string(//h1[@class="entry-title"])')
        title = re.sub(r"^\[\s*[A-Za-z0-9-]+\s*\]\s*", "", title).strip()
        return title

    async def originaltitle(self, ctx, html: Selector) -> str:
        return await self.title(ctx, html)

    async def actors(self, ctx, html: Selector) -> list[str]:
        info, _, _, _ = split_content_blocks(html)
        actors = info.get('出演者', '')
        values = [v.strip() for v in re.split(r'[、,/，]+', actors) if v.strip()]
        return values

    async def directors(self, ctx, html: Selector) -> list[str]:
        info, _, _, _ = split_content_blocks(html)
        director = info.get('監督', '')
        return [director] if director else []

    async def outline(self, ctx, html: Selector) -> str:
        _, outline_parts, _, _ = split_content_blocks(html)
        return '\n'.join(outline_parts).strip()

    async def originalplot(self, ctx, html: Selector) -> str:
        return await self.outline(ctx, html)

    async def poster(self, ctx, html: Selector) -> str:
        thumb = await self.thumb(ctx, html)
        return thumb

    async def publisher(self, ctx, html: Selector) -> str:
        info, _, _, _ = split_content_blocks(html)
        return info.get('レーベル', '')

    async def release(self, ctx, html: Selector) -> str:
        info, _, _, _ = split_content_blocks(html)
        return info.get('配信開始日', '').replace('/', '-').replace('.', '-')

    async def runtime(self, ctx, html: Selector) -> str:
        info, _, _, _ = split_content_blocks(html)
        return info.get('収録時間', '').replace('分', '')

    async def series(self, ctx, html: Selector) -> str:
        return ''

    async def studio(self, ctx, html: Selector) -> str:
        info, _, _, _ = split_content_blocks(html)
        return info.get('メーカー', '')

    async def tags(self, ctx, html: Selector) -> list[str]:
        info, _, _, _ = split_content_blocks(html)
        tags: list[str] = []
        genres = info.get('ジャンル', '')
        for item in re.split(r'[\s,，]+', genres):
            item = item.strip()
            if item and item not in tags:
                tags.append(item)
        for item in extract_all_texts(html, '//div[contains(@class,"entry-tags")]//span[a and strong[contains(text(),"Categorized")]]/a/text()', '//div[contains(@class,"entry-tags")]//span[a and strong[contains(text(),"Tags")]]/a/text()'):
            if item and item not in tags:
                tags.append(item)
        return tags

    async def thumb(self, ctx, html: Selector) -> str:
        _, _, _, image_urls = split_content_blocks(html)
        return image_urls[0] if image_urls else ''

    async def extrafanart(self, ctx, html: Selector) -> list[str]:
        _, _, _, image_urls = split_content_blocks(html)
        extra_images = [url for url in image_urls[1:] if re.search(r'-\d+\.(?:jpg|jpeg|png)$', url, re.I)]
        return extra_images

    async def trailer(self, ctx, html: Selector) -> str:
        return ''

    async def wanted(self, ctx, html: Selector) -> str:
        return ''

    async def year(self, ctx, html: Selector) -> str:
        release = await self.release(ctx, html)
        match = re.search(r'\d{4}', release)
        return match.group(0) if match else ''

    async def image_cut(self, ctx, html: Selector) -> str:
        return 'no'

    async def image_download(self, ctx, html: Selector) -> bool:
        return False

    async def mosaic(self, ctx, html: Selector) -> str:
        return '有码'


class JavfreeCrawler(BaseCrawler):
    parser = Parser()

    @classmethod
    @override
    def site(cls) -> Website:
        return Website.JAVFREE

    @classmethod
    @override
    def base_url_(cls) -> str:
        return 'https://javfree.me'

    @override
    async def _generate_search_url(self, ctx) -> list[str]:
        number = (ctx.input.number or '').strip()
        return [f'{self.base_url}/search/{quote(number)}'] if number else []

    @override
    async def _parse_search_page(self, ctx, html: Selector, search_url: str) -> list[str] | None:
        result = []
        seen = set()
        target = normalize_number(ctx.input.number)
        for article in html.xpath('//div[contains(@class,"content-loop")]//article')[:5]:
            href = extract_text(article, './/a[contains(@href, "/")]/@href')
            title = extract_text(article, 'string(.//h2[contains(@class,"entry-title")])')
            if not href or not title:
                continue
            slug = urlsplit(href).path.rstrip('/').split('/')[-1]
            candidates = [title]
            if slug:
                candidates.append(slug)
            matched = False
            for candidate in candidates:
                title_match = re.search(r'\[\s*([A-Za-z0-9-]+)\s*\]', candidate)
                if title_match and normalize_number(title_match.group(1)) == target:
                    matched = True
                    break
                if normalize_number(candidate) == target:
                    matched = True
                    break
                if target and target in normalize_number(candidate):
                    matched = True
                    break
            if matched:
                full_url = urljoin(self.base_url + '/', href)
                if full_url not in seen:
                    seen.add(full_url)
                    result.append(full_url)
        return result or None

    @override
    async def _parse_detail_page(self, ctx, html: Selector, detail_url: str) -> CrawlerData | None:
        return await self.parser.parse(ctx, html, external_id=detail_url)


main = JavfreeCrawler

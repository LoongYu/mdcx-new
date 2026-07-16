import pytest
from parsel import Selector

from mdcx.crawlers import dmm_new, javdb_new
from mdcx.crawlers.base import Context
from mdcx.models.types import CrawlerInput, Language
from tests.crawlers.parser import ParserTestBase


def crawler_input(number: str) -> CrawlerInput:
    return CrawlerInput(
        appoint_number="",
        appoint_url="",
        file_path=None,
        mosaic="",
        number=number,
        short_number="",
        language=Language.UNDEFINED,
        org_language=Language.UNDEFINED,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, parser_class",
    [
        ("dmm/mono", dmm_new.MonoParser),
        ("dmm/digital", dmm_new.DigitalParser),
        ("dmm/rental", dmm_new.RentalParser),
        ("javdb", javdb_new.Parser),
    ],
)
async def test_parsers(name, parser_class, overwrite, parser_names):
    if parser_names and name not in parser_names:
        pytest.skip(f"跳过解析器: {name}")
    t = ParserTestBase(name, parser_class, overwrite)
    success = await t.run_all_tests()
    assert success, "所有测试应该通过"


@pytest.mark.asyncio
async def test_javdb_actor_gender_markers_only_select_matching_actor():
    html = Selector(
        text="""
        <div class="panel-block">
          <strong>演員:</strong>
          <span>
            <a>女優A</a><strong class="female">♀</strong>
            <a>男優B</a><strong class="male">♂</strong>
            <a>女優C</a><strong class="female">♀</strong>
          </span>
          <span><a>男優D</a><strong class="male">♂</strong></span>
        </div>
        """
    )
    parser = javdb_new.Parser()

    assert await parser.actors(None, html) == ["女優A", "女優C"]
    assert await parser.all_actors(None, html) == ["女優A", "男優B", "女優C", "男優D"]


@pytest.mark.asyncio
async def test_javdb_parser_accepts_extra_classes_on_detail_page():
    html = Selector(
        text="""
        <html>
          <head><title>JavDB</title></head>
          <body>
            <h2 class="title is-4 extra">
              <strong class="current-title translated">測試標題</strong>
              <span class="origin-title hidden">Original Title</span>
            </h2>
            <a class="button is-white copy-to-clipboard active" data-clipboard-text="ABP-001"></a>
            <img class="video-cover lazy" src="https://example.test/covers/ab/ABP001.jpg" />
            <div class="tile-images preview-images compact">
              <a class="tile-item sample" href="https://example.test/sample1.jpg"></a>
            </div>
            <span class="score-stars large"></span> 4.20分, 由1人評價
          </body>
        </html>
        """
    )
    parser = javdb_new.Parser()
    ctx = Context(input=crawler_input("ABP-001"))

    assert await parser.title(ctx, html) == "測試標題"
    assert await parser.originaltitle(ctx, html) == "Original Title"
    assert await parser.number(ctx, html) == "ABP-001"
    assert await parser.thumb(ctx, html) == "https://example.test/covers/ab/ABP001.jpg"
    assert await parser.extrafanart(ctx, html) == ["https://example.test/sample1.jpg"]
    assert await parser.score(ctx, html) == "4.20"


@pytest.mark.asyncio
async def test_javdb_search_accepts_extra_classes_and_normalized_date_number():
    html = Selector(
        text="""
        <div class="movie-list h cols-4">
          <div class="item">
            <a href="/v/date1" class="box movie-list-item">
              <div class="video-title strong"><strong>HEYDOUGA-4037-2024.03.15</strong> title</div>
            </a>
          </div>
        </div>
        """
    )
    crawler = javdb_new.JavdbCrawler(client=None)
    ctx = Context(input=crawler_input("HEYDOUGA-4037-24.03.15"))

    assert await crawler._parse_search_page(ctx, html, "https://javdb.com/search") == [
        "https://javdb.com/v/date1"
    ]

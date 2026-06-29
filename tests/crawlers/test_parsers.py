import pytest
from parsel import Selector

from mdcx.crawlers import dmm_new, javdb_new
from tests.crawlers.parser import ParserTestBase


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

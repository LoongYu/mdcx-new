from pathlib import Path

import pytest

from mdcx.config.enums import NfoInclude
from mdcx.config.manager import manager
from mdcx.core.nfo import write_nfo
from mdcx.models.types import CrawlersResult, FileInfo


def _file_info(tmp_path: Path) -> FileInfo:
    file_info = FileInfo.empty()
    file_info.number = "TEST-001"
    file_info.file_path = tmp_path / "TEST-001.mp4"
    return file_info


def _crawler_result() -> CrawlersResult:
    result = CrawlersResult.empty()
    result.number = "TEST-001"
    result.title = "Test Title"
    result.originaltitle = "Test Title"
    result.actors = ["女優A", "女優C"]
    result.all_actors = ["女優A", "男優B", "女優C"]
    result.directors = ["导演D"]
    return result


async def _write_test_nfo(tmp_path: Path, includes: list[NfoInclude], monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(manager.config, "nfo_include_new", includes)
    nfo_file = tmp_path / "TEST-001.nfo"

    ok = await write_nfo(_file_info(tmp_path), _crawler_result(), nfo_file, tmp_path, update=True)

    assert ok
    return nfo_file.read_text(encoding="UTF-8")


@pytest.mark.asyncio
async def test_write_nfo_actor_uses_female_actors_without_actor_all(tmp_path, monkeypatch):
    content = await _write_test_nfo(tmp_path, [NfoInclude.ACTOR], monkeypatch)

    assert "<name>女優A</name>" in content
    assert "<name>女優C</name>" in content
    assert "<name>男優B</name>" not in content
    assert "<director>导演D</director>" not in content


@pytest.mark.asyncio
async def test_write_nfo_actor_all_includes_male_actors_and_director_is_independent(tmp_path, monkeypatch):
    content = await _write_test_nfo(
        tmp_path,
        [NfoInclude.ACTOR, NfoInclude.ACTOR_ALL, NfoInclude.DIRECTOR],
        monkeypatch,
    )

    assert "<name>女優A</name>" in content
    assert "<name>男優B</name>" in content
    assert "<name>女優C</name>" in content
    assert "<director>导演D</director>" in content

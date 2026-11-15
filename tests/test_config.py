"""Config schema (project.toml checked at load) and the shared page-selection parser."""
import pytest

from drawtrans import pagesel
from drawtrans.config import schema
from drawtrans.config.job import JobConfig


def test_schema_accepts_the_examples():
    import tomllib

    from conftest import ROOT
    for f in [ROOT / "project.example.toml", *ROOT.glob("demo/**/project.toml")]:
        rep = schema.check(tomllib.loads(f.read_text(encoding="utf-8")))
        assert rep.errors == [] and rep.warnings == [], f


def test_schema_names_the_key_and_the_fix(tmp_path):
    rep = schema.check({"project": {"source_lang": "xx"}, "llm": {"budget_usd": "5", "concurrency": True},
                        "scan": {"output_standrd": 2}, "jobb": {}})
    assert "[project] source_lang = 'xx': must be one of" in rep.errors[0]
    assert any("[llm] budget_usd = '5': expected a number" in e for e in rep.errors)
    assert any("concurrency" in e for e in rep.errors)                      # a bool is not an integer
    assert any("did you mean output_standard?" in w for w in rep.warnings)
    assert any("did you mean [job]?" in w for w in rep.warnings)
    p = tmp_path / "project.toml"
    p.write_text('[project]\nsource_lang = "xx"\n', encoding="utf-8")
    with pytest.raises(schema.ConfigError, match="source_lang"):
        JobConfig.load(p)


def test_pagesel():
    assert pagesel.expand("3, 7-9,,2") == [3, 7, 8, 9, 2]
    assert pagesel.select("", 3) == [1, 2, 3]
    assert pagesel.select("2-5,9", 4) == [2, 3, 4]

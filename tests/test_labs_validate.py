import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import labs  # noqa: E402

TOPICS = {"topics": [
    {"name": "dvs", "branch": "dvs-integ", "upstream": "planned", "impact": "none",
     "configure": ["--with-dv-solve"], "requires_inputs": ["dv-solve"], "tests": ["t_dvs_*"]},
    {"name": "debug", "branch": "mballance/debug-proto", "upstream": "labs-only",
     "impact": "runtime hooks", "depends_on": ["dvs"]},
]}
BUILD_INPUTS = {"dependencies": [{"name": "dv-solve"}, {"name": "cadical"}]}
SHA = "33a6fc60c9ae299fb074c5661dc0fc0213a47bd6"
RECORD = {
    "tag": "v5.055-labs.2026.01", "branch": "labs/2026-01", "head": SHA,
    "upstream": {"ref": "master", "sha": SHA}, "previous": None,
    "topics": [{"name": "dvs", "branch": "dvs-integ", "sha": SHA, "merge": SHA}],
    "glue": [],
    "test_gate": {"upstream_suite": "pass", "topic_tests": "pass", "cross_tests": "pass"},
}


def test_tag_for():
    assert labs.tag_for("5.055", "labs/2026-01") == "v5.055-labs.2026.01"
    with pytest.raises(ValueError):
        labs.tag_for("5.055", "labs-2026-01")


def test_tag_matches_release_policy_regex():
    # Same pattern as build-inputs.yaml's release_policy; upstream tags must not match.
    assert labs.TAG_RE.match("v5.055-labs.2026.01")
    assert not labs.TAG_RE.match("v5.055")
    assert not labs.TAG_RE.match("v5.055-labs.2026.01.1")


def test_topics_ok():
    assert labs.check_topics(TOPICS, BUILD_INPUTS) == []
    assert labs.check_topics(TOPICS, None) == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d["topics"][0].pop("branch"), "missing 'branch'"),
    (lambda d: d["topics"][0].update(colour="red"), "unknown field 'colour'"),
    (lambda d: d["topics"][1].update(name="dvs"), "duplicate name"),
    (lambda d: d["topics"][0].update(upstream="soon"), "upstream must be"),
    (lambda d: d["topics"][0].update(depends_on=["debug"]), "earlier topic"),
    (lambda d: d["topics"][0].update(tests="t_dvs_*"), "'tests' must be a list"),
    (lambda d: d["topics"][0].update(requires_inputs=["z3"]), "not a dependency"),
    (lambda d: d["topics"][0].update(name="DVS"), "name must match"),
])
def test_topics_errors(mutate, expect):
    d = copy.deepcopy(TOPICS)
    mutate(d)
    errs = labs.check_topics(d, BUILD_INPUTS)
    assert any(expect in e for e in errs), errs


def test_pr_open_upstream():
    d = copy.deepcopy(TOPICS)
    d["topics"][0]["upstream"] = "pr-open https://github.com/verilator/verilator/pull/1"
    assert labs.check_topics(d, BUILD_INPUTS) == []


def test_record_ok():
    assert labs.check_record(RECORD, "v5.055-labs.2026.01.yaml", {"dvs"}) == []


@pytest.mark.parametrize("mutate,fname,expect", [
    (lambda r: None, "wrong.yaml", "file name must be"),
    (lambda r: r.update(branch="labs/2026-02"), None, "does not match branch"),
    (lambda r: r.update(tag="v5.055"), "v5.055.yaml", "tag must match"),
    (lambda r: r.update(head="main"), None, "'head' must be a commit SHA"),
    (lambda r: r["test_gate"].update(cross_tests="ok"), None, "test_gate.cross_tests"),
    (lambda r: r.update(glue=[{"sha": SHA}]), None, "glue[0]"),
    (lambda r: r.update(previous="labs/2025-12"), None, "previous must be"),
])
def test_record_errors(mutate, fname, expect):
    r = copy.deepcopy(RECORD)
    mutate(r)
    errs = labs.check_record(r, fname or "v5.055-labs.2026.01.yaml", {"dvs"})
    assert any(expect in e for e in errs), errs


def test_validate_cli(tmp_path, capsys):
    (tmp_path / "topics.yaml").write_text(yaml.safe_dump(TOPICS))
    (tmp_path / "releases").mkdir()
    (tmp_path / "releases" / "v5.055-labs.2026.01.yaml").write_text(yaml.safe_dump(RECORD))
    assert labs.main(["--root", str(tmp_path), "validate"]) == 0
    assert "build-inputs.yaml not found" in capsys.readouterr().out
    bad = copy.deepcopy(RECORD)
    bad["topics"][0]["name"] = "retired"
    (tmp_path / "releases" / "v5.055-labs.2026.01.yaml").write_text(yaml.safe_dump(bad))
    assert labs.main(["--root", str(tmp_path), "validate"]) == 0  # warning only
    (tmp_path / "topics.yaml").write_text("topics: 3\n")
    assert labs.main(["--root", str(tmp_path), "validate"]) == 1

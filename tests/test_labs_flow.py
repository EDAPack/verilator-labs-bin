"""End-to-end tests of the labs.py release flow on throwaway git repos.

A fake upstream, a fork (origin) carrying topic branches, and a clone to
integrate in. Building Verilator and running its tests are stubbed out.
"""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import labs  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()


def commit(repo, path, text, msg):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    sh(repo, "add", path)
    sh(repo, "commit", "-q", "-m", msg)


class World:
    def __init__(self, tmp):
        self.tmp = tmp
        self.up = tmp / "upstream"
        self.origin = tmp / "origin.git"
        self.dev = tmp / "dev"
        self.work = tmp / "work"
        self.root = tmp / "labs-bin"

    def run(self, *args):
        return labs.main(["--root", str(self.root), "--verilator", str(self.work), *args])

    def upstream_commit(self, path, text, msg):
        commit(self.up, path, text, msg)

    def topic_commit(self, branch, path, text, msg):
        sh(self.dev, "fetch", "-q", "origin")
        sh(self.dev, "switch", "-q", branch)
        commit(self.dev, path, text, msg)
        sh(self.dev, "push", "-q", "origin", branch)


@pytest.fixture
def w(tmp_path, monkeypatch):
    for k, v in {"HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    w = World(tmp_path)
    w.up.mkdir()
    sh(w.up, "init", "-q", "-b", "master")
    commit(w.up, "configure.ac", "AC_INIT([Verilator],[5.055 devel],\n", "init")
    commit(w.up, "a.txt", "one\ntwo\nthree\n", "a")
    sh(tmp_path, "clone", "-q", "--bare", str(w.up), str(w.origin))
    sh(tmp_path, "clone", "-q", str(w.origin), str(w.dev))
    sh(w.dev, "switch", "-q", "-c", "t1")
    commit(w.dev, "test_regress/t/t_one.py", "#\n", "t1: feature one")
    sh(w.dev, "push", "-q", "origin", "t1")
    sh(w.dev, "switch", "-q", "-c", "mballance/t2", "master")
    commit(w.dev, "a.txt", "one\nTWO-from-t2\nthree\n", "t2: feature two")
    sh(w.dev, "push", "-q", "origin", "mballance/t2")
    sh(tmp_path, "clone", "-q", str(w.origin), str(w.work))
    w.root.mkdir()
    (w.root / "topics.yaml").write_text(yaml.safe_dump({"topics": [
        {"name": "one", "branch": "t1", "upstream": "planned", "impact": "none",
         "enabled_by": "--one", "tests": ["t_one"]},
        {"name": "two", "branch": "mballance/t2", "upstream": "labs-only", "impact": "none"},
    ]}))
    monkeypatch.setattr(labs, "build", lambda *a, **k: None)
    monkeypatch.setattr(labs, "run_driver", lambda vdir, tests, log: "pass")
    assert w.run("setup", "--upstream-url", str(w.up)) == 0
    return w


def release(w, name, glue=None):
    """cut, merge, glue, test, record, tag, with no conflicts expected."""
    assert w.run("cut", name) == 0
    assert w.run("merge") == 0
    assert w.run("glue") == 0
    if glue:
        commit(w.work, "glue.txt", glue, f"labs: {glue}")
    assert w.run("test") == 0
    assert w.run("record") == 0
    assert w.run("tag") == 0


def test_setup(w):
    assert sh(w.work, "remote", "get-url", "upstream") == str(w.up)
    assert sh(w.work, "config", "rerere.enabled") == "true"


def test_first_release(w, capsys):
    release(w, "2026-01")
    tag = "v5.055-labs.2026.01"
    rec = yaml.safe_load((w.root / "releases" / f"{tag}.yaml").read_text())
    assert rec["branch"] == "labs/2026-01" and rec["previous"] is None
    assert [t["name"] for t in rec["topics"]] == ["one", "two"]
    assert rec["topics"][1]["branch"] == "mballance/t2"
    assert rec["topics"][0]["sha"] == sh(w.work, "rev-parse", "origin/t1")
    assert rec["upstream"]["sha"] == sh(w.work, "rev-parse", "upstream/master")
    assert rec["head"] == sh(w.work, "rev-parse", tag + "^{commit}")
    msg = sh(w.work, "tag", "-l", "--format=%(contents)", tag)
    assert "## Topics" in msg and "`--one`" in msg      # headings survive (verbatim)
    assert w.run("validate") == 0
    capsys.readouterr()
    assert w.run("release-notes", tag) == 0
    assert "| two | `mballance/t2` |" in capsys.readouterr().out
    # The tag message is the fallback when the record is not committed.
    (w.root / "releases" / f"{tag}.yaml").unlink()
    assert w.run("release-notes", tag) == 0
    assert "## Topics" in capsys.readouterr().out


def test_merge_is_resumable_and_cut_refuses_existing(w, capsys):
    assert w.run("cut", "2026-01") == 0
    assert w.run("merge") == 0
    assert w.run("merge") == 0
    assert "merged:  two" in capsys.readouterr().out
    sh(w.work, "switch", "-q", "master")
    assert w.run("cut", "2026-01") == 1
    assert w.run("cut", "26-1") == 1


def test_conflict_stops_and_rerere_replays_next_release(w, capsys):
    w.upstream_commit("a.txt", "one\nTWO-upstream\nthree\n", "upstream edits a")
    assert w.run("setup", "--upstream-url", str(w.up)) == 0
    assert w.run("cut", "2026-01") == 0
    assert w.run("merge") == 2
    out = capsys.readouterr().out
    assert "CONFLICT" in out and "a.txt" in out
    assert w.run("merge") == 2                          # still unresolved
    (w.work / "a.txt").write_text("one\nTWO-merged\nthree\n")
    sh(w.work, "add", "a.txt")
    sh(w.work, "commit", "-q", "--no-edit")
    assert w.run("merge") == 0
    assert w.run("test") == 0 and w.run("record") == 0 and w.run("tag") == 0

    # Next release: same conflict; rerere replays it, and the previous merge is the reference.
    w.upstream_commit("b.txt", "b\n", "upstream moves on")
    assert w.run("setup", "--upstream-url", str(w.up)) == 0
    sh(w.work, "switch", "-q", "master")
    assert w.run("cut", "2026-02") == 0
    capsys.readouterr()
    assert w.run("merge") == 2
    out = capsys.readouterr().out
    assert "rerere replayed" in out and "--remerge-diff" in out
    assert (w.work / "a.txt").read_text() == "one\nTWO-merged\nthree\n"


def test_glue_carried_forward(w, capsys):
    release(w, "2026-01", glue="fix one+two")
    w.upstream_commit("b.txt", "b\n", "upstream moves on")
    assert w.run("setup", "--upstream-url", str(w.up)) == 0
    sh(w.work, "switch", "-q", "master")
    assert w.run("cut", "2026-02") == 0
    assert w.run("merge") == 0
    capsys.readouterr()
    assert w.run("glue") == 0
    assert "picked:" in capsys.readouterr().out
    assert w.run("glue") == 0
    assert "present:" in capsys.readouterr().out
    assert w.run("test") == 0 and w.run("record") == 0
    rec = yaml.safe_load((w.root / "releases" / "v5.055-labs.2026.02.yaml").read_text())
    assert rec["previous"] == "v5.055-labs.2026.01"
    assert [g["subject"] for g in rec["glue"]] == ["labs: fix one+two"]


def test_glue_dropped_when_empty(w, capsys):
    release(w, "2026-01", glue="fix")
    # The fix moves into topic one, so the glue becomes empty next time.
    w.topic_commit("t1", "glue.txt", "fix", "one: absorb the fix")
    assert w.run("setup", "--upstream-url", str(w.up)) == 0
    sh(w.work, "switch", "-q", "master")
    assert w.run("cut", "2026-02") == 0
    assert w.run("merge") == 0
    capsys.readouterr()
    assert w.run("glue") == 0
    assert "dropped:" in capsys.readouterr().out


def test_record_guards(w, capsys, monkeypatch):
    assert w.run("cut", "2026-01") == 0
    assert w.run("merge") == 0
    assert w.run("record") == 1                         # no test results
    assert w.run("test", "--quick") == 0
    assert w.run("record") == 1                         # quick run can't be recorded
    assert w.run("test") == 0
    commit(w.work, "late.txt", "x", "labs: late change")
    assert w.run("record") == 1                         # tested a different HEAD
    monkeypatch.setattr(labs, "run_driver", lambda vdir, tests, log: "fail:3")
    assert w.run("test") == 1
    assert w.run("record") == 1
    assert w.run("record", "--waive", "known flaky t_one") == 0
    assert w.run("tag") == 0
    assert w.run("tag") == 1                            # exists
    err = capsys.readouterr().err
    assert "quick" in err and "re-run `labs.py test`" in err


def test_status(w, capsys):
    release(w, "2026-01")
    w.topic_commit("t1", "more.txt", "m", "one: more")
    assert w.run("setup", "--upstream-url", str(w.up)) == 0
    capsys.readouterr()
    assert w.run("status") == 0
    out = capsys.readouterr().out
    assert "previous release: v5.055-labs.2026.01" in out
    row = next(line for line in out.splitlines() if line.startswith("one "))
    assert row.split()[3] == "1"                        # one commit since the release


def test_configure_flags_override():
    topics = [{"configure": ["--with-dv-solve", "--enable-x"]}]
    assert labs.configure_flags(topics, ["--with-dv-solve=/opt/d"]) == \
        ["--enable-x", "--with-dv-solve=/opt/d"]
    assert labs.configure_flags(topics, [], ["--with-dv-solve"]) == ["--enable-x"]


def commit_record(w, tag):
    """Make the labs-bin checkout a repo with the record committed at HEAD."""
    if not (w.root / ".git").exists():
        sh(w.root, "init", "-q", "-b", "main")
    sh(w.root, "add", "-A")
    sh(w.root, "commit", "-q", "-m", f"Record {tag}")


def test_release_tag(w, capsys):
    tag = "v5.055-labs.2026.01"
    assert w.run("cut", "2026-01") == 0 and w.run("merge") == 0
    assert w.run("test") == 0 and w.run("record") == 0
    sh(w.root, "init", "-q", "-b", "main")
    commit(w.root, "topics.yaml", (w.root / "topics.yaml").read_text(), "topics")
    assert w.run("release-tag") == 1                    # record not committed
    commit_record(w, tag)
    assert w.run("release-tag") == 1                    # no source tag yet
    assert "source tag" in capsys.readouterr().err
    assert w.run("tag") == 0
    (w.root / "dirty.txt").write_text("x")
    assert w.run("release-tag") == 1                    # dirty tree
    (w.root / "dirty.txt").unlink()
    capsys.readouterr()
    assert w.run("release-tag") == 0
    out = capsys.readouterr().out
    assert sh(w.root, "rev-parse", tag + "^{commit}") == sh(w.root, "rev-parse", "HEAD")
    assert sh(w.root, "tag", "-l", "--format=%(contents)", tag) == f"Verilator Labs {tag}"
    pushes = [line.split()[-1] for line in out.splitlines() if " push origin " in line]
    assert pushes == ["labs/2026-01", tag, "HEAD", tag]
    assert w.run("release-tag") == 1                    # exists
    assert w.run("release-tag", tag, "--respin", "1") == 1   # needs --note
    capsys.readouterr()
    assert w.run("release-tag", tag, "--respin", "1", "--note", "fix  the\nrpath") == 0
    out = capsys.readouterr().out
    assert sh(w.root, "tag", "-l", "--format=%(contents)", tag + ".1") == "fix the rpath"
    assert "labs/2026-01" not in out                    # re-spin: nothing to push there


def test_release_tag_refuses_moved_source_tag(w, capsys):
    tag = "v5.055-labs.2026.01"
    release(w, "2026-01")
    commit_record(w, tag)
    commit(w.work, "late.txt", "x", "labs: late")
    sh(w.work, "tag", "-f", "-a", "-m", "moved", tag)
    assert w.run("release-tag") == 1
    assert "record's head" in capsys.readouterr().err


def test_check_release(w, capsys):
    tag = "v5.055-labs.2026.01"
    release(w, "2026-01")
    commit_record(w, tag)
    assert w.run("release-tag") == 0
    assert w.run("release-tag", tag, "--respin", "2", "--note", "rebuild with new glibc") == 0
    origin = ["--origin-url", str(w.origin)]

    def check(t):
        capsys.readouterr()
        rc = w.run("check-release", t, *origin)
        return rc, capsys.readouterr()

    rc, cap = check(tag)
    assert rc == 1 and "missing" in cap.err             # source tag not pushed yet
    sh(w.work, "push", "-q", "origin", "labs/2026-01", tag)
    rc, cap = check(tag)
    assert rc == 0
    assert cap.out.splitlines() == [f"core_ref={tag}", "revision=", "revision_note="]
    rc, cap = check(tag + ".2")
    assert rc == 0
    assert cap.out.splitlines() == [f"core_ref={tag}", "revision=2",
                                    "revision_note=rebuild with new glibc"]
    # The source tag on the remote moved away from the record.
    commit(w.work, "late.txt", "x", "labs: late")
    sh(w.work, "tag", "-f", "-a", "-m", "moved", tag)
    sh(w.work, "push", "-q", "-f", "origin", tag)
    rc, cap = check(tag)
    assert rc == 1 and "says" in cap.err
    assert check("v5.055-2026.01")[0] == 1              # not a labs tag
    assert check("v5.055-labs.2026.09")[0] == 1         # no record


def test_release_notes_respin(w, capsys):
    tag = "v5.055-labs.2026.01"
    release(w, "2026-01")
    capsys.readouterr()
    assert w.run("release-notes", tag + ".3") == 0
    assert "## Topics" in capsys.readouterr().out
    assert w.run("release-notes", "v5.055-labs.2026.07") == 1

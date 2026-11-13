#!/usr/bin/env python3
"""Verilator Labs integration tooling.

Works on the topic manifest (topics.yaml), the per-release records
(releases/<tag>.yaml) and, in later subcommands, the Verilator clone in
packages/verilator. See docs/design/labs-release.md and
docs/plan/labs-release-plan.md.

Subcommands, in the order a release uses them:
  setup          add the upstream remote, turn on rerere, fetch
  status         what changed in upstream and each topic since the last release
  cut NAME       create labs/NAME (e.g. 2026-02) from upstream/master
  merge          merge the topics in manifest order; stops on conflict, re-run to resume
  glue           cherry-pick the previous release's `labs:` glue commits
  test           build and run the test gate; results go to build/labs/<branch>/
  record         write releases/<tag>.yaml for the tested tip
  tag            create the annotated source tag in the clone (never pushes)
  release-tag    after committing the record here, tag it to trigger the release
  release-notes  render a release's notes as Markdown (used by CI)
  check-release  CI's trigger: check a release tag against its source tag
  configure-flags  CI's build: print the topics' configure flags, one per line
  validate       check topics.yaml and releases/*.yaml

Exit status: 0 done, 1 error, 2 stopped for a human (conflict to resolve).
Nothing here pushes, so pushing the branch and tag stays a deliberate step.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_URL = "https://github.com/verilator/verilator"
# Where the topic and release branches and the release tags live.
ORIGIN_SLUG = "mballance/verilator"
ORIGIN_URL = f"https://github.com/{ORIGIN_SLUG}"

# v<verilator-version>-labs.<YYYY>.<NN>, e.g. v5.055-labs.2026.01. Must agree
# with release_policy in build-inputs.yaml.
TAG_RE = re.compile(r"^v(\d+\.\d+)-labs\.(\d{4})\.(\d{2})$")
# A packaging re-spin of a release: <tag>.<N>, only on this repo.
RESPIN_RE = re.compile(r"^(v\d+\.\d+-labs\.\d{4}\.\d{2})\.(\d+)$")
BRANCH_RE = re.compile(r"^labs/(\d{4})-(\d{2})$")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
UPSTREAM_RE = re.compile(r"^(planned|labs-only|pr-open https?://\S+)$")
GATE_RE = re.compile(r"^(pass|none|fail:\d+)$")

TOPIC_FIELDS = {
    "name": str, "branch": str, "upstream": str, "impact": str,
    "enabled_by": str, "configure": list, "requires_inputs": list,
    "depends_on": list, "tests": list,
}
TOPIC_REQUIRED = ("name", "branch", "upstream", "impact")
GATE_KEYS = ("upstream_suite", "topic_tests", "cross_tests")


def tag_for(verilator_version: str, branch: str) -> str:
    """`5.055`, `labs/2026-01` -> `v5.055-labs.2026.01`."""
    m = BRANCH_RE.match(branch)
    if not m:
        raise ValueError(f"not a labs release branch: {branch!r}")
    return f"v{verilator_version}-labs.{m.group(1)}.{m.group(2)}"


def _load(path: Path):
    with open(path) as f:
        return yaml.safe_load(f)


def check_topics(data, build_inputs=None) -> list:
    """Return a list of error strings for a parsed topics.yaml.

    `build_inputs` is the parsed build-inputs.yaml, or None when it does not
    exist yet; requires_inputs is only cross-checked when it is given.
    """
    errs = []
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        return ["topics.yaml: top level must be a mapping with a 'topics' list"]
    for k in data:
        if k not in ("topics", "cross_tests"):
            errs.append(f"topics.yaml: unknown top-level key '{k}'")
    ct = data.get("cross_tests", [])
    if not (isinstance(ct, list) and all(isinstance(x, str) for x in ct)):
        errs.append("topics.yaml: cross_tests must be a list of strings")
    dep_names = None
    if build_inputs is not None:
        dep_names = {d.get("name") for d in build_inputs.get("dependencies") or []}
    seen = []
    for i, t in enumerate(data["topics"]):
        where = f"topics[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        name = t.get("name")
        if isinstance(name, str):
            where = f"topic {name!r}"
        for k in TOPIC_REQUIRED:
            if k not in t:
                errs.append(f"{where}: missing '{k}'")
        for k, v in t.items():
            if k not in TOPIC_FIELDS:
                errs.append(f"{where}: unknown field '{k}'")
            elif not isinstance(v, TOPIC_FIELDS[k]):
                errs.append(f"{where}: '{k}' must be a {TOPIC_FIELDS[k].__name__}")
            elif isinstance(v, list) and not all(isinstance(x, str) for x in v):
                errs.append(f"{where}: '{k}' must be a list of strings")
        if isinstance(name, str):
            if not NAME_RE.match(name):
                errs.append(f"{where}: name must match {NAME_RE.pattern}")
            if name in seen:
                errs.append(f"{where}: duplicate name")
        if isinstance(t.get("upstream"), str) and not UPSTREAM_RE.match(t["upstream"]):
            errs.append(f"{where}: upstream must be planned, labs-only or 'pr-open <url>'")
        for dep in t.get("depends_on") or []:
            if dep not in seen:
                errs.append(f"{where}: depends_on '{dep}' must name an earlier topic")
        if dep_names is not None:
            for inp in t.get("requires_inputs") or []:
                if inp not in dep_names:
                    errs.append(f"{where}: requires_inputs '{inp}' is not a "
                                f"dependency in build-inputs.yaml")
        if isinstance(name, str):
            seen.append(name)
    return errs


def check_record(data, filename: str, topic_names=None) -> list:
    """Return a list of error strings for one parsed releases/<tag>.yaml."""
    where = f"releases/{filename}"
    if not isinstance(data, dict):
        return [f"{where}: must be a mapping"]
    errs = []
    for k in ("tag", "branch", "head", "upstream", "topics", "test_gate"):
        if k not in data:
            errs.append(f"{where}: missing '{k}'")
    tag, branch = data.get("tag"), data.get("branch")
    tm = TAG_RE.match(tag) if isinstance(tag, str) else None
    bm = BRANCH_RE.match(branch) if isinstance(branch, str) else None
    if "tag" in data and not tm:
        errs.append(f"{where}: tag must match {TAG_RE.pattern}")
    if "branch" in data and not bm:
        errs.append(f"{where}: branch must match {BRANCH_RE.pattern}")
    if tm and filename != f"{tag}.yaml":
        errs.append(f"{where}: file name must be {tag}.yaml")
    if tm and bm and (tm.group(2), tm.group(3)) != bm.groups():
        errs.append(f"{where}: tag {tag} does not match branch {branch}")
    for k in ("head",):
        if k in data and not (isinstance(data[k], str) and SHA_RE.match(data[k])):
            errs.append(f"{where}: '{k}' must be a commit SHA")
    up = data.get("upstream")
    if "upstream" in data and not (isinstance(up, dict) and isinstance(up.get("sha"), str)
                                   and SHA_RE.match(up["sha"])):
        errs.append(f"{where}: upstream must have a commit 'sha'")
    if "previous" in data and data["previous"] is not None \
            and not (isinstance(data["previous"], str) and TAG_RE.match(data["previous"])):
        errs.append(f"{where}: previous must be a labs tag or null")
    topics = data.get("topics")
    if "topics" in data and not isinstance(topics, list):
        errs.append(f"{where}: topics must be a list")
        topics = []
    for i, t in enumerate(topics or []):
        if not isinstance(t, dict):
            errs.append(f"{where}: topics[{i}] must be a mapping")
            continue
        for k in ("name", "branch", "sha", "merge"):
            if k not in t:
                errs.append(f"{where}: topics[{i}] missing '{k}'")
        for k in ("sha", "merge"):
            if k in t and not (isinstance(t[k], str) and SHA_RE.match(t[k])):
                errs.append(f"{where}: topics[{i}].{k} must be a commit SHA")
        # A record is frozen history, so a topic later retired from
        # topics.yaml is fine; this only catches typos in fresh records.
        if topic_names is not None and t.get("name") not in topic_names:
            errs.append(f"{where}: topic '{t.get('name')}' is not in topics.yaml "
                        f"(ok only if it was retired since)")
    for i, g in enumerate(data.get("glue") or []):
        if not (isinstance(g, dict) and isinstance(g.get("sha"), str)
                and SHA_RE.match(g["sha"]) and isinstance(g.get("subject"), str)):
            errs.append(f"{where}: glue[{i}] needs 'sha' and 'subject'")
    gate = data.get("test_gate")
    if "test_gate" in data:
        if not isinstance(gate, dict):
            errs.append(f"{where}: test_gate must be a mapping")
        else:
            for k in GATE_KEYS:
                v = gate.get(k)
                if not (isinstance(v, str) and GATE_RE.match(v)):
                    errs.append(f"{where}: test_gate.{k} must be 'pass', 'none' or 'fail:<n>'")
    return errs


def cmd_validate(args) -> int:
    root = Path(args.root)
    errs, warns = [], []
    bi_path = root / "build-inputs.yaml"
    build_inputs = _load(bi_path) if bi_path.exists() else None
    if build_inputs is None:
        warns.append("build-inputs.yaml not found; requires_inputs not cross-checked")
    topics_path = root / "topics.yaml"
    topic_names = None
    if not topics_path.exists():
        errs.append("topics.yaml not found")
    else:
        data = _load(topics_path)
        errs += check_topics(data, build_inputs)
        if isinstance(data, dict) and isinstance(data.get("topics"), list):
            topic_names = {t.get("name") for t in data["topics"] if isinstance(t, dict)}
    for rec in sorted((root / "releases").glob("*.yaml")):
        for e in check_record(_load(rec), rec.name, topic_names):
            # Unknown topic names in a record are a warning: records are history.
            (warns if "not in topics.yaml" in e else errs).append(e)
    for w in warns:
        print(f"warning: {w}")
    for e in errs:
        print(f"error: {e}")
    n_rec = len(list((root / "releases").glob("*.yaml")))
    print(f"validate: {'FAIL' if errs else 'ok'} "
          f"({len(errs)} errors, {len(warns)} warnings, {n_rec} release records)")
    return 1 if errs else 0


# --------------------------------------------------------------------------- #
# Git and repo helpers
# --------------------------------------------------------------------------- #

class LabsError(Exception):
    pass


MERGE_SUBJECT_RE = re.compile(r"^labs: merge (\S+) \((.+)\)$")
AC_INIT_RE = re.compile(r"AC_INIT\(\[Verilator\],\s*\[(\d+\.\d+)")


def git(vdir, *args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(vdir), *args],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and r.returncode != 0:
        raise LabsError(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def git_ok(vdir, *args) -> bool:
    return subprocess.run(["git", "-C", str(vdir), *args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def default_verilator_dir() -> str:
    pk = os.environ.get("IVPM_PACKAGES")
    return str(Path(pk) / "verilator") if pk else str(REPO_ROOT / "packages" / "verilator")


def load_topics(root) -> dict:
    data = _load(Path(root) / "topics.yaml")
    errs = check_topics(data)
    if errs:
        raise LabsError("topics.yaml is invalid (run `labs.py validate`):\n  " + "\n  ".join(errs))
    return data


def current_branch(vdir) -> str:
    return git(vdir, "rev-parse", "--abbrev-ref", "HEAD")


def require_labs_branch(vdir) -> str:
    b = current_branch(vdir)
    if not BRANCH_RE.match(b):
        raise LabsError(f"not on a labs/YYYY-NN branch (on {b}); run `labs.py cut` first")
    return b


def require_clean(vdir):
    if git(vdir, "status", "--porcelain", "--untracked-files=no"):
        raise LabsError("working tree has uncommitted changes; commit or stash them first")
    if in_progress(vdir):
        raise LabsError(f"a {in_progress(vdir)} is in progress; finish it first")


def in_progress(vdir):
    gd = Path(git(vdir, "rev-parse", "--absolute-git-dir"))
    if (gd / "MERGE_HEAD").exists():
        return "merge"
    if (gd / "CHERRY_PICK_HEAD").exists():
        return "cherry-pick"
    return None


def unmerged_files(vdir) -> list:
    out = git(vdir, "diff", "--name-only", "--diff-filter=U")
    return out.splitlines() if out else []


def labs_tags(vdir) -> list:
    """Labs release tags in the clone, oldest first."""
    tags = [t for t in git(vdir, "tag", "-l", "v*-labs.*").splitlines() if TAG_RE.match(t)]

    def key(t):
        m = TAG_RE.match(t)
        return ([int(x) for x in m.group(1).split(".")], int(m.group(2)), int(m.group(3)))
    return sorted(tags, key=key)


def previous_tag(vdir, branch=None):
    """Newest labs tag older than `branch` (any, if branch is None)."""
    tags = labs_tags(vdir)
    if branch:
        bm = BRANCH_RE.match(branch)
        tags = [t for t in tags if TAG_RE.match(t).group(2, 3) < bm.groups()]
    return tags[-1] if tags else None


def release_base(vdir, rev) -> str:
    """The upstream commit a labs branch (or tag) was cut from: the first commit
    on its first-parent chain that upstream/master contains."""
    for c in git(vdir, "rev-list", "--first-parent", rev).splitlines():
        if git_ok(vdir, "merge-base", "--is-ancestor", c, "upstream/master"):
            return c
    raise LabsError(f"{rev} has no commit from upstream/master; was it cut with `labs.py cut`?")


def labs_commits(vdir, rev):
    """(sha, subject, is_merge) for the first-parent commits after the base, oldest first."""
    base = release_base(vdir, rev)
    out = git(vdir, "log", "--first-parent", "--reverse", "--format=%H%x00%P%x00%s",
              f"{base}..{rev}")
    res = []
    for line in out.splitlines():
        sha, parents, subj = line.split("\x00", 2)
        res.append((sha, subj, len(parents.split()) > 1))
    return res


def merged_topics(vdir, rev) -> dict:
    """topic name -> (merge commit, topic tip merged) on `rev`."""
    res = {}
    for sha, subj, is_merge in labs_commits(vdir, rev):
        m = MERGE_SUBJECT_RE.match(subj)
        if m and is_merge:
            res[m.group(1)] = (sha, git(vdir, "rev-parse", f"{sha}^2"))
    return res


def glue_commits(vdir, rev) -> list:
    """(sha, subject) of the `labs:` non-merge commits on `rev`, oldest first."""
    return [(sha, subj) for sha, subj, is_merge in labs_commits(vdir, rev)
            if not is_merge and subj.startswith("labs:")]


def topic_ref(t) -> str:
    return f"origin/{t['branch']}"


def verilator_version(vdir, rev="HEAD") -> str:
    m = AC_INIT_RE.search(git(vdir, "show", f"{rev}:configure.ac"))
    if not m:
        raise LabsError("cannot find AC_INIT([Verilator],[<version> ...]) in configure.ac")
    return m.group(1)


def gate_dir(root, branch) -> Path:
    return Path(root) / "build" / "labs" / branch.replace("/", "-")


# --------------------------------------------------------------------------- #
# setup / status / cut
# --------------------------------------------------------------------------- #

def cmd_setup(args) -> int:
    v = args.verilator
    remotes = git(v, "remote").split()
    if "upstream" not in remotes:
        git(v, "remote", "add", "upstream", args.upstream_url)
        print(f"added remote upstream -> {args.upstream_url}")
    git(v, "config", "rerere.enabled", "true")
    git(v, "config", "rerere.autoupdate", "true")
    print("rerere: enabled, autoupdate")
    git(v, "fetch", "--prune", "--tags", "origin")
    git(v, "fetch", "--no-tags", "upstream", "master")
    print("fetched origin (with tags) and upstream/master")
    prev = previous_tag(v)
    print(f"previous labs release: {prev or 'none'}")
    print("next: `labs.py status`, then `labs.py cut YYYY-NN`")
    return 0


def cmd_status(args) -> int:
    v = args.verilator
    topics = load_topics(args.root)["topics"]
    prev = previous_tag(v)
    prev_merged = merged_topics(v, prev) if prev else {}
    print(f"previous release: {prev or 'none'}")
    if prev:
        base = release_base(v, prev)
        n = git(v, "rev-list", "--count", f"{base}..upstream/master")
        print(f"upstream/master: {n} commits since {prev}'s base {base[:10]}")
    print()
    print(f"{'topic':<14} {'branch':<28} {'tip':<11} {'since-prev':>10} {'behind-up':>9}")
    rc = 0
    for t in topics:
        ref = topic_ref(t)
        if not git_ok(v, "rev-parse", "--verify", "--quiet", ref):
            print(f"{t['name']:<14} {t['branch']:<28} MISSING on origin")
            rc = 1
            continue
        tip = git(v, "rev-parse", ref)
        if t["name"] in prev_merged:
            since = git(v, "rev-list", "--count", f"{prev_merged[t['name']][1]}..{tip}")
        else:
            since = "new"
        behind = git(v, "rev-list", "--count", f"{tip}..upstream/master")
        print(f"{t['name']:<14} {t['branch']:<28} {tip[:10]:<11} {since:>10} {behind:>9}")
    dropped = sorted(set(prev_merged) - {t["name"] for t in topics})
    if dropped:
        print(f"\ndropped since {prev}: {', '.join(dropped)}")
    return rc


def cmd_cut(args) -> int:
    v = args.verilator
    branch = f"labs/{args.name}"
    if not BRANCH_RE.match(branch):
        raise LabsError(f"release name must be YYYY-NN, got {args.name!r}")
    require_clean(v)
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        if git_ok(v, "rev-parse", "--verify", "--quiet", ref):
            raise LabsError(f"{ref} already exists")
    git(v, "switch", "--no-track", "-c", branch, "upstream/master")
    print(f"created {branch} at upstream/master {git(v, 'rev-parse', '--short', 'HEAD')}")
    print("next: `labs.py merge`")
    return 0


# --------------------------------------------------------------------------- #
# merge / glue
# --------------------------------------------------------------------------- #

def _show_reference(v, prev, name, prev_merged):
    if prev and name in prev_merged:
        sha = prev_merged[name][0]
        print(f"reference: how {prev} resolved this merge:\n"
              f"  git -C {v} show --remerge-diff {sha[:12]}")
    else:
        print("reference: none (topic is new since the previous release)")


def cmd_merge(args) -> int:
    v = args.verilator
    branch = require_labs_branch(v)
    topics = load_topics(args.root)["topics"]
    prev = previous_tag(v, branch)
    prev_merged = merged_topics(v, prev) if prev else {}
    if in_progress(v) == "merge":
        print("a merge is still in progress: resolve the conflicts, `git add` them, "
              "`git commit --no-edit`, then re-run `labs.py merge`")
        if unmerged_files(v):
            print("unresolved:\n  " + "\n  ".join(unmerged_files(v)))
        return 2
    require_clean(v)
    if glue_commits(v, "HEAD"):
        raise LabsError("glue commits are already on this branch; merge all topics before `glue`")
    done = merged_topics(v, "HEAD")
    for t in topics:
        if t["name"] in done:
            print(f"merged:  {t['name']}")
            continue
        ref = topic_ref(t)
        if not git_ok(v, "rev-parse", "--verify", "--quiet", ref):
            raise LabsError(f"topic {t['name']}: {ref} not found (run `labs.py setup` to fetch)")
        msg = f"labs: merge {t['name']} ({t['branch']})"
        r = subprocess.run(["git", "-C", str(v), "merge", "--no-ff", "-m", msg, ref],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode == 0:
            print(f"merging: {t['name']} ... clean")
            continue
        if in_progress(v) != "merge":
            raise LabsError(f"git merge {ref} failed:\n{r.stdout.strip()}")
        left = unmerged_files(v)
        print(f"merging: {t['name']} ... CONFLICT")
        if left:
            print("unresolved:\n  " + "\n  ".join(left))
        else:
            print("rerere replayed earlier resolutions for every conflict; review them "
                  "(`git diff --cached`)")
        _show_reference(v, prev, t["name"], prev_merged)
        print("then: `git add` the files, `git commit --no-edit`, and re-run `labs.py merge`")
        return 2
    print("all topics merged. next: `labs.py glue`")
    return 0


def cmd_glue(args) -> int:
    v = args.verilator
    branch = require_labs_branch(v)
    topics = load_topics(args.root)["topics"]
    if in_progress(v) == "cherry-pick":
        print("a cherry-pick is still in progress: resolve, `git add`, "
              "`git cherry-pick --continue`, then re-run `labs.py glue`")
        return 2
    require_clean(v)
    missing = [t["name"] for t in topics if t["name"] not in merged_topics(v, "HEAD")]
    if missing:
        raise LabsError(f"topics not merged yet: {', '.join(missing)}; run `labs.py merge`")
    prev = previous_tag(v, branch)
    if not prev:
        print("no previous release, so no glue to carry forward")
        return 0
    here = glue_commits(v, "HEAD")
    applied_subjects = {s for _, s in here}
    picked_from = set()
    for sha, _ in here:
        for m in re.finditer(r"cherry picked from commit ([0-9a-f]{40})",
                             git(v, "show", "-s", "--format=%B", sha)):
            picked_from.add(m.group(1))
    for sha, subj in glue_commits(v, prev):
        if sha in picked_from or subj in applied_subjects:
            print(f"present: {sha[:10]} {subj}")
            continue
        r = subprocess.run(["git", "-C", str(v), "cherry-pick", "-x", sha],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode == 0:
            print(f"picked:  {sha[:10]} {subj}")
            continue
        if in_progress(v) == "cherry-pick" and not unmerged_files(v) \
                and not git(v, "status", "--porcelain", "--untracked-files=no"):
            # Empty: the fix now lives in a topic or upstream.
            git(v, "cherry-pick", "--skip")
            print(f"dropped: {sha[:10]} {subj} (now empty; already in a topic or upstream)")
            continue
        print(f"CONFLICT: {sha[:10]} {subj}")
        print("unresolved:\n  " + "\n  ".join(unmerged_files(v)))
        print("decide: resolve it and `git cherry-pick --continue`, or drop it with "
              "`git cherry-pick --skip` if it no longer applies; then re-run `labs.py glue`")
        return 2
    print("glue done. next: `labs.py test`")
    return 0


# --------------------------------------------------------------------------- #
# test
# --------------------------------------------------------------------------- #

DRIVER_FLAGS = ["-j", "0", "--quiet", "--rerun"]
SCENARIOS = ["--vlt", "--vltmt", "--dist"]
DONE_RE = re.compile(r"==TESTS DONE, .*?Passed (\d+)\s+Failed (\d+)")


def configure_flags(topics, extra, omit=()) -> list:
    """Topic configure flags, with `extra` replacing any flag of the same name
    (e.g. --with-dv-solve=/opt/dvs replaces --with-dv-solve) and flags named
    in `omit` dropped (a platform that cannot build the topic's dependency)."""
    def key(f):
        return f.split("=", 1)[0]
    drop = {key(f) for f in extra} | {key(f) for f in omit}
    flags = [f for t in topics for f in t.get("configure", []) if key(f) not in drop]
    return flags + list(extra)


def run_logged(cmd, cwd, log: Path, env=None) -> int:
    print(f"  $ {' '.join(shlex.quote(c) for c in cmd)}  > {log}")
    with open(log, "a") as fh:
        fh.write(f"$ {' '.join(cmd)}\n")
        fh.flush()
        return subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                              env=env).returncode


def build(vdir, flags, logdir: Path, jobs) -> None:
    log = logdir / "build.log"
    log.write_text("")
    for cmd in (["autoconf"], ["./configure", *flags], ["make", f"-j{jobs}"]):
        if run_logged(cmd, vdir, log) != 0:
            raise LabsError(f"build failed at `{cmd[0]}`; see {log}")


def run_driver(vdir, tests, log: Path) -> str:
    """Run test_regress/driver.py; returns a gate value: pass | fail:<n>."""
    log.write_text("")
    env = dict(os.environ, VERILATOR_ROOT=str(vdir))
    rc = run_logged([sys.executable, "driver.py", *DRIVER_FLAGS, *SCENARIOS, *tests],
                    Path(vdir) / "test_regress", log, env=env)
    m = None
    for m in DONE_RE.finditer(log.read_text(errors="replace")):
        pass
    failed = int(m.group(2)) if m else (0 if rc == 0 else -1)
    if rc == 0 and failed == 0:
        return "pass"
    return f"fail:{failed if failed > 0 else 1}"


def expand_tests(vdir, patterns) -> list:
    tdir = Path(vdir) / "test_regress" / "t"
    found = []
    for pat in patterns:
        hits = sorted(tdir.glob(pat if pat.endswith(".py") else pat + ".py"))
        if not hits:
            raise LabsError(f"test pattern {pat!r} matches nothing in test_regress/t")
        found += [f"t/{h.name}" for h in hits if f"t/{h.name}" not in found]
    return found


def cmd_test(args) -> int:
    v = args.verilator
    branch = require_labs_branch(v)
    require_clean(v)
    data = load_topics(args.root)
    head = git(v, "rev-parse", "HEAD")
    logdir = gate_dir(args.root, branch)
    logdir.mkdir(parents=True, exist_ok=True)
    flags = configure_flags(data["topics"], args.configure_arg or [])
    print(f"building {branch} {head[:10]} with: {' '.join(flags) or '(no configure flags)'}")
    build(v, flags, logdir, args.jobs)
    gate = {}
    topic_tests = expand_tests(v, [p for t in data["topics"] for p in t.get("tests", [])])
    cross_tests = expand_tests(v, data.get("cross_tests", []))
    for key, tests in (("topic_tests", topic_tests), ("cross_tests", cross_tests)):
        if not tests:
            gate[key] = "none"
            continue
        print(f"{key}: {len(tests)} tests")
        gate[key] = run_driver(v, tests, logdir / f"{key}.log")
    if args.quick:
        gate["upstream_suite"] = "skipped"
    else:
        print("upstream_suite: full test_regress (long)")
        gate["upstream_suite"] = run_driver(v, [], logdir / "upstream_suite.log")
    result = {"branch": branch, "head": head, "configure": flags, "gate": gate}
    (logdir / "gate.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(gate))
    ok = all(x in ("pass", "none") for k, x in gate.items() if k != "upstream_suite") \
        and gate["upstream_suite"] in ("pass", "skipped")
    print(f"gate: {'PASS' if ok else 'FAIL'}{' (quick: upstream suite skipped)' if args.quick else ''}"
          f"; logs in {logdir}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# record / tag / release-notes
# --------------------------------------------------------------------------- #

def build_record(v, root, branch, gate_json, topics, notes=None) -> dict:
    head = git(v, "rev-parse", "HEAD")
    merged = merged_topics(v, "HEAD")
    by_name = {t["name"]: t for t in topics}
    rec_topics = []
    for name, (merge, sha) in merged.items():
        t = by_name.get(name, {})
        entry = {"name": name, "branch": t.get("branch", "?"), "sha": sha, "merge": merge}
        for k in ("upstream", "enabled_by"):
            if k in t:
                entry[k] = t[k]
        rec_topics.append(entry)
    tag = tag_for(verilator_version(v), branch)
    gate = {k: gate_json["gate"][k] for k in GATE_KEYS}
    if notes:
        gate["notes"] = notes
    return {
        "tag": tag,
        "branch": branch,
        "head": head,
        "upstream": {"ref": "master", "sha": release_base(v, "HEAD")},
        "previous": previous_tag(v, branch),
        "topics": rec_topics,
        "glue": [{"sha": s, "subject": subj} for s, subj in glue_commits(v, "HEAD")],
        "test_gate": gate,
    }


def cmd_record(args) -> int:
    v = args.verilator
    branch = require_labs_branch(v)
    require_clean(v)
    topics = load_topics(args.root)["topics"]
    missing = [t["name"] for t in topics if t["name"] not in merged_topics(v, "HEAD")]
    if missing:
        raise LabsError(f"topics not merged: {', '.join(missing)}")
    gpath = gate_dir(args.root, branch) / "gate.json"
    if not gpath.exists():
        raise LabsError("no test results; run `labs.py test` first")
    gate_json = json.loads(gpath.read_text())
    head = git(v, "rev-parse", "HEAD")
    if gate_json["head"] != head:
        raise LabsError(f"last test ran on {gate_json['head'][:10]}, HEAD is {head[:10]}; "
                        f"re-run `labs.py test`")
    g = gate_json["gate"]
    if g.get("upstream_suite") == "skipped":
        raise LabsError("the last test run was --quick; the full upstream suite must pass")
    failed = [k for k in GATE_KEYS if not g.get(k, "").startswith(("pass", "none"))]
    if failed and not args.waive:
        raise LabsError(f"gate failed ({', '.join(f'{k}={g[k]}' for k in failed)}); fix it, "
                        f"or record anyway with --waive \"<why>\"")
    rec = build_record(v, args.root, branch, gate_json, topics, notes=args.waive)
    errs = check_record(rec, f"{rec['tag']}.yaml", {t["name"] for t in topics})
    if errs:
        raise LabsError("generated record is invalid:\n  " + "\n  ".join(errs))
    out = Path(args.root) / "releases" / f"{rec['tag']}.yaml"
    out.parent.mkdir(exist_ok=True)
    out.write_text("# Written by scripts/labs.py record. Do not edit.\n"
                   + yaml.safe_dump(rec, sort_keys=False))
    print(f"wrote {out.relative_to(args.root)}")
    print("next: `labs.py tag`, then commit the record")
    return 0


def render_notes(rec) -> str:
    base = rec["upstream"]["sha"]
    lines = [
        f"Verilator Labs {rec['tag']}: upstream Verilator master plus the topics below.",
        "",
        f"Source: https://github.com/{ORIGIN_SLUG}/tree/{rec['tag']} "
        f"(branch `{rec['branch']}`)",
        f"Upstream base: verilator/verilator master @ `{base[:12]}`",
    ]
    if rec.get("previous"):
        lines.append(f"Previous labs release: {rec['previous']}")
    lines += ["", "## Topics", "",
              "| Topic | Branch | Commit | Enabled by | Upstream |",
              "|-------|--------|--------|------------|----------|"]
    for t in rec["topics"]:
        lines.append(f"| {t['name']} | `{t['branch']}` | `{t['sha'][:12]}` | "
                     f"{('`' + t['enabled_by'] + '`') if t.get('enabled_by') else ''} | "
                     f"{t.get('upstream', '')} |")
    if rec.get("glue"):
        lines += ["", "## Integration fixes (glue)", ""]
        lines += [f"- `{g['sha'][:12]}` {g['subject']}" for g in rec["glue"]]
    g = rec["test_gate"]
    lines += ["", "## Test gate", "",
              f"- Upstream regression: {g['upstream_suite']}",
              f"- Topic tests: {g['topic_tests']}",
              f"- Cross-feature tests: {g['cross_tests']}"]
    if g.get("notes"):
        lines.append(f"- Notes: {g['notes']}")
    return "\n".join(lines) + "\n"


def read_record(root, tag):
    p = Path(root) / "releases" / f"{tag}.yaml"
    return _load(p) if p.exists() else None


def cmd_tag(args) -> int:
    v = args.verilator
    branch = require_labs_branch(v)
    require_clean(v)
    tag = tag_for(verilator_version(v), branch)
    rec = read_record(args.root, tag)
    if rec is None:
        raise LabsError(f"releases/{tag}.yaml not found; run `labs.py record` first")
    head = git(v, "rev-parse", "HEAD")
    if rec["head"] != head:
        raise LabsError(f"record is for {rec['head'][:10]}, HEAD is {head[:10]}; re-run test/record")
    if git_ok(v, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
        raise LabsError(f"tag {tag} already exists")
    msg = render_notes(rec)
    # verbatim: the default cleanup would strip the Markdown '#' headings.
    subprocess.run(["git", "-C", str(v), "tag", "-a", "--cleanup=verbatim", "-F", "-", tag, head],
                   input=msg, text=True, check=True)
    print(f"tagged {tag} at {head[:10]} (local only)")
    print(f"next: commit releases/{tag}.yaml in verilator-labs-bin, then `labs.py release-tag`")
    return 0


def split_release_tag(tag):
    """Release tag -> (source tag, re-spin number or None)."""
    m = RESPIN_RE.match(tag)
    if m:
        return m.group(1), int(m.group(2))
    if TAG_RE.match(tag):
        return tag, None
    raise LabsError(f"{tag!r} is not a labs tag (v<ver>-labs.<YYYY>.<NN>[.<N>])")


def cmd_release_tag(args) -> int:
    root, v = args.root, args.verilator
    base = args.tag or tag_for(verilator_version(v), require_labs_branch(v))
    split_release_tag(base)
    if RESPIN_RE.match(base):
        raise LabsError(f"give the base tag and --respin N, not {base}")
    if (args.respin is None) != (args.note is None):
        raise LabsError("--respin and --note go together")
    if args.respin is not None and args.respin < 1:
        raise LabsError("--respin must be 1 or more")
    path = f"releases/{base}.yaml"
    if not git_ok(root, "cat-file", "-e", f"HEAD:{path}"):
        raise LabsError(f"{path} is not committed at HEAD here; commit the record first")
    if git(root, "status", "--porcelain"):
        raise LabsError(f"{root} has uncommitted changes; commit or stash them first")
    rec = yaml.safe_load(git(root, "show", f"HEAD:{path}"))
    src = git(v, "rev-parse", "--verify", "--quiet", f"refs/tags/{base}^{{commit}}", check=False)
    if not src:
        raise LabsError(f"source tag {base} not found in {v}; run `labs.py tag` there first")
    if src != rec["head"]:
        raise LabsError(f"source tag {base} is at {src[:10]}, the record's head is "
                        f"{rec['head'][:10]}")
    if args.respin is None:
        tag, msg = base, f"Verilator Labs {base}"
    else:
        tag, msg = f"{base}.{args.respin}", " ".join(args.note.split())
        if not msg:
            raise LabsError("--note is empty")
    if git_ok(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
        raise LabsError(f"tag {tag} already exists here")
    git(root, "tag", "-a", "-m", msg, tag, "HEAD")
    print(f"tagged {tag} at {git(root, 'rev-parse', '--short', 'HEAD')} here (local only)")
    print("to release, after review, push in this order:")
    if args.respin is None:
        print(f"  git -C {v} push origin {rec['branch']}")
        print(f"  git -C {v} push origin {base}")
    print(f"  git -C {root} push origin HEAD")
    print(f"  git -C {root} push origin {tag}")
    return 0


def cmd_check_release(args) -> int:
    root = args.root
    base, rev = split_release_tag(args.tag)
    rec = read_record(root, base)
    if rec is None:
        raise LabsError(f"releases/{base}.yaml is not in this checkout")
    r = subprocess.run(["git", "ls-remote", "--tags", args.origin_url, f"refs/tags/{base}^{{}}"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise LabsError(f"git ls-remote {args.origin_url} failed:\n{r.stderr.strip()}")
    shas = [line.split()[0] for line in r.stdout.splitlines() if line.strip()]
    if not shas:
        raise LabsError(f"source tag {base} is missing on {args.origin_url} (or is not "
                        "annotated); push it before the release tag")
    if shas[0] != rec["head"]:
        raise LabsError(f"source tag {base} on {args.origin_url} is at {shas[0][:10]}, "
                        f"releases/{base}.yaml says {rec['head'][:10]}")
    note = ""
    if rev is not None:
        note = git(root, "for-each-ref", "--format=%(contents)", f"refs/tags/{args.tag}",
                   check=False)
        note = " ".join(note.split())   # one line, for $GITHUB_OUTPUT
    print(f"core_ref={base}")
    print(f"revision={'' if rev is None else rev}")
    print(f"revision_note={note}")
    return 0


def cmd_configure_flags(args) -> int:
    for f in configure_flags(load_topics(args.root)["topics"], args.configure_arg or [],
                             args.omit or []):
        print(f)
    return 0


def cmd_release_notes(args) -> int:
    tag, _ = split_release_tag(args.tag)
    rec = read_record(args.root, tag)
    if rec is not None:
        sys.stdout.write(render_notes(rec))
        return 0
    # The record may not be committed yet; the source tag message is the same notes.
    v = args.verilator
    if Path(v).is_dir() and git_ok(v, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
        sys.stdout.write(git(v, "tag", "-l", "--format=%(contents)", tag) + "\n")
        return 0
    raise LabsError(f"no releases/{tag}.yaml and no tag {tag} in {v}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="labs.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(REPO_ROOT),
                   help="verilator-labs-bin checkout (default: this script's repo)")
    p.add_argument("--verilator", default=default_verilator_dir(),
                   help="Verilator clone to integrate in (default: $IVPM_PACKAGES/verilator)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("setup", help="add upstream remote, enable rerere, fetch")
    sp.add_argument("--upstream-url", default=UPSTREAM_URL)
    sub.add_parser("status", help="changes in upstream and topics since the last release")
    sp = sub.add_parser("cut", help="create labs/NAME from upstream/master")
    sp.add_argument("name", help="release name YYYY-NN, e.g. 2026-02")
    sub.add_parser("merge", help="merge topics in manifest order (re-run to resume)")
    sub.add_parser("glue", help="cherry-pick the previous release's labs: glue commits")
    sp = sub.add_parser("test", help="build and run the test gate")
    sp.add_argument("--quick", action="store_true",
                    help="skip the full upstream suite (cannot be recorded)")
    sp.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    sp.add_argument("--configure-arg", action="append", metavar="FLAG",
                    help="extra configure flag; replaces a topic flag of the same name "
                         "(e.g. --configure-arg=--with-dv-solve=/opt/dv-solve)")
    sp = sub.add_parser("record", help="write releases/<tag>.yaml for the tested tip")
    sp.add_argument("--waive", metavar="WHY",
                    help="record despite gate failures, with this explanation")
    sub.add_parser("tag", help="create the annotated source tag in the clone (local only)")
    sp = sub.add_parser("release-notes", help="render a release's notes as Markdown")
    sp.add_argument("tag", help="source or release tag (a re-spin uses its base record)")
    sp = sub.add_parser("release-tag",
                        help="tag the record commit here to trigger the release (local only)")
    sp.add_argument("tag", nargs="?",
                    help="source tag (default: from the clone's labs branch)")
    sp.add_argument("--respin", type=int, metavar="N", help="packaging re-spin: tag <tag>.N")
    sp.add_argument("--note", metavar="TEXT", help="what the re-spin changes (its tag message)")
    sp = sub.add_parser("check-release",
                        help="CI: check a release tag; print core_ref/revision/revision_note")
    sp.add_argument("tag")
    sp.add_argument("--origin-url", default=os.environ.get("LABS_ORIGIN_URL", ORIGIN_URL),
                    help=f"where the source tags live (default: $LABS_ORIGIN_URL or {ORIGIN_URL})")
    sp = sub.add_parser("configure-flags",
                        help="CI: print the topics' Verilator configure flags, one per line")
    sp.add_argument("--configure-arg", action="append", metavar="FLAG",
                    help="extra flag; replaces a topic flag of the same name")
    sp.add_argument("--omit", action="append", metavar="FLAG",
                    help="drop the topic flag of this name")
    sub.add_parser("validate", help="check topics.yaml and releases/*.yaml")
    args = p.parse_args(argv)
    cmds = {"setup": cmd_setup, "status": cmd_status, "cut": cmd_cut, "merge": cmd_merge,
            "glue": cmd_glue, "test": cmd_test, "record": cmd_record, "tag": cmd_tag,
            "release-notes": cmd_release_notes, "release-tag": cmd_release_tag,
            "check-release": cmd_check_release, "configure-flags": cmd_configure_flags,
            "validate": cmd_validate}
    try:
        return cmds[args.cmd](args)
    except LabsError as e:
        print(f"labs.py {args.cmd}: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

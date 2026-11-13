# Plan: verilator-labs-bin releases

Design: [docs/design/labs-release.md](../design/labs-release.md). This file is
the build-out plan and its status. Update the Status table as steps finish.

## What this repo is for

1. **CI.** Build and publish a release when a release tag
   (`v<verilator-version>-labs.<labs-version>`, e.g. `v5.051-labs.2026.02`) is
   pushed to this repo. The build uses the source tag of the same name on
   mballance/verilator. It is a thin caller of edapack-common
   `build-release.yml@v1`, using its existing **release track**.
2. **Integration tooling.** Scripts and Claude skills that form a
   `labs/<version>` branch: `upstream/master` plus topic branches from
   mballance/verilator, merged, made to work together, tested and then tagged.
   The work is done in `packages/verilator` (the ivpm `dev` dependency).
3. **Metadata.** `topics.yaml` lists what labs carries now. `releases/<tag>.yaml`
   records exactly what each release was built from. The commit that adds it
   carries the release tag. The same content is the source tag's message, so
   the source repo carries it too.

## Status

| Step | State | Note |
|------|-------|------|
| 0. Settle open questions, update design doc | done | all decided 2026-11-13; design doc updated. Trigger revised 2026-11-13: release tag pushed here, not cron (see Release identity) |
| 1. Metadata schema + validator | done | `topics.yaml` (dvs), `releases/README.md`, `scripts/labs.py validate`, `tests/test_labs_validate.py` (21 pass) |
| 2. Integration CLI (`scripts/labs.py`) | done | all subcommands, including `release-tag` and `check-release` for the tag trigger; `tests/test_labs_flow.py` runs releases on fake repos with a bare repo as mballance/verilator (13 tests; 34 pass with the validator's 21); `setup`/`status` run on the real clone. `test` not yet run for real (needs dv-solve, step 5) |
| 3. Integration skill | done | `skills/labs-integrate/SKILL.md` (flow, stop points, conflicts, fix vs glue, gate triage, summary, re-spin) and `skills/labs-topic/SKILL.md`. Not yet exercised on a real release (step 5) |
| 4. CI recipe (copy of verilator-bin) + dv-solve build/ship | done | recipe copied and adapted (4a), incl. ci.yml release-only, release-tag.yml, release-notes-hook.sh, `labs.py configure-flags`. 4b done: build.sh builds dv-solve (static libs, .pc, headers, licences) into `share/verilator/dv-solve` and passes `--with-dv-solve=<prefix>`; on Windows a failed dv-solve build drops the flag (`configure-flags --omit`). `tests/run_smoke_test.sh` runs a `--solver-dvs` model with `DV_SOLVE_ROOT` unset. Checked locally with dvs-integ + dv-solve 52262e3: passes in place, fails once relocated (baked `DEFENV_DV_SOLVE_ROOT`), passes with `DV_SOLVE_ROOT` set. 4d done: `dvs-integ` fcdfb3460 (pushed to mballance/verilator) makes `getenvDV_SOLVE_ROOT` fall back to `$VERILATOR_ROOT/dv-solve` when the variable is unset or the baked path is missing; the relocated smoke test passes with `DV_SOLVE_ROOT` unset. 4c done: release-tag.yml checked in a private scratch repo (mballance/labs-trigger-test, stub ci.yml, now archived): no record -> clear error; record but no source tag -> clear error; with the source tag -> ci.yml dispatched at the tag with core_ref; re-spin `.1` -> revision=1 and the tag message as revision_note. Release tag == source tag and the body from the hook are checked by the first real release (step 5). |
| 5. First release `labs/2026-01` | todo | |
| 6. Sync/authority onboarding, first consumer | todo | |

Next action: step 4d, in the `dvs` topic make `getenvDV_SOLVE_ROOT` fall back to `$VERILATOR_ROOT/dv-solve` when the baked path is missing (relocation, item 5 of the dv-solve section).

## Release identity (decided 2026-11-13)

- **The release marker is a tag**, not the branch. `labs/2026-02` is the
  integration workspace and can be pushed while still in progress.
- **Two tags with the same name** (revised 2026-11-13; design: Versions and
  tags):
  - the **source tag** `v5.051-labs.2026.02` on mballance/verilator: annotated,
    on the tested tip, its message the release record. CI builds this ref.
  - the **release tag** of the same name here, on the commit that adds
    `releases/<tag>.yaml`. Its push is the release trigger, and it freezes the
    recipe used.
  Order: push the labs branch and source tag first, then the record commit and
  release tag here.
- **Version = `v<verilator-version>-<labs-version>`.** The Verilator part comes
  from `AC_INIT` in `configure.ac` on the labs branch (so `5.051`, without
  `devel`). The labs part is `labs.<YYYY>.<NN>`, from the branch name.
  The tag name becomes the release tag and the ivpm version, unchanged.

Why this works with edapack-common as it is (checked in
`edapack-common/.github/workflows/build-release.yml` and
`scripts/resolve-inputs.py`):

- **A push never publishes.** `publish` is gated on
  `github.event_name != 'push'` (mirror sync safety), so the tag push cannot
  call `build-release.yml` itself. A trigger workflow here
  (`on: push: tags: ['v*-labs.*']`) checks the tag and then runs
  `gh workflow run ci.yml --ref <tag> -f core_ref=<base-tag>`. `GITHUB_TOKEN`
  (with `actions: write`) may trigger `workflow_dispatch`, and a dispatch
  publishes. The authority gate and "already released?" check still apply, so a
  tag the mirror copies to the other forge cannot publish twice.
- **The version comes from the core ref's name.** With `core_ref` set to the
  source tag, the release track's version is `_strip_v(tag)` =
  `5.051-labs.2026.02`, and the release tag is `v` + that. So the published
  release tag equals both tags. The "already released?" check
  (`gh release view <tag>`) is then exact: each labs tag builds once, as a full
  release, with no build-id suffix. This is why the source tag is still needed:
  a branch or SHA `core_ref` would give a bad version.
- `release_policy: 'latest-tag:^v\d+\.\d+-labs\.\d{4}\.\d{2}$'` stays in
  `build-inputs.yaml`, because the release track requires one. The explicit
  `core_ref` overrides it.
- Tags sort by their numbers: Verilator version first, then year and NN. Both
  only move forward, because labs always follows master.
- A packaging re-spin of a published labs release pushes the release tag
  `v5.051-labs.2026.02.1` on the fix commit here. The trigger dispatches with
  `core_ref=v5.051-labs.2026.02` and `revision=1`, and the re-spin tag's
  message becomes `revision_note`.
- The earlier problem (a branch `core_ref` gave a `v2026-02.<run-id>` tag) does
  not arise, and the dev track is not used at all. edapack-common needs no
  change, as the design says.

Labs-version format: `labs.2026.02` is my recommendation over a bare
`2026.02`. `v5.051-2026.02` would look like a variant of upstream's own
`v5.051`. The word `labs` makes the version obviously ours in ivpm output and
bug reports. Dots rather than dashes keep it one semver pre-release-style
suffix.

## Findings from the current state

- `packages/verilator` clones mballance/verilator with only an `origin` remote.
  Tooling must add `upstream` (verilator/verilator) and turn on `rerere`.
- The topic branches the design names (`dvs-integ`, `debug-proto`) do not exist
  yet. Branches that do exist and look like topics: `config-solver`,
  `efficient-waits`, `covergroup-rt` / `-a2` / `-a3`, `funccov-*`,
  `mballance/funccov*`. Their naming is mixed (bare vs `mballance/` prefix).
- **Release body.** The release track's notes step reads `release_notes` (the
  upstream changelog). For labs, omit `release_notes` and supply the body
  through `scripts/release-notes-hook.sh`, which is appended to the release
  body. Check in step 4 that omitting it falls back cleanly ("default notes").

## Step 0: decisions

Decided 2026-11-13:

- **Q1. Ready marker:** an annotated tag (see Release identity).
- **Q2. Version:** `v<verilator-version>-labs.<YYYY>.<NN>`. Trigger: pushing
  the release tag to this repo, which dispatches the release workflow (revised
  2026-11-13; it was cron plus `workflow_dispatch`). No cron.
  `workflow_dispatch` stays for rebuilding a tag by hand.
- **Q3. Branch names vary, so each topic gives its full branch name** in
  `topics.yaml`. There is no naming convention, and the tooling never derives a
  branch from a topic name.
- **Q4. First release carries one topic: dv-solve integration (`dvs-integ`).**
- **Q5. Integration skills live in `skills/`** (`skills/labs-integrate/`,
  `skills/labs-topic/`). The shipped `verilator` user skill also goes in
  `skills/verilator/`. Only that one is listed in `scripts/skill-manifest.yaml`,
  so the integration skills never end up in the release tarball.

### Q6: how `--version` shows labs (accepted 2026-11-13)

`verilator --version` prints `PACKAGE_STRING` (from `AC_INIT`) plus
` rev <config_rev>`. `src/config_rev` uses `$VERILATOR_SRC_VERSION` if set
(the hook upstream provides for distro packagers), and otherwise
`git describe --always`.

**No source change at all. The revision string carries the labs version.**

- On the tagged commit, `git describe` returns the tag. edapack-common's
  `ec_clone_input` does a full `git clone`, so tags are present.
- To avoid depending on clone details, the recipe's `scripts/build.sh` also
  exports `VERILATOR_SRC_VERSION="$EC_TAG"` before building Verilator.
- Result: `Verilator 5.055 devel rev v5.055-labs.2026.02`. "labs" is visible
  in every `--version` and bug report, and the full tag is there to look up.
- A local build of the labs branch before tagging shows
  `rev v5.0xx-<n>-g<sha>`, which correctly identifies it as untagged.

Why not a `labs: version` commit that edits `AC_INIT` (e.g. `5.055 devel labs`):
upstream changes that line at every version bump, so the commit would conflict
in almost every release. It would also have to be carried forward as glue each
time. Using the packager hook means nothing to merge and nothing to carry.

So there is no `version` CLI command and no `labs: version` commit.

## dvs-integ: current state

- Pushed to mballance/verilator on 2026-11-13; tip `33a6fc60c`.
- 4 commits on a base 153 commits behind upstream master (as of 2026-11-13):
  `0e77eccb6` (solver back-end hooks), `2d65fdce6` (`--solver-dvs`),
  `d196eef16` (`nodist/dvs_regress`), `33a6fc60c` (`make format`).
  The labs merge brings it onto current master, so a rebase isn't needed for
  labs.
- It is enabled at build time with `configure --with-dv-solve[=PREFIX]` (off by
  default, found via pkg-config `dv-solve` / `dv-solve-writer`). At run time it
  is enabled with `--solver-dvs`. Labs ships with it built in, so the recipe
  passes `--with-dv-solve`.

## dv-solve: built and shipped by verilator-labs-bin

Labs must build dv-solve from source and ship it inside the release tarball.
`--solver-dvs` uses it in two places:

- **In `verilator_bin`:** configure links `dv_solve_writer` (a static library
  that builds problem images) into Verilator itself.
- **In every Verilated model** that uses `--solver-dvs`: the generated
  makefile/CMake links dv-solve's static runtime (`dv_solve_rt`) from
  `$(DV_SOLVE_ROOT)`. So the user's machine needs dv-solve's headers, static
  libraries and `.pc` files at model build time, not just a shared library at
  run time.

Source state (checked 2026-11-13):

- Repo: `https://github.com/fvutils/dv-solve` (Apache-2.0). Latest tag `v0.5.0`.
- **The embeddable libraries are on dv-solve `main` as of 2026-11-13**
  (`52262e3`, a fast-forward of `wpk/verilator-dvs-integ`; a clean build passed
  all 44 CTest tests before the push). They are not in a tag yet: `v0.5.0`
  predates them. Labs pins `52262e3` until a dv-solve tag that contains them
  exists.
- Build: CMake ≥ 3.14, C11, plus C++ for the CaDiCaL backend. Kissat is
  vendored. CaDiCaL is **not** vendored: it is fetched by ivpm (`rel-3.0.1`) and
  found through `PACKAGES_DIR` / `-DCADICAL_SRC_ROOT`.
- The `.pc` files find the prefix from their own location, so a staged install
  stays valid after relocation. That handles the pkg-config side.

What the recipe does (step 4):

1. **Build inputs.** In `build-inputs.yaml`, add `dv-solve`
   (`policy: latest-tag` once it is tagged, until then pinned through
   `input_overrides` to the merged commit) and `cadical`
   (`tag:rel-3.0.1`, matching dv-solve's `ivpm.yaml`). Both are recorded in
   the release manifest like bitwuzla. Labs should not follow dv-solve `main`
   blindly, so a dv-solve bump counts as a labs change and is recorded in
   `releases/<tag>.yaml`.
2. **Build dv-solve first** in `scripts/build.sh`, on every target, with the
   same toolchain as Verilator (manylinux devtoolset, macOS, MSYS2):
   `cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_POSITION_INDEPENDENT_CODE=ON
   -DCADICAL_SRC_ROOT=<cadical> -DDV_SOLVE_BUILD_TOOLS=OFF
   -DCMAKE_INSTALL_PREFIX=<stage>/share/verilator/dv-solve` (prefix to be
   settled), then `cmake --install`.
3. **Configure Verilator** with `--with-dv-solve=<that prefix>`.
4. **Ship** the dv-solve prefix inside the tarball: headers, `libdv_solve_writer.a`,
   `libdv_solve_rt.a`, `.pc` files, and the shared `libdv_solve.so` and
   `dv-solve-smt2` only if wanted (default: no, to keep it small). Add dv-solve
   and its bundled solvers' licences (Apache-2.0, kissat MIT, CaDiCaL MIT)
   next to Verilator's.
5. **Relocation.** configure bakes `CFG_DV_SOLVE_ROOT` into `verilator_bin`
   as `DEFENV_DV_SOLVE_ROOT`, an absolute build-machine path. An installed
   tarball lives elsewhere. Fix in the topic (preferred, since upstream
   packagers hit the same problem): when `DV_SOLVE_ROOT` is unset and the
   baked path does not exist, use a path relative to `VERILATOR_ROOT`
   (`$VERILATOR_ROOT/share/verilator/dv-solve` or wherever step 2 lands). As a
   fallback, the recipe's wrapper / `linux-post-install.sh` sets
   `DV_SOLVE_ROOT` as it does `VERILATOR_ROOT`.
6. **Check in CI** with a smoke test: on each target, verilate and build a small
   class with `randomize()` using `--solver-dvs` from the *installed*
   tarball, in a different directory than the build. This proves both the
   relocation and the model-link path.
7. **Platforms.** If dv-solve doesn't build on a target (Windows/MSYS2 is the
   likely one), that target ships without `--with-dv-solve` rather than
   failing. The release notes say so per platform. Windows is already
   `optional`.

Initial `topics.yaml`:

```yaml
topics:
  - name: dvs
    branch: dvs-integ
    upstream: planned
    impact: none                 # check: config_build.h / Makefile_obj changes when not enabled
    enabled_by: --solver-dvs
    configure: [--with-dv-solve] # recipe passes these
    requires_inputs: [dv-solve]
    tests: [t_dvs_*, t_constraint_solver_cmake]
```

## Step 1: metadata

`topics.yaml`: the current topic list, in merge order. Schema as in the design
(`name`, `branch`, `upstream`, `impact`, `requires_inputs`, `tests`), plus
`depends_on` for stacked topics, `enabled_by` (the user-facing switch, for
the release notes) and `configure` (configure flags the recipe passes, only
flags that exist upstream-style in the topic itself). `branch` is always the
full branch name on mballance/verilator.

`releases/<tag>.yaml` (e.g. `releases/v5.051-labs.2026.02.yaml`): written by
`labs record`, never by hand. The same data, rendered as text, is the
annotated tag's message.

```yaml
tag: v5.051-labs.2026.02
branch: labs/2026-02
head: <sha>                     # tested tip; the tag points here
upstream: { ref: master, sha: <sha> }
previous: labs/2026-01
topics:
  - { name: dvs, branch: dvs-integ, sha: <sha>, merge: <merge-commit-sha> }
glue:
  - { sha: <sha>, subject: "labs: ..." }
test_gate:
  upstream_suite: pass          # pass | fail:<n> (with waiver note)
  topic_tests: pass
  cross_tests: pass
  log_summary: "..."
```

`scripts/labs.py validate` checks both files (schema, unique names, order
respects `depends_on`, `requires_inputs` are present in `build-inputs.yaml`).

## Step 2: integration CLI, `scripts/labs.py`

One Python CLI with subcommands. It works on `packages/verilator`. Each command
is safe to re-run and prints what it did and what to do next. All build and test
output goes to log files under `build/labs/<branch>/`. It never pushes.

| Command | Does |
|---------|------|
| `setup` | Add the `upstream` remote, `rerere.enabled=true`, `rerere.autoupdate=true`, fetch both remotes. |
| `status` | For each topic: commits since the previous release's merge, whether it is behind `upstream/master`, and whether it still exists. Shows what changed since the last release. |
| `cut <name>` | Check for a clean tree, then create `labs/<name>` from `upstream/master`. Refuses if the branch exists. |
| `merge [--from <topic>]` | Merge the topics in manifest order with `--no-ff` and the message `labs: merge <topic> (<branch>)`. On conflict it stops and prints the previous release's merge commit for that topic (`git show --remerge-diff`) as the reference. Re-running resumes after the last merged topic. |
| `glue` | List the `labs:` glue commits on the previous release branch and cherry-pick them, stopping on conflict. |
| `test [--quick]` | Build, then run the gate: the upstream suite, each topic's `tests`, and cross-feature tests. Logs go to files, and it prints a pass/fail summary. |
| `record` | Write `releases/<tag>.yaml` from `git log --first-parent` and the last `test` result. Refuses if the tree is dirty or the tip is untested. |
| `tag` | Create the annotated tag `v<AC_INIT>-labs.<YYYY>.<NN>` on the tested tip, with the record as its message. Local only. Refuses if the tip differs from the record's `head`. |
| `release-notes <tag>` | Render a record as Markdown, from `releases/<tag>.yaml` or from the tag message. CI's hook uses this. |

Tests: unit tests on a throwaway git repo fixture (cut, merge, conflict stop
and resume, glue, record). No Verilator build is needed for these.

As built (2026-11-13), `scripts/labs.py`:

- **Default paths.** The clone is `$IVPM_PACKAGES/verilator` (override with
  `--verilator`), and the repo root is this checkout (override with `--root`).
- **Exit codes.** 0 means done, 1 an error, and 2 a stop for a human (a
  conflict).
- **Stateless.** Everything is read back from git. A release's base is the
  first commit on its first-parent chain that `upstream/master` contains.
  Merged topics are the first-parent merges with the subject
  `labs: merge <name> (<branch>)`. Glue is the first-parent non-merge commits
  whose subject starts with `labs:`. The previous release is the newest labs
  tag older than the branch.
- **Topic refs.** Topics are merged from `origin/<branch>`, so `setup` must have
  fetched recently.
- **Merge.** `merge` stops on a conflict and points to
  `git show --remerge-diff <previous merge>`. When rerere has replayed every
  conflict, it says so and still stops for review.
- **Glue.** `glue` cherry-picks with `-x`. It skips commits that are already
  present (by trailer or subject) and drops ones that become empty.
- **Test.** `test` builds in place (`autoconf`, `./configure <topic flags>`,
  `make`) and runs `test_regress/driver.py -j 0 --quiet --rerun --vlt --vltmt
  --dist`: topic tests, `cross_tests`, then the full suite. With `--quick` the
  full suite is skipped, and the run can't be recorded. `--configure-arg`
  replaces a topic flag of the same name, e.g.
  `--configure-arg=--with-dv-solve=/path`. Results go to
  `build/labs/<branch>/gate.json`, and logs go next to it.
- **Record.** `record` refuses if a topic is unmerged, if the last test ran on
  a different HEAD, if the run was `--quick`, or if the gate failed, unless
  `--waive "<why>"` is given. The waive reason goes into the record.
- **Tag and notes.** `tag` uses `--cleanup=verbatim`, so the Markdown headings
  survive. `release-notes` reads the record (a re-spin tag uses its base
  record), then the local source tag.

Additions for the release-tag trigger (done 2026-11-13):

| Command | Does |
|---------|------|
| `release-tag` | Here, not in the clone. Refuses unless `releases/<tag>.yaml` is committed at HEAD, the tree is clean, and the local source tag exists in the clone and points at the record's `head`. Creates the annotated release tag on HEAD (message: a one-line title; for a re-spin, `--respin N --note "<what changed>"` gives tag `<tag>.N` with the note as its message). Local only. Prints the push commands in order: labs branch and source tag to mballance/verilator, then the commit and release tag here. |
| `check-release <tag>` | For CI's trigger workflow. Splits off a re-spin suffix, reads `releases/<base>.yaml` from the checkout, checks `git ls-remote` on mballance/verilator for `refs/tags/<base>^{}` equal to the record's `head`, and prints `core_ref`, `revision` and (from the tag message) `revision_note` as `key=value` lines for `$GITHUB_OUTPUT`. Exits 1 with a clear message if the source tag is missing or differs. |

As built: `release-notes` looks up the base tag's record for a re-spin tag,
and the GitHub API fallback is gone (the record is always in the tagged
checkout). `release-tag` takes the source tag as an optional argument (default:
from the clone's labs branch); for a re-spin it prints only the pushes here.
`check-release` prints `revision=` and `revision_note=` empty for a base tag,
and reads the note from the release tag in the checkout, so CI's checkout must
have that tag. The remote is `--origin-url` or `$LABS_ORIGIN_URL` (default
`https://github.com/mballance/verilator`). `tag` now prints "commit the record,
then `release-tag`" instead of the push commands. `previous_tag` still uses
source tags in the clone. `test_labs_flow.py` covers both commands with the
fixture's bare origin as the fake mballance/verilator.

## Step 3: integration skill

`skills/labs-integrate/SKILL.md` drives the CLI and does the steps that
need judgement:

- resolving merge conflicts, with the previous release's resolution as the
  reference;
- deciding whether a fix belongs in a topic (as a commit on the topic branch)
  or as `labs:` glue;
- triaging test failures, and writing cross-feature tests;
- ending with a summary: what changed since the last release, conflicts met,
  glue added or dropped, test results.

Stop points for the user: before pushing the labs branch, source tag and any
topic-branch fixes to mballance/verilator; before committing the release
record here; and before pushing the release tag here (which starts the
release). The skill runs `release-tag` and hands over its printed push
commands; it never pushes or commits. Fits a wpk packet: "cut labs/YYYY-NN"
through "both tags ready to push".

A small second skill, `labs-topic`, adds, retires or reorders a topic in
`topics.yaml` and checks the change with `validate`.

## Step 4: CI recipe

Copy from verilator-bin: `build-inputs.yaml`, `CMakeLists.txt`, `scripts/`,
`tests/`, `deps/`, `skills/verilator` and `skill-manifest.yaml`, `.forgejo/`
checks, and `.github/workflows/ci.yml`. Then:

- `build-inputs.yaml`: core repo `https://github.com/mballance/verilator`,
  `policy: branch:master` (required by the schema, but unused because there is
  no dev job), `release_policy: 'latest-tag:^v\d+\.\d+-labs\.\d{4}\.\d{2}$'`
  (required by the release track; the dispatch's `core_ref` overrides it),
  and no `release_notes`. Add `dv-solve` and `cadical` (see the dv-solve
  section). Keep bitwuzla, since the default SMT solver path still uses it.
- `scripts/build.sh`: build and ship dv-solve as in "dv-solve: built and
  shipped" (steps 1 to 7). Run configure with each topic's `configure` flags,
  and export `VERILATOR_SRC_VERSION="$EC_TAG"`.
- Smoke test: the installed-tarball `--solver-dvs` test from that section, plus
  a check that `verilator --version` contains `labs`.
- `ci.yml`: verilator-bin's file with the `dev` job removed. Keep only the
  `release` job (full release, moves `latest`), same `targets`, plus the
  `core_ref` / `revision` / `revision_note` dispatch inputs. Trigger:
  `workflow_dispatch` only (no cron). Since the release track ignores push
  events, a commit to this repo never publishes.
- `.github/workflows/release-tag.yml` (new): `on: push: tags: ['v*-labs.*']`,
  `permissions: actions: write, contents: read`. Checks out the tag, runs
  `labs.py check-release "$GITHUB_REF_NAME"`, then
  `gh workflow run ci.yml --ref "$GITHUB_REF_NAME" -f core_ref=... [-f revision=... -f revision_note=...]`.
  Check with the dvkit-infra skill whether the `.forgejo/` side needs a
  matching no-op, so a mirrored tag on Forgejo does nothing.
- `scripts/release-notes-hook.sh`: `labs.py release-notes "$EC_TAG"`. It reads
  `releases/<base-tag>.yaml` from the checkout, which is the tagged commit.
- Verify on a fork or with a throwaway tag before step 5: the tag push
  dispatches `ci.yml` at the tag; a missing or mismatched source tag stops
  the trigger with a clear error; the release tag equals the source tag; the
  body comes from the hook alone; a re-spin tag gives `.1` with the note.

## Step 5: first release, `labs/2026-01`

Prerequisites are met: `dvs-integ` is on mballance/verilator, and dv-solve
support is on fvutils/dv-solve `main` at `52262e3` (untagged). Then run steps 2 and
3 for real with `dvs` as the only topic: `setup`, `cut 2026-01`, `merge`,
`test`, `record`, `tag`. The user pushes the branch and the source tag
(`v5.055-labs.2026.01` at current master) to mballance/verilator, commits the
record here, runs `release-tag`, and pushes the commit and the release tag.
That push starts the release. Check the release tag, release body,
`--version` string and ivpm install.

## Step 6: rollout

- Onboard to the Forgejo/GitHub sync and the release-authority gate (the
  `dvkit-infra` skill).
- Switch one project's `ivpm.yaml` to `verilator-labs-bin`.
- Turn step 5 into a reusable wpk packet template for later releases.

## Out of scope

- Automatic topic rebasing onto `upstream/master`. Topics are rebased by hand
  as part of upstreaming.
- A labs-specific CI test suite. The full gate runs locally (design: Test gate).

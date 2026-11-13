# verilator-labs-bin: an experimental Verilator release

Status: design agreed (2026-11-13). Nothing is built yet; see Rollout and the
build-out plan in [docs/plan/labs-release-plan.md](../plan/labs-release-plan.md).

## Goal

Ship binary builds of Verilator master with our feature work merged in and
turned on: DVS solver integration, config-solver, covergroup runtime, efficient
waits, prototype debug support, and so on. Each feature keeps its own branch so
it stays upstreamable. The builds use the same platforms and pipeline as
verilator-bin.

Labs is to verilator-bin roughly what Fedora is to RHEL:

- verilator-bin: upstream Verilator, unmodified. The release track follows
  stable tags.
- verilator-labs-bin: always upstream master, plus features that are on their
  way upstream (and a few that may never get there). It is the place where
  features are proven before they are proposed.

The audience is small: mainly me, plus the few people who use my projects that
need labs features. Those projects pin the labs package in their `ivpm.yaml`.
The process is kept as light as that audience allows. It is a reliable way to
get binaries out, not a product with release management.

The main reason labs exists: pulling a binary release through ivpm is much
cheaper than keeping local Verilator builds in every project and on every
machine. It also makes sharing easy. Anyone can get the exact build my projects
use by adding one `ivpm.yaml` entry, without building Verilator, dv-solve and
the other dependencies themselves.

## Name

**`edapack/verilator-labs-bin`**. Users see it as "Verilator Labs".

- It follows the edapack `<tool>-bin` convention, and it sorts next to
  verilator-bin.
- "labs" says these are features under trial, not a different Verilator. It
  also covers features that will never go upstream.

`verilator --version` must look different from upstream, so that bug reports do
not reach the Verilator maintainers as upstream bugs:
`Verilator 5.055 devel rev v5.055-labs.2026.01`.

That needs no source change. Verilator's `src/config_rev` takes the revision
from `$VERILATOR_SRC_VERSION` (the hook upstream provides for distro
packagers), or else from `git describe`. The recipe sets
`VERILATOR_SRC_VERSION` to the release tag. A build of the tagged commit gets
the same result from `git describe`.

## Versions and tags

A release is named by its version tag:

    v<verilator-version>-labs.<YYYY>.<NN>      e.g. v5.055-labs.2026.01

- `<verilator-version>` is the number in `AC_INIT` on the release branch
  (`5.055 devel` gives `5.055`).
- `labs.<YYYY>.<NN>` comes from the release branch `labs/YYYY-NN`.
- The tag name is also the GitHub release tag of verilator-labs-bin and the
  ivpm version. A packaging re-spin adds a fourth part:
  `v5.055-labs.2026.01.1`.
- "labs" makes the version obviously ours, where a bare `v5.055-2026.01` would
  look like a variant of upstream's `v5.055`.

The same tag name goes on two repos, and each tag has its own job:

- **Source tag, on mballance/verilator.** Annotated, on the tested tip of
  `labs/YYYY-NN`; its message is the release record. It marks exactly what was
  tested, keeps that commit reachable if the branch is ever rewritten, and is
  the ref CI builds (so edapack-common versions the build from its name).
- **Release tag, on verilator-labs-bin.** On the commit that adds
  `releases/<tag>.yaml`. Pushing it is the release trigger. It also freezes
  the recipe (build scripts, `build-inputs.yaml`) the release is built with,
  so rebuilding a tag rebuilds the same thing.

Both are pushed only after the test gate passes: the source branch and tag
first, then the record commit and the release tag here. The `labs/YYYY-NN`
branch is the workspace and can be pushed at any time.

### How the release tag starts a release

edapack-common's `build-release.yml` never publishes on a `push` event: since
the Forgejo↔GitHub mirror, a sync cycle is a push, and a mirror operation must
not be a publishing one. So the tag push does not call `build-release.yml`
directly. A small workflow here, `on: push: tags: ['v*-labs.*']`:

1. Reads `releases/<base-tag>.yaml` from the tagged commit (`<base-tag>` is the
   tag without a re-spin suffix).
2. Checks that mballance/verilator has the source tag `<base-tag>` and that it
   points at the record's `head` (`git ls-remote`). If the source push has not
   landed, it fails here with a clear error.
3. Dispatches the release workflow (`gh workflow run ci.yml --ref <tag>
   -f core_ref=<base-tag>`, plus `revision=<n>` for a re-spin). The job's
   `GITHUB_TOKEN` may trigger `workflow_dispatch`, and a dispatch may publish.

The release-authority gate (only one forge publishes) and the "already
released?" check apply as usual. A tag that arrives on the other forge through
the mirror therefore cannot publish twice. This is the estate's usual rule for
publishing (a human pushes a tag, and the tag is the version); edapack only
departs from it because its other tags are computed by CI.

CI builds the source tag, not the branch tip, so a branch that moves after
release changes nothing. The record names the branch and SHA, so no ivpm
variable or other setting is needed to say what to build.

## Shape

```
 upstream/master ──────────┐
                           │  for each release: branch fresh, merge topics,
 topic branches ───────────┤  integrate (human or AI), test
   mballance/verilator     │
   dvs-integ, config-solver, efficient-waits, debug-proto, ...
                           v
 mballance/verilator    labs/2026-01, labs/2026-02, ...   one branch per release
                           │
                           │  tested tip tagged v5.055-labs.2026.02 (source tag, annotated)
                           v
 edapack/verilator-labs-bin   commit releases/v5.055-labs.2026.02.yaml, tag it
                              v5.055-labs.2026.02 (release tag), push
                           │
                           │  tag push -> check source tag == record head -> dispatch
                           v
                              thin caller of edapack-common build-release.yml@v1 (unchanged),
                              release track, core_ref = source tag
                              -> full release v5.055-labs.2026.02, same targets as verilator-bin
```

edapack-common does not have to change. `build-inputs.yaml` points `core.repo`
at mballance/verilator, with
`release_policy: 'latest-tag:^v\d+\.\d+-labs\.\d{4}\.\d{2}$'` (the release
track requires one; the dispatch's `core_ref` overrides it). The regex skips
upstream's own `v5.0xx` tags, which the fork also carries. On the release track
edapack-common versions a build as the core ref minus its `v`, so the published
release tag equals the source tag. Its "already released?" check is then
exact, and each labs tag is built once.

## Branches

### Topic branches

- One long-lived branch for each feature, on mballance/verilator, based on
  `upstream/master`. It is the branch the upstream PR comes from. The `*-fbN`
  PR-feedback branches are not topics.
- Branch names vary (`dvs-integ`, `config-solver`, `mballance/funccov`, ...).
  There is no naming convention: the manifest gives each topic's full branch
  name, and the tooling never derives a branch from a topic name.
- Each topic is enabled the way it would be upstream (for example
  `--solver dvs`, `--debug-src`). It adds no configure options or build
  switches unless they are meant to go upstream with the feature.
- Aim: zero impact unless enabled. This covers generated C++, runtime size and
  speed, and verilation time. A topic that cannot meet this yet may still ship
  in labs, because labs users accept the cost. But that is also a clear sign it
  is not ready to upstream. Record it in the manifest (`impact:` below).
- A topic that depends on another one is based on it (stacked).

### One integration branch per release: `labs/YYYY-NN`

Each release gets a new branch, cut fresh from `upstream/master`. The topics
are then merged into it in manifest order. `labs/2026-01` is the first labs
release of 2026. The branch is frozen once it is released. The release tag
(see Versions and tags) points at its tested tip. The tag message is the
release record, so the source repo carries what went into the release.

Why fresh each time instead of one branch that is merged forward:

- Every release has a short, readable history: upstream, then one merge for each
  topic, then any glue. You can see what labs carries by reading
  `git log --first-parent`.
- No merge chains build up across releases. A dropped topic just is not merged
  next time, with no revert needed.
- A human or an AI agent resolves conflicts and makes the union work. This is
  the step where the combination of features is actually integrated.

The cost: conflict resolutions have to be redone each release. Two things keep
this small:

- The previous `labs/YYYY-NN` branch is the reference. Its merge commits show
  how each conflict was resolved last time. Enabling `git rerere` locally
  replays most of them automatically.
- Glue commits (fixes needed only because two topics meet) are
  cherry-picked forward from the previous release branch. If a glue fix makes
  sense on its own in the later topic, move it there. Glue that remains has a
  `labs:` subject prefix.

### Integration steps for one release

1. `git checkout -b labs/2026-02 upstream/master`
2. Merge each topic in manifest order. Resolve conflicts with the previous
   release branch as the reference. Cherry-pick its `labs:` glue commits.
3. Build, and run the test gate (below). Fix what breaks: in the topic if the
   fix belongs there, otherwise as glue.
4. Write the release record (`releases/<tag>.yaml` in verilator-labs-bin) and
   create the annotated source tag on the tested tip, with the record as its
   message.
5. Push the branch and the source tag to mballance/verilator.
6. Commit the record in verilator-labs-bin, tag that commit with the same name,
   and push the commit and the tag. The tag push starts the release.

`scripts/labs.py` in verilator-labs-bin does the mechanical parts of each step,
and the skills in `skills/` (`labs-integrate`, `labs-topic`) guide the parts
that need judgement. This is a good fit for a work packet that an AI agent
runs. It starts with "cut labs/YYYY-NN" and ends when the tag is ready to push.

### Topic manifest (`topics.yaml` in verilator-labs-bin)

The manifest is the integrator's checklist and the record of what labs carries.

```yaml
topics:
  - name: dvs
    branch: dvs-integ                # full branch name on mballance/verilator
    upstream: planned                # pr-open <url> | planned | labs-only
    impact: none                     # none | <what it costs when not enabled>
    enabled_by: --solver-dvs         # the user-facing switch
    configure: [--with-dv-solve]     # configure flags the recipe passes
    requires_inputs: [dv-solve]      # must be in build-inputs dependencies
    tests: [t_dvs_*, t_constraint_solver_cmake]
```

Each release also gets a record, `releases/<tag>.yaml`, written by
`labs.py record`: the upstream base, each topic's branch, SHA and merge
commit, the glue commits and the test-gate results.

## Recipe repo (`edapack/verilator-labs-bin`)

The repo has three jobs: CI that publishes releases, the integration tooling
(`scripts/labs.py`, `skills/`), and the metadata (`topics.yaml`,
`releases/`).

- Copy of verilator-bin. It differs in `build-inputs.yaml` (core =
  mballance/verilator with the labs `release_policy`, plus dv-solve and
  CaDiCaL), in the configure flags the topics need, and in building and
  shipping dv-solve (below).
- It uses only the release track and publishes full releases, so ivpm serves
  the newest labs build. The version is the tag (see Versions and tags).
- Releases are cut on demand, about weekly or when a topic gains something
  worth shipping. A release tag push starts each one (see How the release tag
  starts a release). There is no cron, and CI never builds a moving branch.
  `workflow_dispatch` stays for rebuilding a tag by hand.
- Release body: the release record, appended by `scripts/release-notes-hook.sh`
  (topics with SHAs, glue, test-gate results). The record is always in the
  tagged commit, so the hook reads it from the checkout.
- Packaging re-spin: commit the recipe fix, then push the release tag
  `v5.055-labs.2026.01.1`. The trigger builds the same source tag with
  `revision=1`, and the re-spin tag's message is the `revision_note`.

### dv-solve is built and shipped

`--solver-dvs` uses dv-solve in two places: `verilator_bin` links dv-solve's
static writer library, and every model built with `--solver-dvs` links
dv-solve's static runtime library from `$DV_SOLVE_ROOT`. So the release tarball
carries a dv-solve install (headers, static libraries, relocatable `.pc`
files, licences), built from source on every target. The recipe builds it
before Verilator and configures Verilator with `--with-dv-solve=<that prefix>`.
`DV_SOLVE_ROOT` must resolve relative to the installed tarball, not to the
build machine (see the plan). A target where dv-solve does not build ships
without it, and the release notes say so.

## Test gate

Run by the integrator before pushing the release branch (not in CI):

- Full upstream regression suite on Linux x86_64. Any topic with runtime
  impact touches every model, so every upstream test must still pass.
- Each topic's own tests, from the manifest.
- Cross-feature tests, which turn on two topics at once (for example a
  DVS-randomized design built with `--debug-src`). They are the evidence that
  the union works. They go in the later topic, or as glue.

CI runs the verilator-bin smoke test on every target, as usual. It adds a
`--solver-dvs` test built from the installed tarball, and checks that
`--version` says labs.

## Using it

A project that needs labs features names the labs package instead of
verilator-bin:

```yaml
deps:
  - name: verilator
    url: https://github.com/edapack/verilator-labs-bin
    src: gh-rls
```

Both packages install the same `verilator` executable, so a project uses one or
the other, never both.

## Licensing

Verilator is LGPL-3.0 / Artistic-2.0. A modified binary has to come with its
source. The source tag on mballance/verilator, linked from each release,
covers this. The tarball also carries dv-solve's Apache-2.0 licence and the
MIT licences of its bundled SAT solvers.

## Rollout

1. Write `topics.yaml`. Cut `labs/2026-01` with dvs-integ as the only topic.
2. Create the CI recipe in verilator-labs-bin from verilator-bin. Point core at
   mballance/verilator, and build and ship dv-solve. Tag the tested tip
   `v5.055-labs.2026.01`, push it, then push the matching release tag here and
   let CI publish it.
3. Onboard it in the Forgejo/GitHub sync and the release-authority gate, the
   same way as the other edapack repos.
4. Switch one of my projects to depend on verilator-labs-bin.
5. Turn the integration steps into a repeatable work packet for an AI agent.

## Decisions

- Source home: mballance/verilator holds the topic branches and the release
  branches. Features are upstreamed from there. (2026-11-12)
- No labs-only configure options. Topics are enabled the way they would be
  upstream. (2026-11-12)
- Labs ships its features on. The goal is zero impact unless a feature is
  enabled. A topic that misses that goal may ship in labs, but is not ready to
  upstream. (2026-11-13)
- Labs always follows master. There is one track. (2026-11-13)
- One integration branch per release, `labs/YYYY-NN`, cut fresh from
  `upstream/master` and integrated by a human or an AI agent. Releases are cut
  on demand. (2026-11-13)
- Audience: me and a few users of my projects. Keep the process light.
  (2026-11-13)
- An annotated tag `v<verilator-version>-labs.<YYYY>.<NN>` on
  mballance/verilator marks the tested source and names the release. CI uses
  edapack-common's release track, so edapack-common does not change.
  (2026-11-13)
- A tag of the same name on verilator-labs-bin, on the commit that adds the
  release record, is the release trigger. Its push dispatches the release
  workflow with `core_ref` set to the source tag. No cron. (2026-11-13)
- `--version` shows labs through Verilator's `VERILATOR_SRC_VERSION` /
  `git describe` revision, with no source change and no `labs: version`
  commit. (2026-11-13)
- Topic branches are named in full in the manifest; there is no naming
  convention. (2026-11-13)
- The first release carries one topic, dv-solve integration (`dvs-integ`).
  verilator-labs-bin builds and ships dv-solve. (2026-11-13)
- Integration skills live in `skills/` in this repo. (2026-11-13)

## Alternatives considered

- **Scripted compose that regenerates the branch from a manifest** (rerere
  cache, conflicting topics dropped automatically). Rejected: a script only
  shows that the topics merge without conflicts, not that they work together.
  Integration needs judgement, so a human or an AI agent does it.
- **One long-lived integration branch, merged forward each week.** Rejected in
  favour of per-release branches. Merge chains build up, and a dropped topic has
  to be reverted.
- **Labs features off by default, so labs behaves like upstream.** Rejected:
  a labs build whose features are off has no purpose. The zero-impact-unless-enabled
  aim is the right form of that idea.
- **Labs-only configure options to switch features in or out.** Rejected: labs
  ships binaries, so the recipe chooses the configuration. Build switches go in
  only when they are meant to go upstream.
- **A separate `edapack/verilator` org fork for the source.** Rejected:
  features are upstreamed from mballance/verilator, and a second fork would be
  one more mirror to keep in sync.
- **The release branch as the release marker, built with
  `core_ref=labs/...` on the dev track.** Rejected: a pushed branch may still
  be in progress, and edapack-common would version it as
  `v2026-02.<run-id>`.
- **A cron that picks the newest labs tag on mballance/verilator**
  (`release_policy: latest-tag:...`), with no tag here. This was the first
  design. Replaced by the release-tag trigger: releases start when they are
  pushed rather than at the next cron, the record and the trigger arrive in one
  commit, and the recipe used for each release is frozen by its tag.
- **The release tag here only, with no source tag.** Rejected: edapack-common
  takes the version from the core ref's name, so a branch or SHA `core_ref`
  needs a new `version` input in a workflow about 50 repos share. The source
  tag also keeps the released commit reachable, and `labs.py` finds the
  previous release from it.
- **Letting a `v*` tag push publish in edapack-common.** Rejected: an
  estate-wide change to the mirror's sync-safety rule, for one repo. The
  dispatching workflow gets the same result locally.
- **A `labs: version` commit that edits `AC_INIT`.** Rejected: upstream
  changes that line at every version bump, so it would conflict in nearly
  every release.
- **A third track in verilator-bin instead of a new repo.** Rejected: ivpm
  serves the newest full release of a repo, so labs releases would compete with
  the upstream release track.
- **A slower "stable tag plus features" track.** Out of scope: labs always
  follows master.
- **Other names:** `-edge` and `-nightly` read as "newer upstream", which the
  verilator-bin dev track already is. `-exp` is unclear. `-x` is hard to find
  by search.


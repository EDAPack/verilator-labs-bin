---
name: labs-integrate
description: Build one Verilator Labs release. Cut labs/YYYY-NN from upstream/master, merge the topic branches in topics.yaml, resolve conflicts and add glue, run the test gate, write the release record, and tag. Use it when asked to cut, integrate or prepare a labs release in verilator-labs-bin. It stops before every push and commit.
---

# labs-integrate: integrate a Verilator Labs release

`scripts/labs.py` does the mechanical steps. This skill covers the parts
that need judgement: conflicts, fix vs glue, triaging the test gate, and the
summary. The design is `docs/design/labs-release.md`, and the record format
is in `releases/README.md`.

## Ground rules

- **Never push, and never commit in this repo.** Stop and hand the user the
  commands at each stop point (below). Local commits in the clone (merge
  resolutions, glue, topic-branch fixes) are part of the work. Pushing them
  is the user's job.
- Run every `labs.py` command from the root of this repo. The clone is
  `$IVPM_PACKAGES/verilator` (`packages/verilator`). Use `--verilator DIR`
  if it is somewhere else. Edit and resolve in the clone.
- Exit codes: 0 means done, 1 an error (read the message, fix the cause,
  re-run), and 2 a stop for you (a conflict). Every command can be run again
  safely and resumes where it stopped.
- Build and test output goes to `build/labs/<branch>/`. Read the summary
  first, then grep the logs. Don't dump whole logs.
- Don't edit `topics.yaml` as part of a release. Adding, retiring or
  reordering a topic is the `labs-topic` skill, and needs the user's say-so.

## Flow

The release name is `YYYY-NN`, the NNth labs release of the year.

1. `python3 scripts/labs.py setup`: adds the remote, turns on rerere, fetches.
2. `python3 scripts/labs.py status`: what changed in upstream and in each
   topic since the last release. Note any topic that no longer exists or is
   far behind `upstream/master`, and tell the user before going on.
3. `python3 scripts/labs.py cut YYYY-NN`: creates `labs/YYYY-NN`.
4. `python3 scripts/labs.py merge`: merges topics in manifest order. On exit
   2, resolve the conflict (below), `git add` the files, run
   `git commit --no-edit` in the clone, and run `merge` again.
5. `python3 scripts/labs.py glue`: cherry-picks the previous release's
   `labs:` commits. On a conflict, resolve it and run
   `git cherry-pick --continue`, then run `glue` again. Ask whether each glue
   commit still belongs (below).
6. `python3 scripts/labs.py test --quick`, then fix anything that fails. Then
   run `python3 scripts/labs.py test`. The full suite takes a long time, so
   run it with a long timeout and its output sent to a file.
7. `python3 scripts/labs.py record`: writes `releases/<tag>.yaml`.
8. `python3 scripts/labs.py tag`: creates the annotated source tag in the
   clone.
9. **Stop point 1.** Ask the user to commit the record here, on its own commit
   (`git add releases/<tag>.yaml && git commit -m "release <tag>"`).
10. `python3 scripts/labs.py release-tag`: checks the record, the tree and the
    source tag, creates the release tag here, and prints the push commands
    in order.
11. **Stop point 2.** Give the user the printed push commands and the summary
    (below). Pushing the labs branch and source tag to mballance/verilator
    comes first. Pushing the release tag here comes last, and it starts the
    release.

## Conflicts

`merge` prints the previous release's merge commit for the topic. Use it as
the reference:

- `git show --remerge-diff <previous-merge>` shows how the same conflict was
  resolved last time. If rerere has already replayed every hunk, `merge` says
  so and still stops: check the result with `git diff --cached`, don't just
  accept it.
- Keep both features' intent. The aim of labs is a union in which each topic
  still works when it is enabled, and the build is unchanged when it is not.
- If upstream moved the code the topic patches, port the topic's change to the
  new code. Don't restore the old code.
- When a hunk can't be resolved without changing what a topic does, stop and
  ask the user. Don't guess.
- Write down each conflict for the summary: the file, the cause, and how you
  resolved it.

## Fix in the topic, or glue?

A failure or conflict fix goes **on the topic branch** when it would be
needed with that topic alone on `upstream/master`, such as upstream drift or
a plain bug. Make it as a commit on the topic branch in the clone (check out
the branch, fix, commit, return to the labs branch). `merge` takes topics
from `origin/<branch>`, so the fix only reaches labs after the user pushes
it. That is a stop point: hand over the push command
(`git -C <clone> push origin <branch>`). Once it is pushed, run `setup` to
fetch, then rebuild the labs branch from scratch:
`git -C <clone> checkout master && git -C <clone> branch -D labs/YYYY-NN`,
then `cut` and `merge` again. rerere replays the resolutions already made.

It is **glue** (a commit on the labs branch whose subject starts with
`labs: `) only when it is needed because two topics meet. If the fix makes
sense on its own in the later topic, put it there instead. Make glue after
`merge` and `glue` have finished, as an ordinary commit on the labs branch.

For each glue commit from the previous release, `glue` drops it if it comes
out empty (the fix is now in a topic or upstream). On a conflict you decide:
resolve it, or `git cherry-pick --skip` if it no longer applies. A glue
commit that applies cleanly but looks obsolete stays in, because a re-run of
`glue` would pick it again. Put it in the summary for the user to decide.

## Test gate triage

`test` writes `build/labs/<branch>/gate.json` and prints pass/fail for each
part: topic tests, `cross_tests`, and the full upstream suite.

- **Upstream test fails:** first check whether it also fails on plain
  `upstream/master`. If it does, it is not a labs problem, so note it and
  waive it. If not, find the topic that causes it (any topic with runtime
  impact touches every model). Fix it in that topic.
- **Topic test fails:** this is usually upstream drift. Fix it in the topic.
- **Cross test fails, or two features break when combined:** fix it in the
  later topic or as glue. Write a new cross test when two topics touch the
  same code and no test turns both on.
- Use `record --waive "<why>"` only when the user agrees, and only for
  failures that are understood. The reason is kept in the record and the
  release notes.
- `--configure-arg=--with-dv-solve=/path` points the build at a local
  dv-solve when the default flag can't find one.

## The summary (hand this over at stop point 2)

- Tag, branch, upstream base (sha and date), and the previous release.
- Per topic: its sha, and the new commits since the last release (from `status`).
- Conflicts met and how each was resolved, including which ones rerere
  replayed.
- Glue: carried, added and dropped, with a reason for each.
- Topic-branch fixes made in the clone that the user must push.
- Test gate results, with any waivers.
- The push commands from `release-tag`, in order.

## Re-spin (packaging fix, same source)

When the recipe changes but the source does not, there is no new labs
branch. Commit the recipe fix here, then run
`python3 scripts/labs.py release-tag <tag> --respin N --note "<what changed>"`
and hand over the single push command it prints. The release is
`<tag>.N`.

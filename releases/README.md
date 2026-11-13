# Release records

One file per labs release, `<tag>.yaml` (e.g. `v5.055-labs.2026.01.yaml`),
written by `scripts/labs.py record`. Don't edit them by hand: a record is the
frozen history of what a release was built from. The same content is the
message of the annotated source tag on mballance/verilator.

Commit each record on its own, then tag that commit with the release's name
(`scripts/labs.py release-tag`). Pushing that tag starts the release; see
`docs/design/labs-release.md` (Versions and tags).

```yaml
tag: v5.055-labs.2026.01
branch: labs/2026-01
head: <sha>                     # tested tip; the tag points here
upstream: { ref: master, sha: <sha> }
previous: null                  # previous labs tag, or null for the first
topics:
  - { name: dvs, branch: dvs-integ, sha: <sha>, merge: <merge-commit-sha>,
      upstream: planned, enabled_by: --solver-dvs }   # frozen from topics.yaml
glue:                           # labs: commits, may be empty
  - { sha: <sha>, subject: "labs: ..." }
test_gate:
  upstream_suite: pass          # pass | none | fail:<n>
  topic_tests: pass
  cross_tests: pass
  notes: "..."                  # optional: the --waive reason
```

---
name: labs-topic
description: Add, retire, reorder or update a topic in verilator-labs-bin's topics.yaml (the list of mballance/verilator feature branches that Verilator Labs carries), then check it with `labs.py validate`. Use it when asked to add a feature to labs, drop one, or change a topic's fields.
---

# labs-topic: change what Verilator Labs carries

`topics.yaml` lists the topics in merge order. The field reference is the
comment at the top of the file, and the design is
`docs/design/labs-release.md` (Topic branches, Topic manifest). A change
here takes effect at the next release (`labs-integrate`). Past releases keep
their frozen copy in `releases/<tag>.yaml`. Never edit those files.

Don't commit. Show the user the diff and the `validate` result.

## Add a topic

1. Check that the branch exists on mballance/verilator:
   `git -C $IVPM_PACKAGES/verilator ls-remote --heads origin <branch>`.
   Use the full branch name. There is no naming convention, and `*-fbN`
   PR-feedback branches are not topics.
2. Fill in every field:
   - `name`: a short id, unique.
   - `upstream`: `planned`, `labs-only` or `pr-open <url>`.
   - `impact`: `none`, or what it costs when the feature is *not* enabled
     (generated code, runtime, verilation time). Ask the user if unsure.
     Anything other than `none` is a sign the topic is not ready to upstream.
   - `enabled_by`: the user-facing switch, as it would be upstream.
   - `configure`: configure flags the recipe must pass (often `[]`).
   - `requires_inputs`: build inputs it needs. Each must be a dependency in
     `build-inputs.yaml`. If one isn't there, it has to be added to the recipe
     too. Tell the user.
   - `depends_on`: topics it is stacked on. Each must come earlier in the list.
   - `tests`: its `test_regress` test names or globs.
3. Place it: after everything it depends on, and otherwise at the end (a new
   topic resolves conflicts against the older ones).
4. If it touches the same code as another topic, add a cross test to the
   top-level `cross_tests` list, or note that one is needed.

## Retire a topic

Use this when a topic is merged upstream or is being dropped. Remove its
entry. Check that no other topic lists it in `depends_on`. Note that glue
commits naming it will come out empty or conflict in the next `glue` run.
If it was merged upstream, the next release gets it through
`upstream/master`.

## Reorder

Order is merge order. Change it only for a reason, such as a new dependency
or to cut down repeated conflicts. Reordering changes which merge carries
each conflict, so the previous release's resolutions are a weaker reference
the next time.

## Check

```
python3 scripts/labs.py validate
```

It must print `validate: ok`. A warning that `build-inputs.yaml` was not
found means `requires_inputs` was not cross-checked. Report that to the user.

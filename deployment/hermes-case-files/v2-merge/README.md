# case-files overlay v2-merge — rebuild chain

Three-way merge build chain for the Hermes case-files overlay. No machine
paths are baked into the scripts: every input/output is a CLI argument (with
`CF_*`/`SUPPORTPORTAL_ROOT` env fallbacks), and the SupportPortal checkout
defaults to the checkout these scripts live in. Missing arguments fail with a
usage error instead of guessing.

Smoke tests: `backend/tests/test_hermes_case_files_v2_merge_scripts.py` runs
both merge scripts and both verifiers as subprocesses from a clean directory
with scrubbed env. A real-input rebuild test activates with
`CF_B1_SRC`/`CF_B1_PROFILES` (and optional `CF_B1_EXPECTED_TREE` for a
byte-identical diff).

## B1 overlay (td54 lineage, base image 180e2c98 = `sha256:75e0bdd0…`)

```bash
REPO=891612554546.dkr.ecr.us-east-1.amazonaws.com
BASE=$REPO/supportportal/hermes@sha256:75e0bdd07bca712dfcba3014ecb40bb37ebb13c180e2c98e0cfb1b88bd3d886c

# 1) Extract the live-image inputs (any dir you own):
podman pull --arch amd64 $BASE
CID=$(podman create --arch amd64 $BASE true)
mkdir -p b1src supportportal_agent_tools
podman cp $CID:/opt/hermes/tools/skills_tool.py                     b1src/
podman cp $CID:/opt/hermes/gateway/platforms/api_server_runs.py    b1src/   # lands flat: b1src/api_server_runs.py
podman cp $CID:/opt/hermes/supportportal_skill_profile.py           b1src/
podman cp $CID:/opt/hermes/plugins/supportportal_agent_tools/__init__.py  b1src/supportportal_agent_tools/
podman cp $CID:/opt/hermes/plugins/supportportal_agent_tools/plugin.yaml  b1src/supportportal_agent_tools/
podman cp $CID:/opt/hermes/investigation-profiles                   ./investigation-profiles
podman rm $CID

# 2) Merge (from a clean cwd; all paths explicit):
python3 deployment/hermes-case-files/v2-merge/merge_b1_fix.py \
  --src b1src --profiles investigation-profiles --out tree-b1

# 3) Verify the merged tree:
python3 deployment/hermes-case-files/v2-merge/verify_b1.py --tree tree-b1   # -> B1_VERIFY_OK

# 4) Build (copy Dockerfile.overlay-b1 next to the tree as the build context):
podman build --arch amd64 \
  -f deployment/hermes-case-files/v2-merge/Dockerfile.overlay-b1 \
  --build-arg HERMES_BASE_IMAGE=$BASE -t <tag> tree-b1
```

Reproducibility reference (2026-10-21): rebuilding from the preserved live
inputs produced a tree byte-identical to the td54 build tree, whose 12 overlay
files are byte-identical inside the deployed image
`sha256:7df5f3a325465ee07847ecd5c97874f0abf3e06fd1d3306c0fd5e7686f5e44b0`
(td54), and the local rebuild's image ID and rootfs layers equal the td54
image's.

## v2 three-way merge (td49 lineage: gateway 2cdc5a8a × plugin 6198ca25)

```bash
# Work dir layout (extract both base images the same way as above):
#   <work>/from-new-image/api_server_runs.py      (2cdc5a8a)
#   <work>/from-td46/skills_tool.py               (6198ca25)
#   <work>/from-td46/supportportal_agent_tools/{__init__.py,plugin.yaml}
#   <work>/supportportal_skill_profile.py         (6198ca25)
#   <work>/td46/investigation-profiles/
python3 deployment/hermes-case-files/v2-merge/merge_overlay_v2.py \
  --work <work> --out tree-v2          # --sp defaults to this checkout
python3 deployment/hermes-case-files/v2-merge/verify_merged_overlay.py --tree tree-v2  # -> VERIFY_OK
```

Note: the v2 chain copies `supportportal_file_context.py` from the repo bundle,
so its current output includes the R2-V2-B1 `file_skill_visible` helper — the
td49-era tree predates that fix and is expected to differ in exactly that file.

---
name: archer-cross-channel-host-in-enablement
description: >-
  Validate an Agora AppID and enable Archer cross-channel co-hosting (cross-channel
  host-in) with the fixed overseas configuration. Use this skill whenever the user
  explicitly asks to enable or open cross-channel co-hosting for an AppID. Do not
  use it for ordinary AppID lookups, diagnosis, or any request that does not clearly
  authorize the Archer write.
---

# Enable Archer Cross-Channel Co-Hosting

Use the bundled script as the single implementation of this workflow. It validates
the AppID, verifies that the project exists, applies the fixed UAP configuration,
and reads the result back before reporting success.

## Authorization Boundary

- Run this workflow only when the user explicitly asks to enable or open
  cross-channel co-hosting for a specific AppID. That request authorizes only this
  Archer UAP change.
- Do not invoke Archer proactively for diagnosis, general project lookup, or an
  unrelated request.
- Do not open Chrome automatically. If Pilot reports an expired Archer login,
  return the failure and ask the user to run `pilot archer login`; browser use still
  requires the user's explicit permission.
- Do not replace the bundled script with hand-written Archer calls. The script
  keeps validation, write scope, and readback verification deterministic.

## Fixed Configuration

The target is UAP `typeId=6` with exactly these values:

```text
status=1
region=2              # oversea / Priority Overseas
maxSubscribeLoad=50
```

When a configuration already exists, update only these three fields. When no
configuration exists, create it using the identifiers returned by Archer's
project check. If the existing values already match, treat the operation as an
idempotent success without sending a write.

## Workflow

1. Extract one AppID from the user's explicit enablement request. Do not guess or
   substitute an AppID.
2. Run:

   ```bash
   python3 <skill-directory>/scripts/enable_cross_channel_hosting.py '<appid>'
   ```

3. Return the script's stdout verbatim. Do not reinterpret a nonzero exit as
   success.

The script enforces this order:

1. Require exactly 32 hexadecimal characters. Invalid input returns exactly:

   ```text
   关键词必须为整数或 32 位字符串
   ```

2. Call Archer's project validation and exact project search endpoints. A valid
   32-character AppID that is absent from Archer returns exactly:

   ```text
   查无项目
   ```

3. Query the existing type-6 UAP record.
4. Create it if absent, update only the three fixed fields if mismatched, or skip
   the write if it already matches.
5. Query the UAP record again after every write. Report success only when the
   readback has `status=1`, `region=2`, and `maxSubscribeLoad=50`.

## Result Contract

A verified enablement starts with:

```text
开启结果：成功
```

Any execution, authentication, API, parsing, write, or readback problem starts
with:

```text
开启结果：失败
原因：<具体原因>
```

Never claim success solely because the Archer POST or PUT returned successfully.


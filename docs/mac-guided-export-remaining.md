> **Superseded implementation status:** See `guided-export-delivery.md` for the subsequent implementation and test evidence. The text below is historical review context, not the current list of code stubs. Real coordinator acceptance remains pending.

> **2026-09-09 follow-up:** The previous “real workflow wiring” wording does not
> mean the first-read executor is implemented. Material continuation previously
> simulated successful states; it now only validates/reuses a matching existing
> export, or blocks. See `review-followup-2026-09-09.md` for fixes and release blockers.

# Remaining work after snapshot export + real workflow wiring

Date: 2026-09-09.

## This machine (not a new-user first-read)

Idle snapshot `20260909T075630-0700` was decrypted (32 ok, 3 FTS derived DBs skipped with integrity failure). Three-scope export is a **new** run:

- `data/exports/20260909T0802-livedb`
- `source_kind=live-db`, `backup2_coverage=unverified`
- 510789 records; 599 conversations
- Target group: 1 match, 1578 records
- Target private: 1 match, 143064 records
- Old run `20260909T0438-livedb` was not overwritten
- `attachment_extraction_complete=false`; 1828 records `parse_status=partial`

Public `verified_builds` is still empty. `key_capture_allowed` is still false. Local `this_machine_evidence` is not a new-user beta.

## Product wiring done

- Real adapter has preflight, idle snapshot, passphrase HMAC, and a stub `execute_key_capture` that still refuses a global on-switch.
- Workflow: consent alone still blocks live capture (negative test kept). A this-job live grant + candidate environment + passing preflight can pass the gates **without** starting LLDB/WeChat.
- `continue_from_materials` continues a registered snapshot without launching WeChat.
- HTTP export returns `job_id`, polls progress, supports cancel, and can reveal a path only under the export/slices roots.

## Still missing for new-user acceptance

- A **new** first-read rehearsal (fresh snapshot + debug copy + 进入微信 + capture) driven entirely from the UI, on an account that does not already have a passphrase file.
- `export_verified` in the public registry for a stated build.
- Clean-install / second Mac / second account.
- Attachment completeness; backup 2 remains unverified.
- HTTP export of 500k rows from the viewer is now async, but the full three-scope canonical export is still a CLI/pipeline job, not a wizard button.

Do not treat this snapshot’s export or the continue-from-materials path as “a new user finished first read.”

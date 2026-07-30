# Phase Prompts — paste one at a time into Copilot Chat (Agent mode)

Prerequisite: `copilot-instructions.md` is saved at `.github/copilot-instructions.md` in the repo root. Copilot loads it automatically into every chat request, so each prompt below stays short and the full design travels with it.

Wait for `✅ Phase <n> complete`, review the diff, then send the next prompt.

---

## Phase 1 — Solution scaffold

```
Phase 1 only. Create the .NET 8 solution exactly as laid out in "Solution structure" in the project instructions: CreditAttachmentUploader.sln with CreditAttachment.Worker, CreditAttachment.Application, CreditAttachment.Infrastructure, CreditAttachment.Contracts, CreditAttachment.Tests, and the folders listed under each.

Deliver:
- Worker Service host in Program.cs with dependency injection configured.
- AttachmentUploadWorker.cs as a BackgroundService that starts, logs startup, honours the cancellation token, and does nothing else yet.
- appsettings.json reproducing the configuration block from the project instructions verbatim, including every "tbd".
- Strongly typed options classes bound to that configuration: FileShareOptions, WorkerOptions, ChunkingOptions, ApiOptions, AuthenticationOptions (with nested Vault and Kerberos options). Register them with the options pattern.
- Project references only: Worker -> Application, Infrastructure, Contracts. Infrastructure -> Application, Contracts. Application -> Contracts. Tests -> all.

Do not add any NuGet package beyond Microsoft.Extensions.Hosting. Do not implement any pipeline logic. Leave all other folders empty.

Done when: the solution builds and the worker starts and logs.
```

---

## Phase 2 — Contracts

```
Phase 2 only. In CreditAttachment.Contracts, create AttachmentRequest.cs, Registrant.cs, and CaseCommentAttachmentFileDto.cs exactly as written in the "C# data contracts" section of the project instructions. Property names, types, sealed modifiers, init accessors and default values must match character for character.

Add JSON serialisation configuration so the outgoing body matches the sample request body in the project instructions exactly: camelCase property names, "registrant" and "caseCommentAttachmentFileDto" at the root.

Do not add validation attributes, no records, no extra properties, no extra classes.

Done when: serialising a populated AttachmentRequest produces JSON structurally identical to the sample request body.
```

---

## Phase 3 — File share adapter

```
Phase 3 only. In CreditAttachment.Infrastructure/FileSystem, implement the File Share Adapter behind an interface defined in CreditAttachment.Application/Interfaces.

Implement:
- Discovery of files in InputFolder using FileSearchPattern (steps 1 of the processing flow).
- Readiness check: file not locked, size stable across FileReadinessDelaySeconds, write completed (step 2). Not-ready files are skipped until the next scan.
- Atomic move to ProcessingFolder so a second worker instance cannot pick up the same file (step 3).
- Move to ArchiveFolder on success and ErrorFolder on terminal failure, using the yyyy\MM subfolder convention shown in the File state management table (step 12).
- Write the error sidecar JSON next to the failed file, in exactly the format given in the project instructions.

Every operation carries a CorrelationId. Emit the "File discovered", "File archived" and "File moved to error" log events with their mandatory fields.

No API calls, no PDF parsing, no Base64 in this phase.

Done when: a file placed in incoming is discovered, moved to processing, and can be routed to archive or error with a sidecar file.
```

---

## Phase 4 — PDF validator

```
Phase 4 only. In CreditAttachment.Application/Validators, implement the PDF validator using exactly the ValidatePdfAsync logic in the "Core algorithms" section of the project instructions — same checks, same order, same exception types and messages: existence, .pdf extension case-insensitive, non-zero length, and %PDF- signature read from the first 5 bytes with FileShare.None.

Expose it behind an interface in CreditAttachment.Application/Interfaces and register it in DI. Emit the "File validation failed" log event with its mandatory fields.

Do not add a PDF parsing library. Do not add checks beyond the four specified.

Done when: valid PDFs pass and each of the four failure conditions throws its specified exception.
```

---

## Phase 5 — Base64 encoder and chunk splitter

```
Phase 5 only. In CreditAttachment.Application/Services, implement ConvertAndSplitAsync and SplitBase64 exactly as written in the "Core algorithms" section of the project instructions, including both guard clauses and the exact loop. Chunk size comes from ChunkingOptions. Ordering of parts must be preserved.

Expose behind an interface in CreditAttachment.Application/Interfaces and register in DI. Emit the "Base64 conversion started/completed" log event with CorrelationId, FileName, FileSize and ChunkCount. Never log the Base64 string or any chunk.

Do not stream, do not compress, do not alter the PDF bytes.

Done when: concatenating the returned parts in order reproduces the original Base64 string exactly.
```

---

## Phase 6 — Application metadata resolution

```
Phase 6 only. In CreditAttachment.Application/Services, implement resolution of applicationId and x-application-time-of-last-update (step 4 of the processing flow) behind an interface in CreditAttachment.Application/Interfaces.

The design recommends a staging/audit database or a sidecar metadata file and rejects reliance on human-maintained file or folder names alone. Implement the sidecar metadata file resolver: document.pdf paired with document.json containing applicationId and the timestamp. Structure the interface so a database-backed resolver can be added later without changing callers, but do not write that implementation now.

Missing or malformed metadata is a terminal failure: route to the error folder with errorCategory in the sidecar JSON.

The authoritative source of x-application-time-of-last-update is an unresolved open item. Implement the sidecar resolver and output a NEEDS DECISION line naming it.

Done when: a valid sidecar JSON yields both values, and a missing or malformed one produces a terminal failure.
```

---

## Phase 7 — Vault injection and Kerberos credential provider

```
Phase 7 only. In CreditAttachment.Infrastructure/Authentication, implement:
- Startup validation that every key in Authentication:Vault:RequiredKeys (KERBEROS_PRINCIPAL, KERBEROS_PASSWORD, KRB5_CONFIG, KRB5CCNAME) has been injected as an environment variable when Vault:Enabled is true. Fail fast on startup if any is missing.
- Startup validation of Kerberos configuration: service principal, realm, and ticket cache path present, and a ticket present at the configured cache location.
- A Kerberos credential provider that establishes the service identity from the injected configuration for use by integrated authentication.
- Operational readiness checks for Vault injection and Kerberos ticket availability.

The application MUST NOT acquire or renew Kerberos tickets itself — an init container, sidecar or platform mechanism does that. The application MUST NOT request OAuth2 credentials or handle JWTs.

Emit "Kerberos authentication failed" and "Vault secret injection failed" log events with their mandatory fields. Log only aliases and sanitised errors — never the principal password, key material, ticket contents, Vault token, or any injected value.

Done when: a missing required key or a missing ticket cache fails startup with a sanitised, actionable error.
```

---

## Phase 8 — Attachment API client

```
Phase 8 only. In CreditAttachment.Infrastructure/ApiClients, implement the typed attachment API client using UploadAttachmentAsync exactly as written in the "Core algorithms" section of the project instructions, plus the AttachmentApiException type it throws.

Register it with HttpClientFactory. Configure BaseUrl and TimeoutSeconds from ApiOptions. Configure the primary HttpClientHandler for integrated authentication / default credentials per AuthenticationOptions.UseDefaultCredentials. Never attach an Authorization header.

Add a Polly retry policy that implements the retry matrix in the project instructions exactly — 408, 429, 500, 502, 503, 504 and transient network failures retry; 403, 404 and 409 do not; 401 is controlled and retried only after ticket refresh or platform recovery. Honour Retry-After on 429, exponential backoff otherwise. Retries must be bounded.

Idempotency: generate one x-request-id GUID per logical upload attempt and surface it to the caller so it can be persisted. On retry after a process restart, the same request ID is reused for the same logical attempt.

Emit "API request started", "API retry" and "API failed" log events with their mandatory fields. Sanitise the response body before logging. Never log the request body.

BaseUrl is "tbd" and the success response contract is an unresolved open item. Do not invent either.

Done when: the request is built with the correct path, both headers, and the exact JSON body, and each row of the retry matrix behaves as specified.
```

---

## Phase 9 — Audit repository

```
Phase 9 only. In CreditAttachment.Infrastructure/Persistence, implement the audit repository behind an interface in CreditAttachment.Application/Interfaces, plus a migration script creating AttachmentUploadAudit exactly as written in the "Audit table" section of the project instructions — same columns, types, nullability and defaults.

The repository persists and updates: CorrelationId, ApplicationId, FileName, FileHash, RequestId, ApplicationTimeOfLastUpdate, ChunkCount, UploadStatus, ResponseCode, ErrorCategory, ErrorMessage, timestamps. UploadStatus reflects the final states named in the design: archived, failed, skipped, retry-pending.

Never persist raw Base64 content or chunks.

Do not run the migration against any live database. Do not add an ORM beyond what is needed; if you believe a package is required, ask first.

Done when: a processing attempt can be written and later updated with its outcome, keyed by CorrelationId.
```

---

## Phase 10 — Logging, tracing and health

```
Phase 10 only. Wire Serilog structured logging and OpenTelemetry tracing in CreditAttachment.Infrastructure/Telemetry and Program.cs.

Implement every row of the "Logging, audit and observability" event table with its exact event name, level, and mandatory fields as structured properties. Add health checks covering file share reachability, Vault injection, and Kerberos ticket availability. Add alerting hooks for repeated API and authentication failures.

Add a logging filter or sanitiser that guarantees Kerberos passwords, key material, ticket contents, Vault tokens, injected secrets, and raw Base64 content or chunks can never reach a sink.

Done when: every listed event emits with its mandatory fields, and a deliberate attempt to log a secret or a Base64 chunk is redacted.
```

---

## Phase 11 — Worker orchestration

```
Phase 11 only. Implement AttachmentUploadWorker as the full pipeline, wiring the components built in phases 3 to 10 in exactly the 12-step order of the processing flow table in the project instructions.

Requirements:
- Scan on a ScanIntervalSeconds loop.
- Enforce MaxDegreeOfParallelism; avoid loading many large PDFs concurrently.
- Honour the cancellation token cleanly at every await.
- One CorrelationId per file, flowing through every log line and the audit record.
- Every file ends in exactly one final state: archived, failed, skipped, or retry-pending.
- A failure at any step routes the file to the error folder with the sidecar JSON and an audit record, and does not stop the loop.

Do not change any component built in earlier phases. If wiring reveals a genuine gap, stop and ask.

Done when: a PDF dropped into incoming flows through all 12 steps to archive or error, with a matching audit row and a complete log trail.
```

---

## Phase 12 — Tests

```
Phase 12 only. In CreditAttachment.Tests, implement the test suites in the "Test strategy" table of the project instructions, using the existing UnitTests, IntegrationTests and ContractTests folders.

Unit: PDF validation, chunk splitting, metadata parsing, request payload creation, Kerberos configuration validation.
Integration: HTTP client behaviour against a mock API, retry policy per the retry matrix, Kerberos-authenticated request handling, 401 recovery, 409 handling, error folder movement.
Contract: JSON body and headers against the API contract, asserted against the sample request body.
Negative: invalid extension, empty file, locked file, malformed metadata, missing applicationId, stale last-update timestamp, missing Kerberos ticket cache, missing Vault-injected required key.
Security: assert no secrets, ticket details, Vault tokens, or Base64 payloads reach any log sink.

Mock the API and the file share. No test may call the real endpoint, real Vault, or a real KDC.

Done when: all tests pass and every row of the retry matrix and every negative case above has a test.
```

---

## Phase 13 — Deployment and runbook

```
Phase 13 only. Produce deployment configuration and an operational runbook.

Deployment: environment-specific configuration files carrying only non-secret values — endpoint URLs, folder paths, scan intervals, retry settings. Every secret is supplied by Vault runtime injection. Document the required environment variables by name only: KERBEROS_PRINCIPAL, KERBEROS_PASSWORD, KRB5_CONFIG, KRB5CCNAME. Never write a real value into any file.

Runbook: startup prerequisites and ordering (Vault injection, then Kerberos ticket, then worker start), health check endpoints, the meaning of each log event and audit UploadStatus, how to triage a file sitting in the error folder using its sidecar JSON, how to safely reprocess a file, and escalation paths for repeated 401, 403, and Vault or Kerberos failures.

The hosting model, ticket renewal mechanism, Vault namespace and secret path, and SPN mapping are all unresolved open items. Write the runbook with named placeholders and list every one as a NEEDS DECISION item.

Done when: the deployment config contains no secret values and the runbook covers startup, monitoring, triage, reprocessing and escalation.
```

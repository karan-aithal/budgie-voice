# Project Instructions — CreditAttachmentUploader

You are a senior .NET engineer implementing an approved Low Level Design. The design is FINAL. Your job is to translate it into code exactly as specified, one phase at a time.

## HARD RULES — apply to every request in this repository

1. **Build ONLY the phase you were asked for.** Do not start, scaffold, or "prepare for" later phases.
2. **Do not add anything not in this document.** No extra NuGet packages, no extra projects, no extra classes, no extra config keys, no extra endpoints, no helper abstractions, no caching, no queueing, no alternative auth. If you believe something is missing, **STOP and ask** — output `❓ NEEDS DECISION: <question>` and wait.
3. **Do not substitute technology.** Kerberos integrated authentication only. NEVER add OAuth2, JWT, bearer tokens, API keys, or `Authorization` headers.
4. **Do not invent values.** Every `tbd` in the configuration stays `tbd`. Every item in "Open Items" stays unresolved and is surfaced as a `// TODO (open item): ...` comment plus a `❓ NEEDS DECISION` line in your response.
5. **Do not refactor, rename, reformat, or "improve" code from earlier phases.** If a change to earlier code is genuinely required, STOP and ask first.
6. **Do not log** Kerberos passwords, key material, ticket contents, Vault tokens, injected secrets, or raw Base64 content or chunks — in any phase, in any form.
7. **After each phase, output:** `✅ Phase <n> complete` followed by the list of files created/modified and any `❓ NEEDS DECISION` items. Then stop and wait for approval.
8. **STOP and ask before:** deleting files, modifying files outside the stated phase scope, adding a NuGet package not named in this document, running database DDL against a real server, or changing the API contract.

## Target Platform

- .NET 8 Worker Service, runnable as a Windows Service, containerized workload, or scheduled job.
- Long-running hosted service with configurable scan interval and controlled parallelism.
- `HttpClientFactory` for HTTP. Polly for retry. Serilog for structured logging. OpenTelemetry for tracing.

## Objective

Scan a configured file share for PDF documents, convert each valid PDF to Base64, split the Base64 content into an ordered list of string parts, and call the Credit Application API V2 endpoint to add the PDF as a case comment attachment.

**Key principle:** the solution must be deterministic, auditable, secure, and operationally supportable. Every file processed must have a correlation ID, a request ID, an audit record, and a clear final state: archived, failed, skipped, or retry-pending.

### In scope
- File share scanning for PDF files
- PDF validation and Base64 conversion
- Chunking Base64 into `List<string>`
- HashiCorp Vault-backed Kerberos authentication and authorization
- API retry, logging, audit and error handling

### Out of scope (do NOT build)
- Manual UI-based upload
- PDF content extraction or OCR
- Credit decisioning or application assessment
- Design of the upstream identity provider
- Changing the Credit Application API contract

## Architecture (fixed)

```
                  File Share
              Incoming Folder
                     │
                     ▼
              File Scanner ──────────┐
                     │               ▼
                     │   Archive Folder / Error Folder
                     ▼
              PDF Validator
                     │
                     ▼
             Base64 Encoder
                     │
                     ▼
              Chunk Splitter
                     │
                     ▼
         Attachment API Client
                     │
                     ▼
      Kerberos Credential Provider
                     │
                     ▼
   HashiCorp Vault Secret Injection
                     │
                     ▼
 Kerberos Ticket Cache / Service Principal
                     │
                     ▼
        Credit Application API V2
```

### Component responsibilities

| Component | Responsibility |
|---|---|
| Worker Host | Schedules scanning, manages cancellation, coordinates the processing pipeline, enforces concurrency limits. |
| File Share Adapter | Discovers PDF files, checks file readiness, moves files to archive/error folders. |
| PDF Validator | Validates file extension, size, lock state, and PDF header before processing. |
| Base64 Encoder | Reads file bytes and converts content to Base64 without altering PDF binary content. |
| Chunk Splitter | Splits the Base64 string into configured chunk sizes and returns `List<string>`. |
| Kerberos Credential Provider | Uses Vault-injected Kerberos principal configuration and ticket cache location to establish the service identity used by integrated authentication. |
| HashiCorp Vault Integration | Retrieves and injects secrets and Kerberos parameters at runtime through approved platform integration, avoiding secrets in source code, images, or plain-text configuration. |
| API Client | Builds the HTTP request, attaches headers, sends the request, handles retryable responses. |
| Audit Repository | Persists file processing state, request ID, response details, and errors. |
| Observability | Produces structured logs, metrics, traces, and alerts. |

## Solution structure (fixed — do not add or rename projects/folders)

```
CreditAttachmentUploader.sln
├── CreditAttachment.Worker
│   ├── Program.cs
│   ├── AttachmentUploadWorker.cs
│   └── appsettings.json
├── CreditAttachment.Application
│   ├── Interfaces
│   ├── Services
│   ├── Models
│   └── Validators
├── CreditAttachment.Infrastructure
│   ├── ApiClients
│   ├── Authentication
│   ├── FileSystem
│   ├── Persistence
│   └── Telemetry
├── CreditAttachment.Contracts
│   ├── AttachmentRequest.cs
│   ├── Registrant.cs
│   └── CaseCommentAttachmentFileDto.cs
└── CreditAttachment.Tests
    ├── UnitTests
    ├── IntegrationTests
    └── ContractTests
```

## Configuration (fixed — reproduce exactly, keep every `tbd`)

```json
{
  "FileShare": {
    "InputFolder": "\\\\server\\creditdocs\\incoming",
    "ProcessingFolder": "\\\\server\\creditdocs\\processing",
    "ArchiveFolder": "\\\\server\\creditdocs\\archive",
    "ErrorFolder": "\\\\server\\creditdocs\\error",
    "FileSearchPattern": "*.pdf"
  },
  "Worker": {
    "ScanIntervalSeconds": 60,
    "MaxDegreeOfParallelism": 4,
    "FileReadinessDelaySeconds": 10
  },
  "Chunking": {
    "ChunkSize": 500000
  },
  "Api": {
    "BaseUrl": "tbd",
    "AttachmentEndpoint": "/v2/credit/applications/{applicationId}/add-case-comment-attachment",
    "TimeoutSeconds": 100
  },
  "Authentication": {
    "Mode": "Kerberos",
    "UseDefaultCredentials": true,
    "Vault": {
      "Enabled": true,
      "SecretPath": "tbd",
      "InjectedAs": "EnvironmentVariables",
      "RequiredKeys": [
        "KERBEROS_PRINCIPAL",
        "KERBEROS_PASSWORD",
        "KRB5_CONFIG",
        "KRB5CCNAME"
      ]
    },
    "Kerberos": {
      "ServicePrincipal": "tbd",
      "TicketCachePath": "tbd",
      "Realm": "tbd",
      "RenewalMode": "SidecarOrPlatformManaged"
    }
  }
}
```

**Security note:** Kerberos credentials, service principal passwords, key material, and Vault access parameters MUST NOT be stored in plain-text `appsettings.json`, source code, container images, or deployment manifests. Only non-secret configuration — endpoint URLs, folder paths, scan intervals, retry settings — belongs in application configuration.

## API contract (fixed — do not change)

`POST /v2/credit/applications/{applicationId}/add-case-comment-attachment`

| Parameter | Type | Location | Description |
|---|---|---|---|
| `x-request-id` | String | Header | Idempotency key. Generate one unique GUID per file upload attempt and persist it in audit storage. |
| `x-application-time-of-last-update` | String | Header | Timestamp of the last accepted update of the application; ensures the request works on the correct current version/instance. |
| `applicationId` | String | Path | Unique identifier of the credit application. |
| `registrant` | Object | Body | The user or process initiating the request. |
| `caseCommentAttachmentFileDto` | Object | Body | Attached file related information containing the case comment. |
| `caseCommentAttachmentFileDto.caseCommentAttachmentParts` | List&lt;String&gt; | Body | List of Base64 parts. When concatenated in the same order, the parts represent the complete Base64 document. |
| `caseCommentAttachmentFileDto.fileName` | String | Body | The original file name without extension, unless the API requires the full name. |
| `caseCommentAttachmentFileDto.fileExtension` | String | Body | The file extension, expected to be `pdf`. |

### Sample request body

```json
{
  "registrant": {
    "id": "SYSTEM",
    "name": "CreditAttachmentUploader",
    "type": "ServiceAccount"
  },
  "caseCommentAttachmentFileDto": {
    "caseCommentAttachmentParts": [
      "JVBERi0xLjQK...",
      "...nextBase64Chunk..."
    ],
    "fileName": "BuyerDocument",
    "fileExtension": "pdf"
  }
}
```

## Processing flow (fixed order)

| Step | Name | Description | Output |
|---|---|---|---|
| 1 | Discover file | Find PDF files in the input folder using the configured search pattern. | New PDF candidate list |
| 2 | Check readiness | Ensure the file is not locked, size is stable, and the write operation has completed. | Ready for processing, or skip until next scan |
| 3 | Move to processing folder | Atomically move the file to prevent duplicate processing by another worker instance. | Processing file path |
| 4 | Resolve metadata | Determine `applicationId` and `x-application-time-of-last-update`. May come from file naming convention, sidecar metadata file, database, or upstream service. | Application ID and last-update timestamp |
| 5 | Validate PDF | Validate extension, size, non-empty content, and `%PDF-` file signature. | Validation result |
| 6 | Read bytes | Read file bytes using async file IO. | `byte[]` PDF content |
| 7 | Convert Base64 | Convert bytes using `Convert.ToBase64String`. | Base64 string |
| 8 | Split chunks | Split Base64 into an ordered `List<string>` using the configured chunk size. | `caseCommentAttachmentParts` |
| 9 | Establish Kerberos identity | Ensure Vault-injected Kerberos configuration is present and a valid ticket exists in the configured credential cache. | Authenticated service identity |
| 10 | Call API | POST to the add-case-comment-attachment endpoint with headers and JSON body. | HTTP response |
| 11 | Persist audit | Write success/failure details including correlation ID, request ID, response status and error. | Audit record |
| 12 | Finalize file state | Move file to archive on success, or error folder on terminal failure. | Clear file state |

## Application metadata resolution

Options considered:

| Option | Description | Note |
|---|---|---|
| File naming convention | e.g. `APP12345_20260720T121500Z_document.pdf` | Simple, but fragile if users rename files manually. |
| Sidecar metadata file | e.g. `document.pdf` plus `document.json` containing `applicationId` and timestamp. | Good for batch/integration scenarios. |
| Database lookup | A staging table maps file name or external reference to application metadata. | Best when files are produced by upstream systems. |
| Pre-upload API lookup | Call an application API to retrieve the current last-update timestamp. | Best for correctness if allowed by platform architecture. |

**Recommended approach:** use a staging/audit database or a sidecar metadata file. Do not rely only on human-maintained folder names or file names for critical application metadata.

## HashiCorp Vault and Kerberos authentication

The service authenticates using Kerberos integrated authentication. HashiCorp Vault is the approved central secret store for Kerberos-related secret material and runtime parameters. The application MUST NOT request OAuth2 client credentials or manage JWT access tokens directly.

At runtime, Vault integration injects the required Kerberos parameters and secret values into the workload environment. A Kerberos init container, sidecar, platform component, or equivalent approved mechanism obtains a Kerberos ticket for the configured service principal and writes it to the configured ticket cache. The .NET service then uses integrated authentication / default credentials when calling downstream services that accept Kerberos.

### Authentication sequence
1. Workload starts in the target runtime environment.
2. Vault integration authenticates to HashiCorp Vault using the approved platform identity.
3. Vault retrieves the required Kerberos principal configuration and secret material.
4. Kerberos initialization obtains a service ticket and writes it to the configured credential cache.
5. The worker service starts after the Kerberos ticket is available.
6. The API client sends the request using integrated Kerberos authentication.
7. Background renewal or a platform-managed sidecar keeps the ticket valid for the running service.

### Rules

| Area | Rule |
|---|---|
| Secret store | HashiCorp Vault is the authoritative source for Kerberos-related secret material and runtime parameters. |
| Runtime identity | The worker runs under an approved service identity, not a personal user account. |
| Credential injection | Secrets are injected at runtime by approved platform integration; never committed to source or stored in plain-text configuration. |
| Kerberos ticket handling | An init container, sidecar, or platform-managed mechanism obtains and renews the service ticket. The application does not acquire tickets itself. |
| .NET authentication mode | The API client uses integrated authentication / default credentials rather than manually attaching a bearer token. |
| Ticket cache | The ticket cache location is provided through runtime configuration such as `KRB5CCNAME` and must be available to the worker process. |
| 401 handling | Treat as authentication failure. Validate ticket availability, cache path, service principal, clock skew, and downstream SPN configuration before retrying. |
| 403 handling | Do not retry continuously. Log the authorization failure and raise an operational alert. |
| Secret logging | Never log Kerberos passwords, key material, ticket contents, Vault tokens, or injected secret values. |

## C# data contracts (fixed — reproduce exactly)

```csharp
public sealed class AttachmentRequest
{
    public CaseCommentAttachmentFileDto CaseCommentAttachmentFileDto { get; init; } = default!;
    public Registrant Registrant { get; init; } = default!;
}

public sealed class Registrant
{
    public string Id { get; init; } = default!;
    public string Name { get; init; } = default!;
    public string Type { get; init; } = "ServiceAccount";
}

public sealed class CaseCommentAttachmentFileDto
{
    public List<string> CaseCommentAttachmentParts { get; init; } = new();
    public string FileName { get; init; } = default!;
    public string FileExtension { get; init; } = "pdf";
}
```

## Core algorithms (fixed — implement exactly this logic)

### PDF validation

```csharp
public async Task ValidatePdfAsync(string filePath, CancellationToken ct)
{
    if (!File.Exists(filePath))
        throw new FileNotFoundException("PDF file not found", filePath);

    if (!string.Equals(Path.GetExtension(filePath), ".pdf", StringComparison.OrdinalIgnoreCase))
        throw new InvalidOperationException("Only PDF files are supported.");

    var fileInfo = new FileInfo(filePath);
    if (fileInfo.Length == 0)
        throw new InvalidOperationException("PDF file is empty.");

    await using var stream = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.None);
    var header = new byte[5];
    var read = await stream.ReadAsync(header.AsMemory(0, 5), ct);
    var signature = Encoding.ASCII.GetString(header, 0, read);

    if (!signature.StartsWith("%PDF-"))
        throw new InvalidOperationException("Invalid PDF signature.");
}
```

### Base64 conversion and splitting

```csharp
public async Task<List<string>> ConvertAndSplitAsync(string filePath, int chunkSize, CancellationToken ct)
{
    byte[] fileBytes = await File.ReadAllBytesAsync(filePath, ct);
    string base64 = Convert.ToBase64String(fileBytes);
    return SplitBase64(base64, chunkSize);
}

public static List<string> SplitBase64(string content, int chunkSize)
{
    if (string.IsNullOrWhiteSpace(content))
        throw new ArgumentException("Base64 content cannot be empty.", nameof(content));

    if (chunkSize <= 0)
        throw new ArgumentOutOfRangeException(nameof(chunkSize));

    var parts = new List<string>();

    for (int i = 0; i < content.Length; i += chunkSize)
    {
        parts.Add(content.Substring(i, Math.Min(chunkSize, content.Length - i)));
    }

    return parts;
}
```

### API client

```csharp
public async Task UploadAttachmentAsync(
    string applicationId,
    string applicationTimeOfLastUpdate,
    AttachmentRequest request,
    CancellationToken ct)
{
    string requestId = Guid.NewGuid().ToString();

    using var httpRequest = new HttpRequestMessage(
        HttpMethod.Post,
        $"/v2/credit/applications/{applicationId}/add-case-comment-attachment");

    httpRequest.Headers.Add("x-request-id", requestId);
    httpRequest.Headers.Add("x-application-time-of-last-update", applicationTimeOfLastUpdate);

    // Kerberos authentication is handled by HttpClientHandler using integrated authentication / default credentials.
    // Do not attach bearer tokens or log Kerberos ticket details.

    httpRequest.Content = JsonContent.Create(request);

    using var response = await _httpClient.SendAsync(httpRequest, ct);

    if (!response.IsSuccessStatusCode)
    {
        string responseBody = await response.Content.ReadAsStringAsync(ct);
        throw new AttachmentApiException(response.StatusCode, responseBody, requestId);
    }
}
```

**Implementation note:** configure the underlying HTTP handler for integrated authentication where supported by the hosting platform. The service relies on the Kerberos ticket cache / service identity prepared by the Vault and Kerberos runtime components.

## Retry, resilience and idempotency (fixed)

| Condition | Retry? | Handling |
|---|---|---|
| 408 Request Timeout | Yes | Retry with exponential backoff. |
| 429 Too Many Requests | Yes | Respect `Retry-After` header if present; otherwise exponential backoff. |
| 500, 502, 503, 504 | Yes | Retry transient server or gateway failures. |
| 401 Unauthorized | Controlled | Validate Kerberos ticket availability and renewal status. Retry only after ticket refresh or platform recovery. |
| 403 Forbidden | No | Log as authorization failure and alert the support/security team. |
| 404 Application Not Found | No | Move file to error folder; metadata is likely wrong. |
| 409 Conflict | No / Controlled | Log conflict. Do not blindly retry; application version may be stale. |
| Invalid PDF | No | Move to error folder with error sidecar JSON. |
| Network failure | Yes | Retry if transient and within retry budget. |

**Idempotency:** persist `x-request-id` with the file audit record. If the same file must be retried after a process restart, reuse the same request ID for that logical upload attempt unless the API contract explicitly requires a new idempotency key per attempt.

## Logging, audit and observability

Sensitive values — Kerberos passwords, key material, ticket contents, Vault tokens, injected secrets, raw Base64 content — MUST NEVER be logged.

| Event | Level | Mandatory fields |
|---|---|---|
| File discovered | Information | CorrelationId, FileName, FileSize, InputFolder |
| File validation failed | Warning / Error | CorrelationId, FileName, ValidationError |
| Base64 conversion started/completed | Information | CorrelationId, FileName, FileSize, ChunkCount |
| Kerberos authentication failed | Error | CorrelationId, ErrorCode, ServicePrincipalAlias, TicketCacheConfigured, SanitizedError |
| Vault secret injection failed | Error | CorrelationId, VaultSecretPathAlias, MissingKeyAlias, SanitizedError |
| API request started | Information | CorrelationId, ApplicationId, RequestId, Endpoint |
| API retry | Warning | CorrelationId, RequestId, AttemptNumber, StatusCode, Delay |
| API failed | Error | CorrelationId, ApplicationId, RequestId, StatusCode, ResponseBodySanitized |
| File archived | Information | CorrelationId, FileName, ArchivePath |
| File moved to error | Error | CorrelationId, FileName, ErrorPath, ErrorCategory |

### Audit table (fixed)

```sql
CREATE TABLE AttachmentUploadAudit
(
    Id BIGINT IDENTITY PRIMARY KEY,
    CorrelationId VARCHAR(100) NOT NULL,
    ApplicationId VARCHAR(100) NULL,
    FileName VARCHAR(500) NOT NULL,
    FileHash VARCHAR(128) NULL,
    RequestId VARCHAR(100) NULL,
    ApplicationTimeOfLastUpdate VARCHAR(100) NULL,
    ChunkCount INT NULL,
    UploadStatus VARCHAR(50) NOT NULL,
    ResponseCode INT NULL,
    ErrorCategory VARCHAR(100) NULL,
    ErrorMessage VARCHAR(MAX) NULL,
    CreatedUtc DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    UpdatedUtc DATETIME2 NULL
);
```

## File state management

| Folder | Purpose | Example |
|---|---|---|
| incoming | New files waiting to be picked up. | `\\server\creditdocs\incoming` |
| processing | Temporary exclusive ownership by the worker. | `\\server\creditdocs\processing` |
| archive | Successfully uploaded files. | `\\server\creditdocs\archive\2026\07` |
| error | Terminally failed files and error sidecar JSON. | `\\server\creditdocs\error\2026\07` |

### Error sidecar JSON format

```json
{
  "correlationId": "ATTACH-20260720-000001",
  "applicationId": "APP12345",
  "fileName": "BuyerDocument.pdf",
  "status": "Failed",
  "errorCategory": "ApplicationNotFound",
  "message": "Credit Application API returned 404.",
  "timestampUtc": "2026-07-20T13:45:00Z"
}
```

## Security requirements

| Area | Requirement |
|---|---|
| Transport security | Use HTTPS/TLS 1.2 or higher for all API and token calls. |
| Secrets | Store Kerberos credentials, key material, and Vault access parameters in HashiCorp Vault or an approved enterprise secret store. Do not log or commit secrets. |
| Kerberos | Use an approved service principal identity, managed ticket cache, and platform-supported renewal. Do not use personal credentials. |
| Authorization | Grant least-privilege access to the target API and file share. |
| File share access | Run the service account with least privilege: read/write required folders only. |
| Base64 content | Treat Base64 as sensitive document content. Do not log payload or chunks. |
| Audit | Keep audit records according to retention policy and operational requirements. |

## Non-functional requirements

| Category | Requirement |
|---|---|
| Performance | Process configurable file volumes with `MaxDegreeOfParallelism`. Avoid loading too many large PDFs concurrently. |
| Scalability | A single worker instance is simplest. For multiple instances, use atomic move to the processing folder and database idempotency. |
| Reliability | Retries must be bounded. Terminal failures must be visible in the error folder and the audit table. |
| Maintainability | Use interfaces, dependency injection, unit tests, and clear separation between file I/O, API calls, token acquisition, and orchestration. |
| Operability | Expose logs, audit table, health checks, and alerts for repeated API/authentication failures. |
| Compliance | Keep payload handling auditable while avoiding storage of raw Base64 unless explicitly approved. |

## Test strategy

| Test type | Coverage |
|---|---|
| Unit | PDF validation, chunk splitting, metadata parsing, request payload creation, Kerberos configuration validation. |
| Integration | HTTP client behaviour against a mock API, retry policy, Kerberos-authenticated request handling, 401 recovery behaviour, 409 handling, error folder movement. |
| Contract | Validate JSON body and headers against the API contract. |
| Negative | Invalid extension, empty file, locked file, malformed metadata, missing `applicationId`, stale last-update timestamp, missing Kerberos ticket cache, missing Vault-injected required key. |
| Operational | Restart during processing, duplicate file detection, large PDF handling, network outage, Vault injection failure, Kerberos ticket expiry and renewal. |
| Security | Verify no secrets, Kerberos ticket details, Vault tokens, or Base64 payloads are logged. |

## Open items — UNRESOLVED. Do not decide these yourself.

| Topic | Required decision |
|---|---|
| Maximum API payload size | Confirm the API limit and set `ChunkSize` and file size validation accordingly. |
| Source of `x-application-time-of-last-update` | Confirm whether to read from sidecar metadata, database, or upstream application lookup. |
| Registrant object schema | Confirm required fields in `Registrant` and adjust the contract class. |
| API success response contract | Log response status and body if safe; update the response model once the contract is known. |
| Duplicate file behaviour | Use file hash and audit table to detect duplicate processing if required. |
| Vault namespace and secret path | Confirm with the platform/security team and update deployment configuration. |
| Kerberos service principal and SPN mapping | Confirm service account, SPN, realm, and downstream API Kerberos configuration. |
| Ticket renewal mechanism | Confirm whether renewal is handled by a sidecar, init container, host service, or platform automation. |
| Hosting model | Confirm whether this runs as a Windows Service, OpenShift workload, or another runtime, and configure integrated authentication accordingly. |

## Phase plan

Build strictly in this order. One phase per request. Do not begin the next phase until told to.

| Phase | Scope |
|---|---|
| 1 | Solution scaffold, projects, DI, options classes, `appsettings.json` |
| 2 | Contracts project — `AttachmentRequest`, `Registrant`, `CaseCommentAttachmentFileDto` |
| 3 | File share adapter — discovery, readiness, atomic move, archive/error movement, sidecar JSON |
| 4 | PDF validator |
| 5 | Base64 encoder and chunk splitter |
| 6 | Application metadata resolution |
| 7 | Vault injection validation and Kerberos credential provider |
| 8 | Attachment API client, `HttpClientFactory`, integrated auth, Polly retry policy, idempotency |
| 9 | Audit repository and audit table migration |
| 10 | Serilog structured logging, OpenTelemetry tracing, health checks |
| 11 | Worker host orchestration — scan loop, parallelism, cancellation, pipeline wiring |
| 12 | Tests — unit, integration, contract, negative |
| 13 | Deployment configuration and runbook |

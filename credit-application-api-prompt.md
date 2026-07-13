# .NET Development Prompt — Credit Application Retrieval API

---

## CAVEMAN MODE: ON

**NO EXTRA STUFF. BUILD EXACTLY WHAT IS WRITTEN. NO MORE. NO LESS. IF DOCUMENT NOT SAY IT, YOU NOT ADD IT. FOLLOW DOCUMENT. BUILD DOCUMENT.**

---

## What You Are Building

A secure REST API that retrieves the **latest Credit Application record** from the `C3ApplicationLog` table for a given `CaseId` and `BuyerId`.

Built in **.NET 8 / ASP.NET Core Web API**.

---

## The Stack — Use This. Nothing Else.

* ASP.NET Core Web API (.NET 8)
* Entity Framework Core
* SQL Server
* Serilog
* FluentValidation
* Polly (Retry & Circuit Breaker)
* Application Insights / OpenTelemetry

---

## Solution Structure — Build Exactly This

```
Factoring.Onboarding.sln

├── Factoring.Bff.Api
│   ├── Controllers
│   ├── Services
│   ├── Clients
│   │   └── CreditApplicationStatusApiClient.cs
│   ├── Models
│   ├── Middleware
│   │   ├── CorrelationMiddleware.cs
│   │   ├── ExceptionHandlingMiddleware.cs
│   │   └── AuthenticationMiddleware.cs
│   ├── Validators
│   └── Program.cs
│
├── Factoring.CreditApplicationStatus.Api
│   ├── Controllers
│   │   └── CreditApplicationStatusController.cs
│   ├── Services
│   │   └── CreditApplicationStatusService.cs
│   ├── Repositories
│   │   ├── ICreditApplicationStatusRepository.cs
│   │   └── CreditApplicationStatusRepository.cs
│   ├── Models
│   ├── Utilities
│   │   └── JsonColumnParser.cs
│   ├── Middleware
│   ├── Validators
│   └── Program.cs
│
├── Factoring.CreditApplicationStatus.Infrastructure
│   ├── DbContext
│   ├── Configurations
│   ├── Migrations
│   └── DataAccess
│
├── Factoring.CreditApplicationStatus.Contracts
│   ├── Requests
│   ├── Responses
│   └── Constants
│
└── Factoring.CreditApplicationStatus.Tests
    ├── UnitTests
    ├── IntegrationTests
    └── TestHelpers
```

---

## Key Classes — Build These. No Renames. No Extras.

### BFF Layer
* `CreditApplicationStatusBffController`
* `CreditApplicationStatusProxyService`
* `CreditApplicationStatusApiClient`
* `CorrelationMiddleware`
* `ExceptionHandlingMiddleware`

### Credit Application API Layer
* `CreditApplicationStatusController`
* `CreditApplicationStatusService`
* `ICreditApplicationStatusRepository`
* `CreditApplicationStatusRepository`
* `JsonColumnParser`

---

## API Endpoints

### Credit Application API
```http
GET /api/credit-applications/{caseId}/buyers/{buyerId}
```

### BFF Endpoint
```http
GET /bff/cases/{caseId}/buyers/{buyerId}/credit-application
```

Frontend talks **only** to the BFF. Never directly to the Credit Application API.

---

## HTTP Response Codes

| Status | Meaning |
|---|---|
| 200 | Record found |
| 404 | No matching record |
| 400 | Invalid input |
| 401 | Auth required |
| 403 | Not authorized |
| 500 | Server error |

---

## Request Flow — Implement In This Order. No Shortcuts.

1. User opens Buyer screen
2. Frontend calls BFF endpoint
3. BFF validates authentication and authorization
4. BFF calls Credit Application API with `CaseId` and `BuyerId`
5. Credit Application API retrieves latest record
6. JSON columns are converted into structured objects
7. API returns result to BFF
8. BFF optionally transforms the response
9. BFF returns final payload to frontend

---

## BFF Responsibilities — All Of These. Nothing Extra.

* Validate authentication tokens
* Validate user authorization
* Generate and propagate Correlation IDs
* Invoke the Credit Application API
* Retry transient failures
* Transform or enrich responses for UI
* Mask sensitive fields where required
* Return user-friendly error messages
* Capture telemetry and performance metrics

---

## Credit Application API Responsibilities — All Of These. Nothing Extra.

* Retrieve the latest `C3ApplicationLog` record
* Parse JSON columns
* Execute business validation
* Interact with the database
* Produce audit logs
* Return structured API responses

---

## Response Model — Use Dynamic Dictionary. No Fixed DTOs.

```json
{
  "caseId": "12345",
  "buyerId": "98765",
  "lastUpdated": "2026-07-09T08:00:00Z",
  "data": {
    "creditApplicationId": "CA123",
    "status": "Approved",
    "requestedAmount": 1000000,
    "responsePayload": {
      "decision": "Approved"
    },
    "newFutureColumn": "value"
  }
}
```

Read DB row into `Dictionary<string, object>`. New DB columns auto-appear. No code changes needed.

---

## JSON Column Handling — `JsonColumnParser.cs`

Columns that may contain JSON:
* `RequestBody`
* `ResponseBody`
* `Metadata`
* `AuditDetails`

**Logic:**
1. Detect if value is a JSON string
2. Attempt deserialization
3. Valid JSON → return as structured object
4. Invalid JSON → return original string as-is

**DB value:**
```json
"{\"decision\":\"Approved\",\"score\":87}"
```
**API returns:**
```json
{ "decision": "Approved", "score": 87 }
```

---

## Database Table — `C3ApplicationLog`

| Column | Data Type | Nullable |
|---|---|---|
| C3ApplicationLogId (PK) | bigint | No |
| BuyerId (FK) | bigint | No |
| CaseId | bigint | No |
| ApplicationId | bigint | Yes |
| RequestId | nvarchar(100) | No |
| RequestBody | nvarchar(max) | Yes |
| ResponseBody | nvarchar(max) | Yes |
| StatusCode | int | Yes |
| StatusDescription | nvarchar(500) | Yes |
| Success | bit | No |
| CreatedDate | datetime2(3) | No |
| UpdatedDate | datetime2(3) | Yes |
| Operation | varchar(100) | Yes |
| CallBackStatus | varchar(100) | Yes |

### Required Index
```sql
IX_C3ApplicationLog_CaseId_BuyerId_UpdatedDate
ON C3ApplicationLog (CaseId, BuyerId, UpdatedDate DESC)
```

---

## Security — Follow This Exactly

### SQL Injection — NEVER DO THIS
```csharp
// ❌ FORBIDDEN
string sql = $"SELECT * FROM C3ApplicationLog WHERE BuyerId = {buyerId}";
```

### ALWAYS DO THIS
```sql
WHERE BuyerId = @BuyerId AND CaseId = @CaseId
```

Use **Entity Framework Core** or **parameterized SQL**. Nothing else.

### Input Validation (FluentValidation)
* `CaseId` provided
* `BuyerId` provided
* Valid numeric values
* Length limits enforced
* Allowed characters only

### Authentication
Use the platform's existing authentication mechanism.

### Authorization
Validate caller has access to the requested `CaseId`.

### Output Encoding
Use ASP.NET Core built-in JSON serialization.

---

## Correlation & Traceability

Generate or propagate header:
```
X-Correlation-Id
```

Include `CorrelationId` in:
* Request logs
* Database logs
* Error logs
* Response headers

---

## Logging Strategy — Serilog

### Information Logs

**API Started:**
* CorrelationId
* CaseId
* BuyerId
* Timestamp

**Record Found:**
* RecordId
* Version

**API Completed:**
* Duration
* Response Code

### Warning Logs
* No matching data found

### Error Logs
* CorrelationId
* Exception
* Stack Trace

### NEVER LOG
* Access tokens
* Secrets
* Connection strings

---

## Error Response Contract

```json
{
  "correlationId": "abc123",
  "errorCode": "NOT_FOUND",
  "message": "Credit application not found."
}
```

---

## Tests — Write All Four Types

### Unit Tests
* Valid record retrieval
* No record found
* Latest record selection
* JSON parsing (valid)
* JSON parsing (invalid)

### Integration Tests
* Database retrieval
* Authorization validation
* Error handling

### Security Tests
* SQL injection attempts
* Invalid identifiers
* Unauthorized access

### Performance Tests
* Large table volumes
* Concurrent API requests

---

## Design Principles — Follow All

* Separation of Concerns
* Clean Architecture
* Dependency Injection
* Repository Pattern
* Structured Logging
* Secure by Design
* Future-proof Dynamic Response Handling

---

## CAVEMAN MODE REMINDER

**YOU SEE DOCUMENT. YOU BUILD DOCUMENT. YOU NOT INVENT. YOU NOT DECORATE. YOU NOT IMPROVE. DOCUMENT IS LAW. BUILD THE THING.**

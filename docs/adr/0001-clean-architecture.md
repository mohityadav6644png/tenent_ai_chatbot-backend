# ADR 0001: Clean Architecture for Enterprise Chatbot Backend

## Status
Accepted

## Context
The backend requires strict multi-tenant isolation, enterprise text-to-SQL generation with guardrails, robust authentication, and separation between HTTP presentation, business logic, and cloud infrastructure adapters.

## Decision
We adopted Clean Architecture with the following layer separation:
1. **Core**: Configuration, JSON logging, JWT security, TenantContext, custom exceptions.
2. **API (Presentation)**: HTTP request handlers, dependency injection, and Pydantic contract schemas.
3. **Modules (Business Logic)**: Pure domain logic (Auth, Chat Text-to-SQL, SQLGuard, Tenants, Reports, Audit).
4. **Infrastructure (Adapters)**: Real database and cloud implementations (Azure OpenAI, Azure Key Vault, Redis, MongoDB, PostgreSQL).
5. **Container**: Composition root wiring interfaces to concrete implementations.

## Consequences
- Clean separation of concerns.
- Testability without external cloud dependencies.
- Pluggable infrastructure adapters.


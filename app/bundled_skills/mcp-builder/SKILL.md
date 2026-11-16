---
name: mcp-builder
description: Design and build a Model Context Protocol (MCP) server: workflow-shaped tools, clear names and schemas, actionable errors, pagination, auth, and an evaluation pass. Use when exposing an API or system to LLM agents via MCP.
metadata:
  category: Build
  inspired-by: anthropics/skills
---
# MCP Builder

An MCP server is an interface for a model, not a thin wrapper over every endpoint. Design for the tasks an agent will actually perform.

## 1. Research

- Learn the target system: API docs, auth model, rate limits, pagination style, data sizes.
- Check the current MCP specification and the official SDK for the chosen language (TypeScript and Python SDKs are the most mature). Read the SDK README for the current server, tool-registration and transport APIs instead of relying on memory; they change.
- Ask the user for 3-10 concrete tasks the agent should accomplish ("find open invoices over 30 days for a customer and draft a reminder"). These drive tool design and the evaluation.

## 2. Design tools

- Build tools around workflows. Merge chains the agent would always call together into one tool; keep separate only what is useful alone.
- Names: verb_noun, consistent prefix per service (`billing_search_invoices`, `billing_get_invoice`).
- Descriptions: what it does, when to use it versus similar tools, what it returns. Write them for a model that has never seen the API.
- Input schemas: typed, constrained (enums, min/max, formats), required fields minimal, sensible defaults. Accept human identifiers (name, email) where the API needs opaque IDs, and resolve them.
- Outputs: return only fields the agent needs; offer a `detail` or `format` option (concise vs full) for large objects. Use human-readable names alongside IDs.
- Mark read-only and destructive tools with the SDK's annotations; destructive tools should require explicit parameters, never defaults.

## 3. Implement

- Pagination: accept `limit` and `cursor`/`offset`, return `has_more` and the next cursor. Cap response size and say when output was truncated and how to narrow it.
- Errors: return messages the agent can act on ("Customer 'acme' not found; try billing_search_customers with a partial name"), not stack traces. Map auth, rate-limit and validation errors distinctly.
- Auth: read credentials from environment variables or the host's secret store; never hardcode, log or return them. Use least-privilege scopes.
- Transport: stdio for local single-user servers; streamable HTTP for remote ones, with authentication and origin checks.
- Respect rate limits with backoff; set timeouts on every outbound call.
- Share one client and helper layer across tools; keep each tool handler thin.

## 4. Test

- Unit-test handlers with the upstream API mocked: happy path, empty result, pagination, each error class.
- Run the SDK's inspector or a minimal client to list tools and call each one against a sandbox account.

## 5. Evaluate

Write 10 realistic, read-only evaluation tasks from the user's workflows, each needing several tool calls and having a single verifiable answer. Run an agent on them, read the transcripts, and fix tool names, descriptions, defaults and errors where the agent stumbled. Repeat until the agent succeeds reliably.

Report: tool list with one-line purpose, how to configure and run, eval results, known limits.

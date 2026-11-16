---
name: domain-model
description: Build a shared domain model and glossary (entities, relationships, invariants, ubiquitous language) from the code and the user, flagging naming conflicts. Use when terms are confusing, before a big feature, or onboarding.
metadata:
  category: Plan
  inspired-by: mattpocock/skills
---
# Domain Model

Make the language of the domain explicit so code, docs and conversation use the same words for the same things.

## 1. Mine the code

- Find the core nouns: models, database tables and migrations, API schemas, type definitions, enums, route names. Useful starts: `grep -rn "class \|interface \|type \|CREATE TABLE" <src>` narrowed to the relevant dirs.
- For each candidate entity record: where it is defined, its identity (what makes two instances the same), key fields, lifecycle states, and who creates, changes and deletes it.
- Capture relationships with cardinality (one-to-many, optional, owned vs referenced).
- Look for invariants enforced in code: validation, constraints, state-machine checks, uniqueness. Note invariants that seem implied but are not enforced.

## 2. Find language problems

Flag each of these with file references:

- **Synonyms**: one concept, several names (`customer`, `client`, `account_holder`).
- **Homonyms**: one name, several concepts (`account` meaning login and billing entity).
- **Drift**: UI, API and database disagree on a name or meaning.
- **Vague names**: `data`, `item`, `info`, `manager` hiding a real concept.
- **Missing concepts**: logic repeatedly operating on a bundle of fields that has no name.

## 3. Interrogate the user

Ask one question at a time about what the code cannot tell you, each with your best guess:

- "Is an Order still an Order after it is cancelled, or does it become something else?"
- "Can a Project exist without an Owner?"
- "Which name should win: `workspace` or `team`?"

Prefer the term the users of the product actually say.

## 4. Write the model

Save as markdown (for example `docs/domain.md`, or where the user wants) with:

```
## Glossary
**Term** -- definition in one or two sentences. Aliases to avoid: x, y. Code: path/to/def

## Entities
### <Entity>
Identity: ...  Lifecycle: draft -> active -> archived
Fields that matter: ...
Invariants: ...

## Relationships
Order 1..* LineItem (owned; deleted with order)

## Naming conflicts
- <conflict> -- recommendation -- files affected
```

A small mermaid `erDiagram` or `classDiagram` helps when there are more than five entities.

## Rules

- Describe the domain, not the framework. Skip ORM plumbing and DTO wrappers unless they leak meaning.
- Do not rename anything in code from this skill. List recommended renames with their blast radius and let the user decide.
- Mark guesses as guesses until the user confirms them.

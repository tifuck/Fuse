---
name: postmortem
description: Write a blameless incident postmortem with timeline, impact, root cause, lessons and owned action items. Use after an outage, data loss, security incident, failed deploy or near miss, or when asked for an incident review or RCA.
metadata:
  category: Ops
---
# Postmortem

Explain how the system allowed the failure, and change the system so it cannot happen the same way again. Blameless: describe actions and decisions, not people's character.

## Gather evidence first
- Collect logs, alerts, dashboards, deploy and config history (`git log --since`, CI runs), chat or ticket timelines, and status page updates the user can share.
- Use timestamps in one timezone (UTC) throughout.
- Separate facts (seen in a log or graph) from recollection and from hypothesis. Mark unknowns as unknown.
- Do not paste secrets, personal data or customer identifiers into the document.

## Document structure
```
# <Title: what broke, date>
Status: draft | reviewed   Severity: <scale used by the team>
Authors / reviewers

## Summary
Two to four sentences: what happened, impact, duration, root cause, fix.

## Impact
Who was affected, how many, for how long, what failed (errors, latency, data), SLO budget used, money or data lost.

## Timeline (UTC)
HH:MM  event (trigger, first symptom, detection, escalation, mitigation, resolution)

## Root cause
## Contributing factors
## Detection and response
## What went well
## What went badly
## Where we got lucky
## Action items
```

## Root cause with five whys
Start from the user-visible failure and ask why repeatedly, each answer backed by evidence:
1. Why did checkout fail? The payments service returned 500s.
2. Why? Its database connection pool was exhausted.
3. Why? A new query held connections for seconds.
4. Why? It missed an index and review did not include a query plan.
5. Why? No check or load test covers new queries.
Stop when you reach something the team can change in process or system. Incidents usually have several contributing causes; list them rather than forcing a single one. "Human error" is never a root cause; ask why the system made the error easy and its effect large.

## Key durations
Note time to detect, time to mitigate and time to resolve. Long detection points at monitoring; long mitigation points at runbooks, rollback paths or access.

## Action items
Each item must be:
- Specific and verifiable ("alert when pool usage exceeds 80% for 5 min", not "improve monitoring").
- Owned by a named person or team, with a due date or ticket.
- Typed: prevent, detect, mitigate, or process.
Prefer items that become automatic checks: a test reproducing the failure, a CI or lint rule, an alert, a migration guard, a review checklist item with a clear trigger. A checklist item nobody runs does not count.
Keep the list short and prioritized; three done beats ten open.

## Finish
- Read it as an outsider: could someone on another team understand what happened and why?
- Check every claim in the timeline against a source.
- List open questions explicitly instead of guessing.

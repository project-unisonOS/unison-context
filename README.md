# unison-context

The context service tracks the active state of the user and environment.

Responsibilities (future):
- Store per-user preferences, accessibility needs, current task context.
- Maintain ephemeral session memory.
- Expose read / write / subscribe API to other modules.
- Respect privacy zones defined in `unison-spec/docs/safety.md`.

Current state:
- Minimal HTTP service with `/health` and `/ready`.
- Containerized for inclusion in `unison-devstack`.

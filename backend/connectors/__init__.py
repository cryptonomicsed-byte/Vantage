"""Optional mode connectors — additive only.

Native backends are the default (AgentTV pipeline, Gitea worktrees, the
internal signal bus). A connector is a thin adapter that, when its API key or
webhook is configured, forwards a mode request to an external backend and
returns the result to the lounge. Unconfigured is not an error: the response
says so and the native path remains untouched.

P7 gate: /video --connector higgsfield (or equivalent) invokes the external
backend and posts the result to the lounge.
"""

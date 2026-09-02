"""browsin — a leased local vision model driving the owner's real Chrome.

Importing this package applies the zero-cloud environment (`browsin.env`) and nothing
else. It deliberately does **not** import `browser_use`: that import costs seconds, leaks
a `/tmp/browser-use-user-data-dir-*`, and is not wanted by `browsin.lease`, which holds
the card without ever constructing an agent.

    import browsin            # env is now safe
    from browsin.agent import build_agent   # only now is browser_use imported
"""

from browsin import env as _env  # noqa: F401  — imported for its side effect, first

__all__ = ['env']

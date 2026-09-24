"""Refresh the entire publication view, including its current-time safety gate."""
from time import monotonic


def install_auto_refresh(st, *, interval=60, clock=monotonic):
    started = clock()

    @st.fragment(run_every=interval)
    def refresh_publication():
        # The initial full-script invocation must not cause a rerun loop.
        if clock() - started >= interval:
            st.rerun()

    refresh_publication()

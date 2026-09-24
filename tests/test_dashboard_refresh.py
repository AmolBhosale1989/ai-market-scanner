from scanner.dashboard_refresh import install_auto_refresh


def test_timer_reruns_full_app_without_initial_loop():
    class Streamlit:
        reruns = 0

        def fragment(self, *, run_every):
            self.interval = run_every
            def decorate(fn):
                self.tick = fn
                return fn
            return decorate

        def rerun(self):
            self.reruns += 1

    st = Streamlit()
    now = [100.0]
    install_auto_refresh(st, clock=lambda: now[0])
    assert st.reruns == 0
    assert st.interval == 60
    now[0] = 159.9
    st.tick()
    assert st.reruns == 0
    now[0] = 160.0
    st.tick()
    assert st.reruns == 1
    # A full rerun installs a new timer rather than immediately rerunning again.
    install_auto_refresh(st, clock=lambda: now[0])
    assert st.reruns == 1


def test_timer_registered_before_publication_read_and_fail_closed_stop():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert source.index("install_auto_refresh(st)") < source.index("production_manifest=publication_info") < source.index("st.stop()")

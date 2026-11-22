import desktop


def test_new_window_shares_engine_but_starts_with_a_fresh_session():
    calls = []
    class Backend:
        def create_window(self, *args, **kwargs):
            calls.append((args, kwargs))
    windows = desktop.DesktopWindows("http://127.0.0.1:8766", Backend())
    assert windows.new_window()["opened"]
    assert calls[0][0] == ("MasterClip", "http://127.0.0.1:8766/?new=1")
    assert calls[0][1]["js_api"] is windows

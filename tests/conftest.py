import pytest


@pytest.fixture(autouse=True)
def _clear_kinozal_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambient KINOZAL_USERNAME/PASSWORD must never leak into a test.

    After the #227 mirror-fallback refactor, a primary `fetch_html` failure
    triggers a real `login()` against kinozal.guru when credentials are present.
    A developer who set those vars locally (e.g. for a manual E2E trial per the
    ci.md instructions) would otherwise see fetch-failure tests hit the network.
    Tests that exercise the mirror set the credentials explicitly within the
    test body, so clearing the ambient values here is always safe.
    """
    monkeypatch.delenv("KINOZAL_USERNAME", raising=False)
    monkeypatch.delenv("KINOZAL_PASSWORD", raising=False)

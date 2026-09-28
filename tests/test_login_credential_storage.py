from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_login_form_supports_browser_password_managers():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="authForm" autocomplete="on"' in html
    assert 'id="authEmail" name="username"' in html
    assert 'autocomplete="username"' in html
    assert 'id="authPassword" name="password" type="password"' in html
    assert 'autocomplete="current-password"' in html
    assert 'id="rememberCredentials" type="checkbox"' in html


def test_only_email_is_persisted_in_local_storage():
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'localStorage.setItem(rememberedLoginEmailKey, email)' in javascript
    assert 'new PasswordCredential({ id: email, password' in javascript
    assert 'localStorage.setItem(rememberedLoginEmailKey, password)' not in javascript
    assert 'localStorage.setItem("password"' not in javascript


def test_login_form_includes_password_recovery_without_persisting_token():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="forgotPasswordButton"' in html
    assert 'id="passwordResetRequestForm"' in html
    assert 'id="passwordResetConfirmForm"' in html
    assert "window.history.replaceState" in javascript
    assert 'localStorage.setItem("passwordResetAccessToken"' not in javascript

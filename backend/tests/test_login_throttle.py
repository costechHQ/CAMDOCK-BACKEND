import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.throttle import FailureThrottle, LoginThrottle
from tests.test_auth_api import EMAIL, PASSWORD, add_user, login


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def failure_throttle(clock, max_failures=5, window=900, block=900, **kwargs):
    return FailureThrottle(max_failures, window, block, clock=clock, **kwargs)


# --- the failure counter ---------------------------------------------------------


def test_the_nth_failure_blocks_and_the_one_before_does_not():
    clock = Clock()
    throttle = failure_throttle(clock)

    started = [throttle.record_failure("a") for _ in range(5)]

    assert started == [False, False, False, False, True]
    assert throttle.retry_after("a") == 900


def test_four_failures_leave_the_key_open():
    throttle = failure_throttle(Clock())
    for _ in range(4):
        throttle.record_failure("a")

    assert throttle.retry_after("a") == 0


def test_the_block_ends_at_the_stated_time():
    clock = Clock()
    throttle = failure_throttle(clock)
    for _ in range(5):
        throttle.record_failure("a")

    clock.advance(899)
    assert throttle.retry_after("a") == 1
    clock.advance(1)
    assert throttle.retry_after("a") == 0


def test_attempts_during_a_block_do_not_extend_it():
    clock = Clock()
    throttle = failure_throttle(clock)
    for _ in range(5):
        throttle.record_failure("a")
    clock.advance(600)

    assert throttle.record_failure("a") is False

    assert throttle.retry_after("a") == 300


def test_a_fresh_count_starts_after_a_block_ends():
    clock = Clock()
    throttle = failure_throttle(clock)
    for _ in range(5):
        throttle.record_failure("a")
    clock.advance(900)

    for _ in range(4):
        assert throttle.record_failure("a") is False
    assert throttle.retry_after("a") == 0


def test_failures_older_than_the_window_are_forgotten():
    clock = Clock()
    throttle = failure_throttle(clock)
    for _ in range(4):
        throttle.record_failure("a")
    clock.advance(900)

    assert throttle.record_failure("a") is False
    assert throttle.retry_after("a") == 0


def test_keys_are_independent():
    throttle = failure_throttle(Clock())
    for _ in range(5):
        throttle.record_failure("a")

    assert throttle.retry_after("b") == 0


def test_reset_forgets_the_failures():
    throttle = failure_throttle(Clock())
    for _ in range(4):
        throttle.record_failure("a")
    throttle.reset("a")

    assert throttle.record_failure("a") is False
    assert throttle.retry_after("a") == 0


def test_memory_is_capped():
    throttle = failure_throttle(Clock(), max_failures=2, max_keys=50)

    for number in range(500):
        throttle.record_failure(f"noise-{number}")

    assert len(throttle._entries) <= 50


def test_expired_keys_are_dropped_before_live_ones_are_evicted():
    clock = Clock()
    throttle = failure_throttle(clock, max_failures=2, max_keys=3)
    for key in ("old1", "old2"):
        throttle.record_failure(key)
    clock.advance(1000)
    throttle.record_failure("live")
    throttle.record_failure("live2")

    assert set(throttle._entries) == {"live", "live2"}


# --- the login endpoint ----------------------------------------------------------


@pytest.fixture
def clock(api_client):
    """Swap the app's throttle for one driven by a fake clock."""
    fake = Clock()
    window = 900
    api_client.app.state.login_throttle = LoginThrottle(
        failure_throttle(fake, 5, window, window),
        failure_throttle(fake, 20, window, window),
    )
    return fake


@pytest.fixture
def account(db):
    add_user(db)


def fail(client, times, email=EMAIL):
    return [
        login(client, email=email, password="wrong").status_code for _ in range(times)
    ]


def test_five_wrong_passwords_then_the_sixth_attempt_is_refused(
    api_client, clock, account
):
    assert fail(api_client, 5) == [401] * 5

    response = login(api_client, password="wrong")

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "too_many_attempts",
            "message": "Too many failed sign-in attempts. Try again later.",
        }
    }
    assert response.headers["retry-after"] == "900"


def test_the_correct_password_is_refused_during_a_block(api_client, clock, account):
    fail(api_client, 5)

    assert login(api_client).status_code == 429


def test_the_correct_password_works_after_the_block_expires(api_client, clock, account):
    fail(api_client, 5)
    clock.advance(900)

    assert login(api_client).status_code == 200


def test_retry_after_counts_down(api_client, clock, account):
    fail(api_client, 5)
    clock.advance(400)

    assert login(api_client).headers["retry-after"] == "500"


def test_a_blocked_attempt_does_not_extend_the_block(api_client, clock, account):
    fail(api_client, 5)
    clock.advance(600)
    login(api_client, password="wrong")

    clock.advance(300)

    assert login(api_client).status_code == 200


def test_other_emails_are_unaffected(api_client, clock, account, db):
    add_user(db, email="other@camdock.test")
    fail(api_client, 5)

    assert login(api_client, email="other@camdock.test").status_code == 200


def test_unknown_emails_are_throttled_with_the_same_response(api_client, clock, db):
    fail(api_client, 5, email="nobody@camdock.test")

    response = login(api_client, email="nobody@camdock.test")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_attempts"


def test_a_disabled_account_counts_like_any_other_failure(api_client, clock, db):
    add_user(db, is_active=False)

    assert fail(api_client, 5) == [401] * 5
    assert login(api_client).status_code == 429


def test_a_successful_login_clears_the_email_counter(api_client, clock, account):
    fail(api_client, 4)
    assert login(api_client).status_code == 200

    assert fail(api_client, 4) == [401] * 4
    assert login(api_client).status_code == 200


def test_email_case_and_spaces_do_not_dodge_the_limit(api_client, clock, account):
    for variant in (" Admin@Camdock.test", "ADMIN@CAMDOCK.TEST", EMAIL):
        login(api_client, email=variant, password="wrong")
    login(api_client, email=EMAIL, password="wrong")
    login(api_client, email=EMAIL, password="wrong")

    assert login(api_client, email="Admin@camdock.test").status_code == 429


def test_one_address_guessing_many_emails_is_blocked_by_the_ip_limit(
    api_client, clock, account
):
    for number in range(20):
        login(api_client, email=f"user{number}@camdock.test", password="wrong")

    response = login(api_client)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_attempts"


def test_the_ip_limit_is_not_reached_by_a_few_failures(api_client, clock, account):
    for number in range(19):
        login(api_client, email=f"user{number}@camdock.test", password="wrong")

    assert login(api_client).status_code == 200


def test_a_forwarded_header_does_not_dodge_the_limit(api_client, clock, account):
    for number in range(5):
        api_client.post(
            "/api/v1/auth/login",
            json={"email": EMAIL, "password": "wrong"},
            headers={"X-Forwarded-For": f"198.51.100.{number}"},
        )

    response = api_client.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.200"},
    )

    assert response.status_code == 429


def test_a_failure_to_log_in_never_returns_a_throttle_hint_before_the_limit(
    api_client, clock, account
):
    response = login(api_client, password="wrong")

    assert response.status_code == 401
    assert "retry-after" not in response.headers


def test_the_log_names_the_address_but_no_credentials(
    api_client, clock, account, caplog
):
    with caplog.at_level(logging.INFO):
        fail(api_client, 5)
        login(api_client)

    text = caplog.text.lower()
    assert "blocked" in text
    for forbidden in (EMAIL, "wrong", PASSWORD, "password"):
        assert forbidden not in text


def test_the_response_body_leaks_nothing(api_client, clock, account):
    fail(api_client, 5)

    text = login(api_client).text.lower()

    assert EMAIL not in text
    assert "token" not in text


def test_each_app_instance_has_its_own_counters(make_client):
    first = make_client()
    second = make_client()

    assert first.app.state.login_throttle is not second.app.state.login_throttle


def test_the_openapi_contract_documents_the_429():
    from tests.test_openapi_contract import load_exporter

    login_operation = load_exporter().build_openapi()["paths"]["/api/v1/auth/login"]

    assert "429" in login_operation["post"]["responses"]


# --- configuration ---------------------------------------------------------------


def test_the_defaults_match_the_policy():
    settings = get_settings()

    assert settings.login_max_failures == 5
    assert settings.login_ip_max_failures == 20
    assert settings.login_window_minutes == 15
    assert settings.login_block_minutes == 15
    assert settings.trusted_proxy_networks == ()


@pytest.mark.parametrize(
    "name",
    [
        "login_max_failures",
        "login_ip_max_failures",
        "login_window_minutes",
        "login_block_minutes",
    ],
)
def test_limits_below_one_are_rejected(name):
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://u:p@h/db", **{name: 0})


def test_a_malformed_proxy_list_stops_startup():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://u:p@h/db", trusted_proxies="oops")


def test_proxy_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8, 192.168.0.1")
    get_settings.cache_clear()

    networks = get_settings().trusted_proxy_networks

    assert [str(n) for n in networks] == ["10.0.0.0/8", "192.168.0.1/32"]

"""Stable source updates may be quarantined until a release has aged."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hermes_cli import source_releases
from hermes_cli import source_check
from hermes_cli import update_cmd_check

pytestmark = pytest.mark.real_release_channels


def test_stable_target_selects_newest_release_old_enough(monkeypatch):
    now = datetime(2026, 10, 29, 12, tzinfo=UTC)
    base = "https://api.github.com/repos/NousResearch/hermes-agent"
    old_sha = "a" * 40
    responses = {
        f"{base}/releases?per_page=100&page=1": [
            {
                "tag_name": "v0.22.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-10-25T12:00:00Z",
            },
            {
                "tag_name": "v0.21.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-10-15T12:00:00Z",
            },
        ],
        f"{base}/commits/v0.21.0": {"sha": old_sha},
    }
    monkeypatch.setattr(source_releases, "_json", lambda url: responses[url])

    target = source_releases.resolve_source_target(
        "stable",
        repository="NousResearch/hermes-agent",
        minimum_release_age_days=14,
        now=now,
    )

    assert (target.version, target.commit) == ("0.21.0", old_sha)


def test_stable_target_explains_when_no_release_has_aged(monkeypatch):
    now = datetime(2026, 10, 29, 12, tzinfo=UTC)
    base = "https://api.github.com/repos/NousResearch/hermes-agent"
    monkeypatch.setattr(
        source_releases,
        "_json",
        lambda url: [
            {
                "tag_name": "v0.22.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-10-25T12:00:00Z",
            }
        ] if url == f"{base}/releases?per_page=100&page=1" else {},
    )

    with pytest.raises(ValueError, match="at least 14 days old"):
        source_releases.resolve_source_target(
            "stable",
            repository="NousResearch/hermes-agent",
            minimum_release_age_days=14,
            now=now,
        )


def test_stable_target_handles_unbounded_quarantine_without_datetime_overflow(monkeypatch):
    now = datetime(2026, 10, 29, 12, tzinfo=UTC)
    base = "https://api.github.com/repos/NousResearch/hermes-agent"
    monkeypatch.setattr(
        source_releases,
        "_json",
        lambda url: [
            {
                "tag_name": "v0.21.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2020-01-01T00:00:00Z",
            }
        ] if url == f"{base}/releases?per_page=100&page=1" else {},
    )

    with pytest.raises(ValueError, match="at least 1000000000 days old"):
        source_releases.resolve_source_target(
            "stable",
            repository="NousResearch/hermes-agent",
            minimum_release_age_days=1_000_000_000,
            now=now,
        )


@pytest.mark.parametrize("value", [-1, True, "14", None])
def test_minimum_release_age_rejects_non_integer_or_negative_config(value):
    with pytest.raises(ValueError, match="non-negative integer"):
        source_releases.minimum_release_age_days(
            {"updates": {"minimum_release_age_days": value}}
        )


def test_minimum_release_age_defaults_to_disabled():
    assert source_releases.minimum_release_age_days({}) == 0


@pytest.mark.parametrize("channel", ["canary", "preview"])
def test_minimum_release_age_does_not_change_non_stable_channels(channel, monkeypatch):
    request = {
        "commit": "d" * 40,
        "sourceVersion": "0.22.0",
        "buildId": "build-1",
    }
    record = {
        "name": channel,
        "repository": "NousResearch/hermes-agent",
        "state": "active",
        "policy": "canary-release" if channel == "canary" else "preview",
    }
    monkeypatch.setattr(
        source_releases,
        "_resolve_channel",
        lambda *_: SimpleNamespace(requested=record, terminal=record, manifest={"request": request}),
    )

    target = source_releases.resolve_source_target(
        channel,
        repository="NousResearch/hermes-agent",
        minimum_release_age_days=14,
    )

    assert target.commit == request["commit"]


def test_minimum_release_age_does_not_change_main_channel():
    target = source_releases.resolve_source_target(
        "main",
        repository="NousResearch/hermes-agent",
        minimum_release_age_days=14,
    )

    assert target.branch == "main"


def test_source_check_reads_minimum_age_from_profile_config(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    home = tmp_path / "home"
    (root / ".git").mkdir(parents=True)
    home.mkdir()
    (home / "config.yaml").write_text(
        "updates:\n  minimum_release_age_days: 14\n",
        encoding="utf-8",
    )
    head = "b" * 40
    monkeypatch.setattr(
        source_check,
        "_read_checkout",
        lambda *_: source_check._Checkout(
            root, "git", None, head, "main",
            "https://github.com/NousResearch/hermes-agent.git",
            "NousResearch/hermes-agent", False,
        ),
    )
    seen = []

    def resolve(channel, *args, **kwargs):
        seen.append(kwargs["minimum_release_age_days"])
        return source_releases.SourceTarget(
            channel, channel, "NousResearch/hermes-agent", commit=head, version="0.21.0"
        )

    monkeypatch.setattr(source_check, "resolve_source_target", resolve)

    status = source_check.check_for_updates(
        install_root=root,
        home=home,
        channel="stable",
        force=True,
    )

    assert seen == [14]
    assert status["updateAvailable"] is False


def test_explicit_update_check_honors_configured_minimum_age(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "checkout"
    home.mkdir()
    root.mkdir()
    (home / "config.yaml").write_text(
        "updates:\n  minimum_release_age_days: 14\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    seen = []

    def resolve(channel, *args, **kwargs):
        seen.append(kwargs["minimum_release_age_days"])
        return source_releases.SourceTarget(
            channel, channel, "NousResearch/hermes-agent", branch="main"
        )

    monkeypatch.setattr(source_releases, "resolve_source_target", resolve)

    branch = update_cmd_check.channel_compare_branch("stable", ["git"], root)

    assert branch == "main"
    assert seen == [14]


def test_update_apply_honors_configured_minimum_age(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "checkout"
    home.mkdir()
    root.mkdir()
    (home / "config.yaml").write_text(
        "updates:\n  minimum_release_age_days: 14\n",
        encoding="utf-8",
    )
    sha = "c" * 40
    seen = []

    def resolve(channel, *args, **kwargs):
        seen.append(kwargs["minimum_release_age_days"])
        return source_releases.SourceTarget(
            channel, channel, "NousResearch/hermes-agent", commit=sha, version="0.21.0"
        )

    monkeypatch.setattr(source_releases, "resolve_source_target", resolve)
    monkeypatch.setattr(
        update_cmd_check,
        "_uc",
        lambda: SimpleNamespace(
            _m=lambda: SimpleNamespace(PROJECT_ROOT=root),
            _update_run_channel=lambda args: "stable",
        ),
    )
    request = {"home": str(home)}

    target = update_cmd_check.select_apply_target(
        SimpleNamespace(branch=None, channel=None),
        "main",
        request,
        git_cmd=["git"],
        stop=lambda: None,
    )

    assert target[:2] == (sha, sha)
    assert seen == [14]

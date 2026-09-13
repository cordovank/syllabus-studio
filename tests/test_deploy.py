"""Deploying the site to GitHub Pages (spec 003 §4a), against real git, offline.

Every test builds a throwaway app repository with a local bare repository as its
``origin``, so nothing touches the network or the real repo. The developer's own
git config is shut out, so settings like commit signing can't leak in.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from syllabus_studio.core.models import Course
from syllabus_studio.deploy import (
    DeployError,
    commit_message,
    deploy,
    git_version,
    is_site_worktree,
    pages_url,
    plan_deploy,
    site_deploy_status,
    site_init,
)
from syllabus_studio.storage import CourseBundle
from syllabus_studio.storage.site import publish_to_site, stamp_provenance, unpublish


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


@pytest.fixture
def app_repo(tmp_path: Path) -> Path:
    """An app repository with one commit on main and a bare ``origin``."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--quiet", "--bare", str(origin))
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    (repo / "README.md").write_text("app\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "app")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "--quiet", "origin", "main")
    return repo


def _publish(site: Path, course: Course, *, reviewer: str = "") -> None:
    bundle = CourseBundle.build(course, {})
    stamped = stamp_provenance(
        bundle, author_provider="echo", author_model="echo", reviewer=reviewer, published=True
    )
    publish_to_site(site, stamped)


def _course(course: Course, cid: str, title: str) -> Course:
    return course.model_copy(deep=True, update={"id": cid, "title": title})


# --- site init ---------------------------------------------------------------------


def test_site_init_creates_an_orphan_worktree_and_is_idempotent(app_repo: Path) -> None:
    site = app_repo / "site"
    first = site_init(site)

    assert is_site_worktree(site, "gh-pages")
    assert (site / ".nojekyll").is_file() and (site / "index.html").is_file()
    assert "Settings → Pages" in first.pages_hint
    assert "git push" not in first.pages_hint

    site_init(site)  # again: no error, same worktree
    assert is_site_worktree(site, "gh-pages")
    assert git(app_repo, "worktree", "list").count(str(site.resolve())) == 1


def test_the_site_branch_shares_no_history_with_the_code(app_repo: Path, course: Course) -> None:
    site = app_repo / "site"
    site_init(site)
    _publish(site, course)
    deploy(site)

    merge_base = subprocess.run(
        ["git", "merge-base", "main", "gh-pages"], cwd=app_repo, capture_output=True
    )
    assert merge_base.returncode != 0, "course content never enters the code's history"


def test_site_init_refuses_a_plain_folder_rather_than_moving_it(app_repo: Path) -> None:
    site = app_repo / "site"
    site.mkdir()
    (site / "catalog.json").write_text("{}", encoding="utf-8")

    with pytest.raises(DeployError) as err:
        site_init(site)
    assert err.value.code == "site_not_empty"
    assert (site / "catalog.json").read_text(encoding="utf-8") == "{}", "left untouched"


def test_site_init_reuses_a_gh_pages_branch_that_already_exists_on_origin(
    app_repo: Path, tmp_path: Path, course: Course
) -> None:
    site = app_repo / "site"
    site_init(site)
    _publish(site, course)
    pushed = deploy(site)["commit"]

    # a fresh clone of the app, on another machine
    clone = tmp_path / "elsewhere"
    git(tmp_path, "clone", "--quiet", str(tmp_path / "origin.git"), str(clone))
    site_init(clone / "site")
    assert git(clone / "site", "rev-parse", "HEAD") == pushed


# --- deploy ------------------------------------------------------------------------


def test_deploy_refuses_until_site_init_and_when_nothing_changed(
    app_repo: Path, course: Course
) -> None:
    site = app_repo / "site"
    site.mkdir()
    with pytest.raises(DeployError) as err:
        plan_deploy(site)
    assert err.value.code == "not_initialised"
    assert "site init" in err.value.message

    site.rmdir()
    site_init(site)
    _publish(site, course)
    deploy(site)
    with pytest.raises(DeployError) as err:
        plan_deploy(site)
    assert err.value.code == "nothing_to_deploy"


def test_a_dry_run_reports_courses_and_commits_nothing(app_repo: Path, course: Course) -> None:
    site = app_repo / "site"
    site_init(site)
    _publish(site, _course(course, "ml", "Applied ML"), reviewer="Ada")
    _publish(site, _course(course, "stats", "Statistics"))

    plan = plan_deploy(site)

    assert {c["title"] for c in plan["added"]} == {"Applied ML", "Statistics"}
    assert plan["updated"] == plan["removed"] == []
    assert plan["unreviewed"] == ["Statistics"], "flagged before it goes live"
    unborn = subprocess.run(["git", "rev-parse", "HEAD"], cwd=site, capture_output=True)
    assert unborn.returncode != 0, "no commit was made"


def test_deploy_commits_and_pushes_to_the_remote_gh_pages(
    app_repo: Path, tmp_path: Path, course: Course
) -> None:
    site = app_repo / "site"
    site_init(site)
    _publish(site, _course(course, "ml", "Applied ML"))

    done = deploy(site)

    remote_head = git(tmp_path / "origin.git", "rev-parse", "gh-pages")
    assert remote_head == done["commit"]
    assert git(site, "log", "-1", "--format=%s") == "Publish: Applied ML (added)"
    assert site_deploy_status(site, branch="gh-pages", remote="origin") == {
        "initialised": True,
        "pending": 0,
        "ahead": 0,
    }


def test_the_next_deploy_sees_updates_and_removals(app_repo: Path, course: Course) -> None:
    site = app_repo / "site"
    site_init(site)
    _publish(site, _course(course, "ml", "Applied ML"))
    _publish(site, _course(course, "stats", "Statistics"))
    deploy(site)

    _publish(site, _course(course, "ml", "Applied ML"), reviewer="Ada")  # republished
    unpublish(site, "stats")

    plan = plan_deploy(site)
    assert [c["id"] for c in plan["updated"]] == ["ml"]
    assert [c["id"] for c in plan["removed"]] == ["stats"]
    assert plan["added"] == []
    assert commit_message(plan) == "Publish: Applied ML (updated), Statistics (removed)"
    assert site_deploy_status(site, branch="gh-pages", remote="origin")["pending"] > 0


def test_a_rejected_push_keeps_the_commit_and_never_forces(
    app_repo: Path, tmp_path: Path, course: Course
) -> None:
    site = app_repo / "site"
    site_init(site)
    _publish(site, _course(course, "ml", "Applied ML"))
    deploy(site)

    # someone else deploys from another clone first
    other = tmp_path / "other"
    git(tmp_path, "clone", "--quiet", "-b", "gh-pages", str(tmp_path / "origin.git"), str(other))
    (other / "CNAME").write_text("courses.example.com\n", encoding="utf-8")
    git(other, "add", "CNAME")
    git(other, "commit", "--quiet", "-m", "elsewhere")
    git(other, "push", "--quiet", "origin", "gh-pages")
    theirs = git(tmp_path / "origin.git", "rev-parse", "gh-pages")

    _publish(site, _course(course, "stats", "Statistics"))
    with pytest.raises(DeployError) as err:
        deploy(site)

    assert err.value.code == "push_rejected"
    assert "pull" in err.value.message
    assert git(site, "log", "-1", "--format=%s") == "Publish: Statistics (added)", "commit kept"
    assert git(tmp_path / "origin.git", "rev-parse", "gh-pages") == theirs, "nothing forced"

    # after pulling, deploying again pushes the kept commit without committing twice
    git(site, "pull", "--quiet", "--rebase", "origin", "gh-pages")
    plan = plan_deploy(site)
    assert plan["pendingFiles"] == 0 and plan["unpushedCommits"] == 1
    assert deploy(site)["commit"] == git(tmp_path / "origin.git", "rev-parse", "gh-pages")


def test_deploy_without_a_remote_commits_and_says_so(tmp_path: Path, course: Course) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    (repo / "x").write_text("x", encoding="utf-8")
    git(repo, "add", "x")
    git(repo, "commit", "--quiet", "-m", "x")
    site = repo / "site"
    site_init(site)
    _publish(site, course)

    with pytest.raises(DeployError) as err:
        deploy(site)
    assert err.value.code == "no_remote"
    assert git(site, "rev-parse", "HEAD"), "the commit exists locally"


# --- small pieces ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remote", "url"),
    [
        ("https://github.com/cordovank/syllabus-studio.git", "https://cordovank.github.io/syllabus-studio/"),
        ("https://github.com/cordovank/syllabus-studio", "https://cordovank.github.io/syllabus-studio/"),
        ("git@github.com:cordovank/syllabus-studio.git", "https://cordovank.github.io/syllabus-studio/"),
        ("ssh://git@github.com/CordovaNK/courses.git", "https://cordovank.github.io/courses/"),
        ("https://github.com/cordovank/cordovank.github.io.git", "https://cordovank.github.io/"),
        ("https://gitlab.com/cordovank/syllabus-studio.git", None),
        ("/tmp/origin.git", None),
    ],
)
def test_the_pages_url_comes_from_the_remote(remote: str, url: str | None) -> None:
    assert pages_url(remote) == url


def test_git_versions_are_compared_numerically() -> None:
    assert git_version("git version 2.53.0") == (2, 53)
    assert git_version("git version 2.9.1 (Apple Git-1)") == (2, 9)
    assert git_version("git version 2.9.1") < (2, 42)

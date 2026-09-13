"""Deploy the site to GitHub Pages: an orphan ``gh-pages`` branch, checked out as a
git worktree at ``SS_SITE_DIR`` (spec 003 §4a).

Two commands, both thin over ``git``:

- :func:`site_init` sets up the worktree once. It never pushes and never enables
  Pages — that changes a public setting, so it only prints how.
- :func:`plan_deploy` says what would go live; :func:`deploy` commits and pushes it.
  The CLI shows the plan and asks before calling ``deploy``.

Lives beside ``publishing.py`` rather than in ``core``: it runs ``git`` against
files, and ``core`` must not learn about either. Never force-pushes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from syllabus_studio.storage.site import COURSES_DIR, write_reader_files

MIN_GIT = (2, 42)  # `git worktree add --orphan`


class DeployError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- git ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        raise DeployError("no_git", "git isn't installed, and deploying needs it.")
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise DeployError("git_failed", f"git {' '.join(args)} failed: {detail}")
    return proc


def git_version(text: str) -> tuple[int, int]:
    m = re.search(r"(\d+)\.(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def pages_url(remote_url: str) -> str | None:
    """``https://<owner>.github.io/<repo>/`` for a GitHub remote, else None.

    A repository named ``<owner>.github.io`` is a user site, served at the root.
    """
    m = re.match(
        r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        remote_url.strip(),
    )
    if not m:
        return None
    owner, repo = m.group("owner"), m.group("repo")
    if repo.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{repo}/"


def _remote_url(cwd: Path, remote: str) -> str:
    proc = _git(cwd, "remote", "get-url", remote, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _owner_repo(remote_url: str) -> str:
    m = re.search(r"github\.com[:/](?P<path>[^/]+/[^/]+?)(?:\.git)?/?$", remote_url)
    return m.group("path") if m else "<owner>/<repo>"


def is_site_worktree(site_dir: Path, branch: str) -> bool:
    """A worktree whose top level *is* ``site_dir`` with ``branch`` checked out — not
    merely a folder that happens to sit inside the app's own repository."""
    site_dir = Path(site_dir)
    if not (site_dir / ".git").exists():
        return False
    top = _git(site_dir, "rev-parse", "--show-toplevel", check=False)
    head = _git(site_dir, "symbolic-ref", "--short", "HEAD", check=False)  # works unborn
    return (
        top.returncode == 0
        and Path(top.stdout.strip()).resolve() == site_dir.resolve()
        and head.stdout.strip() == branch
    )


# --- site init -----------------------------------------------------------------------


@dataclass
class InitResult:
    site_dir: Path
    branch: str
    done: list[str] = field(default_factory=list)
    pages_hint: str = ""


def site_init(site_dir: Path, *, branch: str = "gh-pages", remote: str = "origin") -> InitResult:
    """Make ``site_dir`` a worktree of ``branch``. Idempotent; never pushes."""
    site_dir = Path(site_dir).resolve()
    result = InitResult(site_dir=site_dir, branch=branch)
    repo = site_dir.parent

    if _git(repo, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        raise DeployError("not_a_repo", f"{repo} isn't inside a git repository.")

    if is_site_worktree(site_dir, branch):
        result.done.append(f"{site_dir} is already a worktree of {branch}")
    else:
        if site_dir.exists() and any(site_dir.iterdir()):
            raise DeployError(
                "site_not_empty",
                f"{site_dir} already exists as a plain folder, and a worktree needs an empty "
                f"path. It is generated — delete it, run `site init`, then publish again.",
            )
        have_local = (
            _git(
                repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
            ).returncode
            == 0
        )
        have_remote = bool(_remote_url(repo, remote)) and bool(
            _git(repo, "ls-remote", "--heads", remote, branch, check=False).stdout.strip()
        )
        if have_local:
            _git(repo, "worktree", "add", str(site_dir), branch)
            result.done.append(f"checked out the existing {branch} branch at {site_dir}")
        elif have_remote:
            _git(repo, "fetch", remote, branch)
            _git(repo, "worktree", "add", "-b", branch, str(site_dir), f"{remote}/{branch}")
            result.done.append(f"checked out {remote}/{branch} at {site_dir}")
        else:
            version = git_version(_git(repo, "--version").stdout)
            if version < MIN_GIT:
                raise DeployError(
                    "git_too_old",
                    f"Creating an orphan branch needs git {MIN_GIT[0]}.{MIN_GIT[1]} or newer; "
                    f"this is {version[0]}.{version[1]}.",
                )
            _git(repo, "worktree", "add", "--orphan", "-b", branch, str(site_dir))
            result.done.append(
                f"created {branch} as an orphan branch at {site_dir} — no history shared with "
                "your code"
            )

    written = write_reader_files(site_dir)
    result.done.append(f"refreshed the reader ({len(written)} files, .nojekyll)")

    result.pages_hint = (
        "Enable GitHub Pages yourself — it makes the site public:\n"
        f"  Settings → Pages → Deploy from a branch → {branch} / (root)\n"
        f"  or: gh api -X POST repos/{_owner_repo(_remote_url(repo, remote))}/pages "
        f'-f "source[branch]={branch}" -f "source[path]=/"'
    )
    return result


# --- deploy --------------------------------------------------------------------------


def _catalog_at_head(site_dir: Path) -> dict[str, Any]:
    proc = _git(site_dir, "show", "HEAD:catalog.json", check=False)
    if proc.returncode != 0:
        return {"entries": []}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"entries": []}


def _catalog_now(site_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((site_dir / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"entries": []}


def _changed_paths(site_dir: Path) -> list[str]:
    out = _git(site_dir, "status", "--porcelain", "--untracked-files=all").stdout
    paths = []
    for line in out.splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def _commits_ahead(site_dir: Path, remote: str, branch: str) -> int:
    if _git(site_dir, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
        return 0  # unborn: nothing committed yet
    tracking = f"refs/remotes/{remote}/{branch}"
    has_tracking = (
        _git(site_dir, "rev-parse", "--verify", "--quiet", tracking, check=False).returncode == 0
    )
    span = f"{tracking}..HEAD" if has_tracking else "HEAD"
    return int(_git(site_dir, "rev-list", "--count", span).stdout.strip() or 0)


def _card(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("id", ""),
        "title": entry.get("title", ""),
        "lessonCount": entry.get("lessonCount"),
        "humanReviewed": entry.get("humanReviewed"),
    }


def site_deploy_status(site_dir: Path, *, branch: str, remote: str) -> dict[str, Any]:
    """Cheap and offline, for the Studio's *N changes not deployed*."""
    site_dir = Path(site_dir)
    if not is_site_worktree(site_dir, branch):
        return {"initialised": False, "pending": 0, "ahead": 0}
    return {
        "initialised": True,
        "pending": len(_changed_paths(site_dir)),
        "ahead": _commits_ahead(site_dir, remote, branch),
    }


def plan_deploy(
    site_dir: Path, *, branch: str = "gh-pages", remote: str = "origin"
) -> dict[str, Any]:
    """What ``deploy`` would put live, from ``catalog.json`` against the last commit."""
    site_dir = Path(site_dir)
    if not is_site_worktree(site_dir, branch):
        raise DeployError(
            "not_initialised",
            f"{site_dir} isn't a worktree of the {branch} branch. "
            "Run `syllabus-studio site init` first.",
        )

    changed = _changed_paths(site_dir)
    ahead = _commits_ahead(site_dir, remote, branch)
    if not changed and not ahead:
        raise DeployError(
            "nothing_to_deploy", "Nothing to deploy: the site matches what was pushed."
        )

    before = {e.get("id"): e for e in _catalog_at_head(site_dir).get("entries", []) if e.get("id")}
    after = {e.get("id"): e for e in _catalog_now(site_dir).get("entries", []) if e.get("id")}
    touched_bundles = {
        Path(p).name.removesuffix(".course.json")
        for p in changed
        if p.startswith(f"{COURSES_DIR}/")
    }

    added = [_card(after[i]) for i in after if i not in before]
    removed = [_card(before[i]) for i in before if i not in after]
    updated = [
        _card(after[i])
        for i in after
        if i in before and (after[i] != before[i] or i in touched_bundles)
    ]
    assets = [p for p in changed if p != "catalog.json" and not p.startswith(f"{COURSES_DIR}/")]

    remote_url = _remote_url(site_dir, remote)
    return {
        "siteDir": str(site_dir),
        "branch": branch,
        "remote": remote,
        "added": added,
        "updated": updated,
        "removed": removed,
        "assets": len(assets),
        "pendingFiles": len(changed),
        "unpushedCommits": ahead,
        "unreviewed": [c["title"] for c in added + updated if c["humanReviewed"] is not True],
        "url": pages_url(remote_url) if remote_url else None,
    }


def commit_message(plan: dict[str, Any]) -> str:
    parts = [
        f"{c['title']} ({kind})" for kind in ("added", "updated", "removed") for c in plan[kind]
    ]
    if parts:
        return "Publish: " + ", ".join(parts)
    return "Update the reader" if plan["assets"] else "Deploy the site"


def deploy(
    site_dir: Path,
    *,
    branch: str = "gh-pages",
    remote: str = "origin",
    message: str = "",
) -> dict[str, Any]:
    """Commit everything in the site worktree and push it. Never force-pushes.

    A rejected push leaves the commit in place: running ``deploy`` again after
    pulling in the site pushes it without committing twice.
    """
    site_dir = Path(site_dir)
    plan = plan_deploy(site_dir, branch=branch, remote=remote)

    if plan["pendingFiles"]:
        _git(site_dir, "add", "--all")
        _git(site_dir, "commit", "--quiet", "-m", message.strip() or commit_message(plan))
    sha = _git(site_dir, "rev-parse", "HEAD").stdout.strip()

    if not _remote_url(site_dir, remote):
        raise DeployError(
            "no_remote",
            f"Committed {sha[:7]} in {site_dir}, but there is no `{remote}` remote to push to.",
        )
    push = _git(site_dir, "push", "--set-upstream", remote, branch, check=False)
    if push.returncode != 0:
        raise DeployError(
            "push_rejected",
            f"Committed {sha[:7]} locally, but the push was rejected — {remote}/{branch} has "
            f"changes this site doesn't. Pull them in {site_dir} (`git -C {site_dir} pull "
            f"--rebase`), then deploy again. Nothing was force-pushed.\n{push.stderr.strip()}",
        )
    return {**plan, "commit": sha}

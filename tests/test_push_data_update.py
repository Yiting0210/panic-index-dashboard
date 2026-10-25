"""push_data_update against real git repositories in a temp directory.

A bare repo plays GitHub, a shallow clone plays the workflow checkout, and
a second clone merges "PRs" to main while the workflow runs.
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))

from push_data_update import PushError, git, push_data_commit  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def sh(cwd, *args):
    return git(*args, cwd=cwd).stdout.strip()


def commit(repo, path, text, message):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")
    sh(repo, "add", path)
    sh(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                       "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    sh(tmp_path, "clone", "-q", str(remote), str(seed))
    commit(seed, "data/market.csv", "date,close\n1,100\n", "A: seed")
    commit(seed, "app.py", "print('v1')\n", "A2: app")
    sh(seed, "push", "-q", "origin", "HEAD:main")
    # Shallow, like actions/checkout; file:// so --depth applies locally.
    workflow = tmp_path / "workflow"
    sh(tmp_path, "clone", "-q", "--depth", "1", remote.as_uri(), str(workflow))
    other = tmp_path / "other"
    sh(tmp_path, "clone", "-q", str(remote), str(other))
    return remote, workflow, other


def log(repo, ref="main"):
    return sh(repo, "log", "--format=%s", ref).splitlines()


def test_data_commit_is_rebased_onto_a_merge_made_during_the_run(repos):
    remote, workflow, other = repos
    commit(workflow, "data/market.csv", "date,close\n1,100\n2,101\n", "data: daily update")
    commit(other, "app.py", "print('v2')\n", "B: merged PR")
    sh(other, "push", "-q", "origin", "HEAD:main")

    assert push_data_commit(cwd=workflow) == "pushed"
    assert log(remote) == ["data: daily update", "B: merged PR", "A2: app", "A: seed"]


def test_plain_push_without_a_concurrent_merge(repos):
    remote, workflow, _ = repos
    commit(workflow, "data/market.csv", "date,close\n1,100\n2,101\n", "data: daily update")
    assert push_data_commit(cwd=workflow) == "pushed"
    assert log(remote)[0] == "data: daily update"


def test_conflict_on_the_data_file_fails_without_pushing(repos):
    remote, workflow, other = repos
    commit(workflow, "data/market.csv", "date,close\n1,100\n2,101\n", "data: daily update")
    commit(other, "data/market.csv", "date,close\n1,100\n2,999\n", "B: edits the data")
    sh(other, "push", "-q", "origin", "HEAD:main")
    before = sh(remote, "rev-parse", "main")

    with pytest.raises(PushError, match="conflict"):
        push_data_commit(cwd=workflow)
    assert sh(remote, "rev-parse", "main") == before
    # The rebase was aborted, so the checkout is not left mid-rebase.
    assert not (workflow / ".git" / "rebase-merge").exists()


def test_same_data_already_on_main_pushes_nothing(repos):
    remote, workflow, other = repos
    commit(workflow, "data/market.csv", "date,close\n1,100\n2,101\n", "data: daily update")
    commit(other, "data/market.csv", "date,close\n1,100\n2,101\n", "data: manual update")
    sh(other, "push", "-q", "origin", "HEAD:main")

    assert push_data_commit(cwd=workflow) == "up-to-date"
    assert log(remote)[0] == "data: manual update"


def test_retries_when_main_moves_between_fetch_and_push(repos):
    remote, workflow, other = repos
    commit(workflow, "data/market.csv", "date,close\n1,100\n2,101\n", "data: daily update")
    merges = iter(["B: first merge", "C: second merge"])

    def run(*args, **kwargs):
        # A PR merges right before each of the first two pushes.
        if args[0] == "push":
            message = next(merges, None)
            if message:
                commit(other, f"{message[0]}.py", "x\n", message)
                sh(other, "push", "-q", "origin", "HEAD:main")
        return git(*args, **kwargs)

    assert push_data_commit(cwd=workflow, run=run) == "pushed"
    assert log(remote)[:3] == ["data: daily update", "C: second merge", "B: first merge"]


def test_gives_up_after_the_attempt_limit(repos):
    _, workflow, other = repos
    commit(workflow, "data/market.csv", "date,close\n1,100\n2,101\n", "data: daily update")
    count = iter(range(100))

    def run(*args, **kwargs):
        if args[0] == "push":
            n = next(count)
            commit(other, f"pr{n}.py", "x\n", f"PR {n}")
            sh(other, "push", "-q", "origin", "HEAD:main")
        return git(*args, **kwargs)

    with pytest.raises(PushError, match="gave up after 2 attempts"):
        push_data_commit(cwd=workflow, attempts=2, run=run)


def test_other_push_errors_fail_at_once(repos):
    _, workflow, _ = repos
    commit(workflow, "data/market.csv", "date,close\n1,100\n2,101\n", "data: daily update")
    calls = []

    def run(*args, **kwargs):
        calls.append(args[0])
        if args[0] == "push":
            return subprocess.CompletedProcess(args, 128, "", "fatal: Authentication failed")
        return git(*args, **kwargs)

    with pytest.raises(PushError, match="Authentication failed"):
        push_data_commit(cwd=workflow, run=run)
    assert calls.count("push") == 1

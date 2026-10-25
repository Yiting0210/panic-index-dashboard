"""Push the daily data commit on top of the latest main, never by force.

The daily workflow checks out main, fetches data, runs the tests, and
commits; a PR merged meanwhile makes a plain `git push` fail as
non-fast-forward. This script fetches main, rebases the data commit onto
it, and pushes, retrying a few times if main moves again in between.

* A rebase conflict (another commit changed the same data files) aborts the
  rebase and fails loudly; nothing is force-pushed.
* If the rebased commit becomes empty (main already has the same data),
  nothing is pushed and the script succeeds.
* Any push error other than a rejected non-fast-forward fails at once.

Usage:
    python scripts/push_data_update.py [--remote origin] [--branch main] [--attempts 3]
"""
import argparse
import subprocess
import sys

REJECTED_MARKERS = ("[rejected]", "non-fast-forward", "fetch first")


class PushError(RuntimeError):
    pass


def git(*args, cwd=None, check=True):
    """Run git and return the completed process (text output)."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=False)
    if check and proc.returncode != 0:
        raise PushError(f"git {' '.join(args)} failed:\n{proc.stdout}{proc.stderr}")
    return proc


def push_data_commit(remote="origin", branch="main", attempts=3, cwd=None, run=git):
    """Rebase HEAD onto the latest remote branch and push it.

    Returns "pushed", or "up-to-date" when the remote already has the data.
    ``run`` is the git runner, replaceable in tests.
    """
    upstream = f"{remote}/{branch}"
    for attempt in range(1, attempts + 1):
        run("fetch", remote, f"+refs/heads/{branch}:refs/remotes/{upstream}", cwd=cwd)
        rebase = run("rebase", upstream, cwd=cwd, check=False)
        if rebase.returncode != 0:
            run("rebase", "--abort", cwd=cwd, check=False)
            raise PushError(
                f"Rebasing the data commit onto {upstream} hit a conflict; another commit "
                f"changed the same files. Nothing was pushed.\n{rebase.stdout}{rebase.stderr}"
            )
        head = run("rev-parse", "HEAD", cwd=cwd).stdout.strip()
        if head == run("rev-parse", upstream, cwd=cwd).stdout.strip():
            print(f"{upstream} already has this data; nothing to push.")
            return "up-to-date"
        push = run("push", remote, f"HEAD:refs/heads/{branch}", cwd=cwd, check=False)
        if push.returncode == 0:
            print(f"Pushed the data commit on top of {upstream} (attempt {attempt}).")
            return "pushed"
        if not any(marker in push.stderr for marker in REJECTED_MARKERS):
            raise PushError(f"git push failed:\n{push.stdout}{push.stderr}")
        print(f"{upstream} moved during the push (attempt {attempt}/{attempts}); retrying.")
    raise PushError(f"{upstream} kept moving; gave up after {attempts} attempts.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    try:
        push_data_commit(args.remote, args.branch, args.attempts)
    except PushError as exc:
        print(f"::error title=Daily data push::{exc}".replace("\n", "%0A"))
        sys.exit(1)


if __name__ == "__main__":
    main()

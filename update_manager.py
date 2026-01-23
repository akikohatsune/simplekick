import logging
import os
import subprocess
import sys


def _run(cmd: list[str], cwd: str, logger: logging.Logger) -> subprocess.CompletedProcess[str]:
    logger.info("Running update command: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _git_root(logger: logging.Logger) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning("Not a git repository; auto-update skipped.")
        return None
    return result.stdout.strip() or None


def _is_dirty(cwd: str, logger: logging.Logger) -> bool:
    result = _run(["git", "status", "--porcelain"], cwd, logger)
    if result.returncode != 0:
        logger.warning("Failed to check git status; auto-update skipped.")
        return True
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    tracked_changes = [line for line in lines if not line.startswith("?? ")]
    if tracked_changes:
        logger.warning(
            "Working tree has tracked changes; auto-update skipped. Changes: %s",
            " | ".join(tracked_changes),
        )
        return True
    if lines:
        logger.info("Untracked files present; proceeding with auto-update.")
    return False


def _checkout_tag(tag: str, cwd: str, logger: logging.Logger) -> bool:
    fetch = _run(["git", "fetch", "--tags", "origin"], cwd, logger)
    if fetch.returncode != 0:
        logger.warning("Failed to fetch tags: %s", fetch.stderr.strip())
        return False
    checkout = _run(["git", "checkout", tag], cwd, logger)
    if checkout.returncode != 0:
        logger.warning("Failed to checkout %s: %s", tag, checkout.stderr.strip())
        return False
    return True


def _install_requirements(cwd: str, logger: logging.Logger) -> bool:
    requirements_path = os.path.join(cwd, "requirements.txt")
    if not os.path.exists(requirements_path):
        logger.info("No requirements.txt found; skipping install.")
        return True
    result = _run([sys.executable, "-m", "pip", "install", "-r", requirements_path], cwd, logger)
    if result.returncode != 0:
        logger.warning("Failed to install requirements: %s", result.stderr.strip())
        return False
    return True


def perform_update(latest_tag: str, logger: logging.Logger) -> bool:
    repo_root = _git_root(logger)
    if not repo_root:
        return False
    if _is_dirty(repo_root, logger):
        logger.warning("Working tree is dirty; auto-update skipped.")
        return False
    if not _checkout_tag(latest_tag, repo_root, logger):
        return False
    if not _install_requirements(repo_root, logger):
        return False
    return True

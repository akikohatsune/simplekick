import json
import logging
import urllib.error
import urllib.request


def _parse_repo(repo: str) -> tuple[str, str] | None:
    if not repo:
        return None
    if "/" not in repo:
        return None
    owner, name = repo.strip().split("/", 1)
    if not owner or not name:
        return None
    return owner, name


def _normalize_version(version: str) -> str:
    version = (version or "").strip()
    if version.startswith("v"):
        version = version[1:]
    return version


def _request_json(url: str, timeout: int = 5) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "simplekick-update-check",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _fetch_latest_release(owner: str, repo: str) -> tuple[str, str] | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    data = _request_json(url)
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name")
    html_url = data.get("html_url", "")
    if not tag:
        return None
    return tag, html_url


def check_for_updates(current_version: str, repo: str, logger: logging.Logger) -> None:
    parsed = _parse_repo(repo)
    if not parsed:
        logger.warning("Invalid GITHUB_REPO format. Expected owner/repo.")
        return

    owner, name = parsed
    try:
        latest = _fetch_latest_release(owner, name)
        if not latest:
            logger.info("No releases found for %s/%s.", owner, name)
            return
    except Exception:
        logger.exception("Failed to check updates from GitHub.")
        return

    latest_tag, url = latest
    current_norm = _normalize_version(current_version)
    latest_norm = _normalize_version(latest_tag)

    if latest_norm and current_norm and latest_norm != current_norm:
        logger.info(
            "Update available: %s -> %s (%s)",
            current_version,
            latest_tag,
            url or "no url",
        )
    else:
        logger.info("Up to date: %s", current_version)

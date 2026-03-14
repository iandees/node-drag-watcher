"""Website URL cleanup checker.

Detects website/url tags that need normalization:
- Add scheme if missing
- Lowercase domain
- Strip tracking parameters (utm_*, fbclid, gclid, etc.)
- Upgrade HTTP to HTTPS if site supports it
"""

import logging
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import requests

from checkers import Action, Issue, BaseChecker

log = logging.getLogger(__name__)

WEBSITE_TAG_PATTERN = re.compile(
    r'^(website|url|contact:website)(:.+)?$'
)

# Query params to strip
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igsh", "mc_cid", "mc_eid", "ref",
    "y_source", "srsltid",
}

# utm_source values that indicate URL was copied from Google
GOOGLE_UTM_SOURCES = {"gmb", "google", "google_maps", "google_my_business", "yxt-goog"}


def _normalize_url(raw: str) -> str | None:
    """Normalize a URL structurally (no network).

    Returns normalized URL or None if not a valid website URL.
    """
    stripped = raw.strip()

    # Skip non-website schemes
    if stripped.startswith(("mailto:", "tel:", "ftp:")):
        return None

    # Fix doubled schemes (e.g. "http://Https://example.com" → "https://example.com")
    doubled = re.match(r'^https?://(https?://)', stripped, re.IGNORECASE)
    if doubled:
        inner = stripped[doubled.start(1):]
        # Lowercase the scheme portion (e.g. "Https://" → "https://")
        stripped = inner[:inner.index("://") + 3].lower() + inner[inner.index("://") + 3:]

    # Fix truncated schemes (e.g. "ttps://", "ttp://", "htp://")
    truncated = re.match(r'^h?t?t?ps?://', stripped)
    if truncated and not stripped.startswith(("http://", "https://")):
        stripped = "https://" + stripped[truncated.end():]

    # Add scheme if missing
    if not stripped.startswith(("http://", "https://")):
        stripped = "https://" + stripped

    parsed = urlparse(stripped)

    # Lowercase domain
    netloc = parsed.netloc.lower()

    # Strip tracking params
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v for k, v in params.items()
            if k not in TRACKING_PARAMS and not k.startswith("utm_")
        }
        query = urlencode(filtered, doseq=True) if filtered else ""
    else:
        query = ""

    # Strip trailing slash on bare domain
    path = parsed.path
    if path == "/":
        path = ""

    result = urlunparse((parsed.scheme, netloc, path, parsed.params, query, ""))
    return result


def _try_https_upgrade(url: str) -> str:
    """Try upgrading HTTP to HTTPS. Returns the best URL."""
    if not url.startswith("http://"):
        return url

    https_url = "https://" + url[7:]
    try:
        resp = requests.head(https_url, timeout=5, allow_redirects=True,
                             headers={"User-Agent": "node-drag-watcher/0.1"})
        if resp.status_code < 400:
            # Check if redirected to same domain
            final_parsed = urlparse(resp.url)
            original_parsed = urlparse(https_url)
            orig_domain = original_parsed.netloc.lower().lstrip("www.")
            final_domain = final_parsed.netloc.lower().lstrip("www.")
            if orig_domain == final_domain:
                return resp.url
            # Cross-domain redirect — keep original with HTTPS
            return https_url
        return url
    except Exception:
        return url


def _is_trivial_url_change(old: str, new: str) -> bool:
    """Return True if the only difference is a trailing slash."""
    return old.rstrip("/") == new.rstrip("/")


class WebsiteChecker(BaseChecker):
    """Detect website/url tags that need cleanup."""

    def check(self, action: Action) -> list[Issue]:
        if action.action_type == "delete":
            return []

        issues = []

        for tag_key, tag_value in action.tags_new.items():
            if not WEBSITE_TAG_PATTERN.match(tag_key):
                continue

            # Check for Google-copied URL before normalization strips params
            extra = {}
            parsed_raw = urlparse(tag_value)
            if parsed_raw.query:
                raw_params = parse_qs(parsed_raw.query)
                utm_sources = {v.lower() for vals in raw_params.get("utm_source", []) for v in vals.split(",")}
                if utm_sources & GOOGLE_UTM_SOURCES:
                    extra["google_copy"] = True

            normalized = _normalize_url(tag_value)
            if normalized is None:
                continue

            # Try HTTPS upgrade
            final_url = _try_https_upgrade(normalized)

            if final_url == tag_value:
                continue

            # Skip trivial changes (e.g. trailing slash only) unless
            # we're upgrading from http to https
            is_https_upgrade = tag_value.startswith("http://") and final_url.startswith("https://")
            if not is_https_upgrade and _is_trivial_url_change(tag_value, final_url):
                continue

            issues.append(Issue(
                element_type=action.element_type,
                element_id=action.element_id,
                element_version=action.version,
                changeset=action.changeset,
                user=action.user,
                check_name="website_cleanup",
                summary=f"{tag_key}: {tag_value} → {final_url}",
                tags_before={tag_key: tag_value},
                tags_after={tag_key: final_url},
                extra=extra,
            ))

        return issues

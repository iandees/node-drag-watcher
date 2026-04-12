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

# Redirect/tracking wrapper URL patterns.
# Each entry is (domain regex, query param holding the real URL).
REDIRECT_UNWRAPPERS = [
    (re.compile(r'^https?://(?:www\.)?google\.\w+(?:\.\w+)?/url\b', re.IGNORECASE), "url"),
    (re.compile(r'^https?://l\.facebook\.com/l\.php\b', re.IGNORECASE), "u"),
    (re.compile(r'^https?://(?:out|away)\.vk\.com/away\.php\b', re.IGNORECASE), "to"),
    (re.compile(r'^https?://slack-redir\.net/link\b', re.IGNORECASE), "url"),
    (re.compile(r'^https?://(?:www\.)?tripadvisor\.\w+(?:\.\w+)?/ExternalLinkInterstitial\b', re.IGNORECASE), "url"),
    (re.compile(r'^https?://(?:www\.)?youtube\.com/redirect\b', re.IGNORECASE), "q"),
]

# Known URL shortener domains. URLs on these domains are expanded via
# HEAD request to get the real destination.
URL_SHORTENER_DOMAINS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "is.gd",
    "buff.ly",
    "rb.gy",
    "shorturl.at",
    "cutt.ly",
    "t.ly",
    "lnkd.in",
    "youtu.be",
    "amzn.to",
    "fb.me",
    "tiny.cc",
    "acortar.link",
}

# Query params to strip
TRACKING_PARAMS = {
    # Google Ads / Analytics
    "gclid", "gclsrc", "gad_source", "gad_campaignid",
    "gbraid", "wbraid", "dclid",
    "_ga", "_gac", "_gl", "_gid",
    # UTM (catch-all for utm_* is in the filter below, but list common ones here too)
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "utm_creative_format", "utm_marketing_tactic",
    # Facebook / Meta
    "fbclid", "fb_action_ids", "fb_action_types", "fb_source", "fb_ref",
    # Microsoft / Bing
    "msclkid",
    # Twitter / X
    "twclid",
    # TikTok
    "ttclid",
    # Instagram
    "igsh", "igshid",
    # LinkedIn
    "li_fat_id",
    # Pinterest
    "epik",
    # Snapchat
    "sclid",
    # Adobe / Omniture
    "s_cid", "s_kwcid",
    # HubSpot
    "hsa_cam", "hsa_grp", "hsa_mt", "hsa_src", "hsa_ad", "hsa_acc", "hsa_net", "hsa_kw",
    "_hsenc", "_hsmi", "__hstc", "__hsfp", "__hssc",
    # Mailchimp
    "mc_cid", "mc_eid",
    # Marketo
    "mkt_tok",
    # Klaviyo
    "_kx",
    # Drip
    "__s",
    # Yandex
    "yclid", "ymclid", "_openstat",
    # Yahoo / Verizon
    "guccounter", "guce_referrer", "guce_referrer_sig",
    # YouTube / Spotify share tracking
    "si", "feature",
    # Matomo / Piwik
    "mtm_source", "mtm_medium", "mtm_campaign", "mtm_keyword", "mtm_content",
    "mtm_cid", "mtm_group", "mtm_placement",
    "pk_source", "pk_medium", "pk_campaign", "pk_keyword", "pk_content",
    # Misc
    "ref", "y_source", "srsltid", "otppartnerid", "campaignid",
    "vero_id", "wickedid",
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

    # Unwrap redirect/tracking wrappers (e.g. Google, Facebook)
    for pattern, param_key in REDIRECT_UNWRAPPERS:
        if pattern.match(stripped):
            wrapper_params = parse_qs(urlparse(stripped).query)
            if param_key in wrapper_params:
                stripped = wrapper_params[param_key][0]
            break

    # Strip junk text before an embedded valid URL (e.g. "h https://example.com")
    embedded = re.search(r'https?://', stripped, re.IGNORECASE)
    if embedded and embedded.start() > 0:
        stripped = stripped[embedded.start():]

    # Fix malformed schemes: doubled, truncated, wrong separators, extra chars, etc.
    # Match any prefix that looks like a mangled http(s) scheme and extract the rest.
    scheme_fix = re.match(
        r'^((?:h*t+p+s*[;:]?[/\\]*){1,2}'  # one or two scheme-like runs (h optional for truncated)
        r'(?:[;:][/\\]*|[/\\]+))'           # separator: colon/semicolon + slashes, or just slashes
        r'[\s]*',                            # optional whitespace after separator
        stripped, re.IGNORECASE,
    )
    if scheme_fix:
        rest = stripped[scheme_fix.end():]
        prefix_lower = scheme_fix.group(1).lower()
        # Determine http vs https from the *last* scheme-like word in the prefix
        # (handles doubled schemes like "http://https://" where inner scheme wins)
        # Truncated words (missing letters) default to https:// since the typo
        # likely lost letters rather than intentionally typing http://
        scheme_words = re.findall(r'h*t+p+s*', prefix_lower)
        last_word = scheme_words[-1] if scheme_words else ""
        is_full_http = last_word in ("http", "hhttp")
        scheme = "http://" if is_full_http else "https://"
        stripped = scheme + rest

    # Strip leading :// or // (protocol-relative or malformed prefix)
    leading = re.match(r'^:?/+', stripped)
    if leading:
        stripped = stripped[leading.end():]

    # Add scheme if missing
    if not stripped.startswith(("http://", "https://")):
        stripped = "https://" + stripped

    parsed = urlparse(stripped)

    # Lowercase domain; strip stray leading dots (e.g. "http:.example.com")
    netloc = parsed.netloc.lower().lstrip(".")

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

    result = urlunparse((parsed.scheme, netloc, path, parsed.params, query, parsed.fragment))
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


def _try_expand_shortener(url: str) -> str:
    """If the URL is on a known shortener domain, follow redirects to get the real URL."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().lstrip("www.")
    if domain not in URL_SHORTENER_DOMAINS:
        return url
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True,
                             headers={"User-Agent": "node-drag-watcher/0.1"})
        if resp.status_code < 400 and resp.url != url:
            return resp.url
    except Exception:
        pass
    return url


def _is_trivial_url_change(old: str, new: str) -> bool:
    """Return True if the only difference is a trailing slash (including before query params)."""
    if old.rstrip("/") == new.rstrip("/"):
        return True
    # Also catch /?query → ?query (slash before query string)
    o = urlparse(old)
    n = urlparse(new)
    return (
        o.scheme == n.scheme
        and o.netloc == n.netloc
        and o.path.rstrip("/") == n.path.rstrip("/")
        and o.query == n.query
        and o.fragment == n.fragment
    )


class WebsiteChecker(BaseChecker):
    """Detect website/url tags that need cleanup."""

    def check(self, action: Action) -> list[Issue]:
        if action.action_type == "delete":
            return []

        issues = []

        for tag_key, tag_value in action.tags_new.items():
            if not WEBSITE_TAG_PATTERN.match(tag_key):
                continue

            # Skip tags that weren't changed in this edit
            if action.tags_old.get(tag_key) == tag_value:
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

            # Expand URL shorteners (network call)
            expanded = _try_expand_shortener(normalized)
            if expanded != normalized:
                # Re-normalize the expanded URL (strip tracking params, etc.)
                expanded = _normalize_url(expanded) or expanded
                normalized = expanded

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

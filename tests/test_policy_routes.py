"""RM-P8 policy/support route tests — dependency-free, standard library only.

Source root: Path(__file__).resolve().parents[1], overridable via the
RM_P8_LANE_ROOT environment variable so an operator can run mutations against a
disposable fixture copy. Serves only that root on 127.0.0.1 with an ephemeral
port through an owned ThreadingHTTPServer, always shut down/joined in cleanup.
Loopback-only; no live host is ever contacted.

Nine named tests. M1 = test_provisional_dom_presence_and_attributes +
test_policy_css_has_no_hidden_content (DOM-presence/attribute and static CSS
guard proof only, never rendered-visibility proof). M2 =
test_exact_case_paths_and_added_file_inventory.

Each M1/M2 test also runs its own mutation controls against disposable fixture
copies: the controls assert that the guard actually rejects the mutation, so a
green suite cannot be produced by a guard that never fires.

Guard hardening (repaired after proven counterexamples):
  * HttpLane uses a per-test opener with NO auto-redirect following and an empty
    proxy map, so get() manually enforces its bounded hops and validates each
    target as same-origin BEFORE it is fetched; an external Location is rejected
    pre-fetch (proved in-test with a lane-owned controlled handler).
  * A trailing !important is normalized away before any hiding decision.
  * Any clip/clip-path that is not a valid skip-link-only rule is rejected,
    whatever the function spelling.
  * A rule is exempt only when every selector's SUBJECT is the skip link; a mere
    :has()/sibling reference to `.skip-link` is not enough.
  * @import and external font url() are rejected; unsupported at-rule syntax
    raises instead of swallowing the following rule.
"""

import hashlib
import http.server
import os
import re
import shutil
import socketserver
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


def _resolve_base():
    override = os.environ.get("RM_P8_LANE_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[1]


BASE = _resolve_base()

BASE_COMMIT = "57fe9402933f5cdeffe56393f1f94cd75f44e825"
BASE_INDEX_SHA256 = "c7c44935a77ed29fe0b6bf894cb7fdc5963bfb6a9b60d4085726f8413a890105"
BASE_STYLES_SHA256 = "295b360eed08fe227a4c727cb04cc19d9d2c92722848db3e0ec1cd061a3db5fa"

# Pinned base Git blob SHA1s (operator-supplied base tree). Preservation is
# verified by recomputing these from candidate bytes, never by existence alone.
PINNED_BLOB_SHA1 = {
    "CNAME": "d989092775e75810a49fa2be74264ece95abcd75",
    "assets/hero-base.png": "2dda4969d1bee28b355beecf14a7275b38c013f3",
    "assets/icon.svg": "188fe8af9904da08a0f1d8a82ebe51c33ab9551c",
    "assets/logo.svg": "640d2d1535975be40b56ffead2f64a0dda9b9836",
}

SLUGS = ("privacy", "terms", "support")

TITLES = {
    "privacy": "Privacy \u2014 provisional | CardBarter",
    "terms": "Terms \u2014 provisional | CardBarter",
    "support": "Support \u2014 provisional | CardBarter",
}

H1S = {"privacy": "Privacy", "terms": "Terms", "support": "Support"}

NOTICE = "Provisional preview \u2014 not approved for publication."
VERSION = (
    "Scaffold version: RM-P8-2026-10-14-v1. "
    "This is a preview version, not a policy effective date."
)
BODY = {
    "privacy": (
        "This page is a placeholder for CardBarter\u2019s privacy policy. "
        "Final policy text has not been approved for publication."
    ),
    "terms": (
        "This page is a placeholder for CardBarter\u2019s terms. "
        "Final terms have not been approved for publication."
    ),
    "support": (
        "This page is a placeholder for CardBarter support information. "
        "No contact channel is configured on this preview page."
    ),
}

FOOTER_EXPECTED = {"Contact": "/support", "Privacy": "/privacy", "Terms": "/terms"}

EXPECTED_ADDED_PRODUCTION = {
    "privacy/index.html",
    "terms/index.html",
    "support/index.html",
    "policy.css",
}
EXPECTED_ADDED_ALL = EXPECTED_ADDED_PRODUCTION | {"tests/test_policy_routes.py"}

# Reverse of the only permitted homepage change, to reproduce base bytes.
FOOTER_SUBSTITUTIONS = (
    (
        '<a href="/support" class="footer-link">Contact</a>',
        '<a href="#" class="footer-link">Contact</a>',
    ),
    (
        '<a href="/privacy" class="footer-link">Privacy</a>',
        '<a href="#" class="footer-link">Privacy</a>',
    ),
    (
        '<a href="/terms" class="footer-link">Terms</a>',
        '<a href="#" class="footer-link">Terms</a>',
    ),
)

FORBIDDEN_EFFECTIVE_PHRASES = (
    "effective as of",
    "last updated",
    "effective on",
    "in effect as of",
    "becomes effective",
    "become effective",
    "took effect",
)

# ---------------------------------------------------------------------------
# Disposable-fixture support (helpers + root argument, for operator mutations).
# ---------------------------------------------------------------------------

FIXTURE_COPY_FILES = ("index.html", "styles.css", "CNAME", "policy.css")
FIXTURE_COPY_DIRS = ("assets", "privacy", "terms", "support")

# Read-only asset fixtures are made hardlinks (no write travels through them),
# so the 7.6MB hero is not copied four times. Disposable text files stay copies.
ASSET_FIXTURES = ("assets/hero-base.png", "assets/icon.svg", "assets/logo.svg")


def make_fixture_root(dest, source=None):
    """Copy the lane source (production files + test file) into a disposable
    root. The operator mutates that copy and runs its own tests/ file, or sets
    RM_P8_LANE_ROOT to it.

    Text files are copied; the three read-only page assets are hardlinked, with a
    copy fallback if the filesystem refuses the link."""
    source = Path(source or BASE)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_COPY_FILES:
        src = source / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    for name in FIXTURE_COPY_DIRS:
        src = source / name
        if src.is_dir():
            shutil.copytree(src, dest / name)
    for rel in ASSET_FIXTURES:
        src = source / rel
        target = dest / rel
        if not src.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        try:
            os.link(src, target)
        except OSError:
            shutil.copy2(src, target)
    (dest / "tests").mkdir(exist_ok=True)
    tsrc = source / "tests" / "test_policy_routes.py"
    if tsrc.is_file():
        shutil.copy2(tsrc, dest / "tests" / "test_policy_routes.py")
    return dest


def fixture_pages(root):
    """Case-exact fixture page paths, for operator mutation."""
    return [Path(root) / slug / "index.html" for slug in SLUGS]


def replace_fixture_file(path, text):
    """External controller helper: always unlink then replace a disposable text
    file before mutation, so a hardlinked asset in the lane source is preserved."""
    path = Path(path)
    if path.exists():
        path.unlink()
    path.write_text(text, encoding="utf-8")
    return path


def blob_sha1(raw):
    """Git blob SHA1 of exact bytes: sha1(b'blob ' + str(len(raw)) + b'\\0' + raw)."""
    header = b"blob " + str(len(raw)).encode("ascii") + b"\0"
    return hashlib.sha1(header + raw).hexdigest()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class _Node:
    __slots__ = ("tag", "attrs", "parent", "children")

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children = []

    def get(self, name):
        return self.attrs.get(name)

    def classes(self):
        return (self.attrs.get("class") or "").split()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class DomParser(HTMLParser):
    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
    RAW_TEXT = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, None)
        self.stack = [self.root]
        self.nodes = []
        self._skip_depth = 0

    @property
    def current(self):
        return self.stack[-1]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        self.nodes.append(node)
        if tag in self.RAW_TEXT:
            self._skip_depth += 1
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        self.nodes.append(node)

    def handle_endtag(self, tag):
        if tag in self.RAW_TEXT and self._skip_depth:
            self._skip_depth -= 1
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.current.children.append(data)

    def text(self, node):
        parts = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                parts.append(self.text(child))
        return "".join(parts)

    def by_tag(self, tag):
        return [n for n in self.nodes if n.tag == tag]

    def all_elements(self):
        return list(self.nodes)

    def document_text(self):
        return self.text(self.root)


def parse_dom(markup):
    parser = DomParser()
    parser.feed(markup)
    parser.close()
    return parser


def normalize_ws(text):
    return re.sub(r"\s+", " ", text).strip()


def walk(node):
    yield node
    for child in node.children:
        if not isinstance(child, str):
            yield from walk(child)


def find_deepest_exact(dom, needle):
    target = normalize_ws(needle)
    best = None
    for node in dom.all_elements():
        if normalize_ws(dom.text(node)) == target:
            best = node
    return best


def find_deepest_containing(dom, needle):
    target = normalize_ws(needle)
    best = None
    for node in dom.all_elements():
        if target and target in normalize_ws(dom.text(node)):
            best = node
    return best


def find_by_class(dom, cls):
    return [n for n in dom.all_elements() if cls in n.classes()]


def has_ancestor(node, ancestor):
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


# ---------------------------------------------------------------------------
# Bounded HTTP client (loopback only, redirects bounded, target re-checked)
# ---------------------------------------------------------------------------


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib's default HTTPRedirectHandler silently follows a 3xx before get()
    can validate the next target. Returning None here leaves the redirect to the
    lane's bounded hop loop, which re-checks origin before the next fetch."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _FakeResponse:
    """Minimal response object for a lane-owned controlled first response."""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self._body = body

    def read(self, *args):
        return self._body

    def geturl(self):
        return "http://127.0.0.1/fake"

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _ControlledLaneHandler:
    """Lane-owned handler that returns one controlled first response (with a
    Location supplied by the test) and records every fetch, so the target guard
    can be proven to reject an external Location BEFORE any fetch."""

    def __init__(self, response):
        self.response = response
        self.fetched = []

    def open(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.fetched.append(url)
        if len(self.fetched) > 1:
            raise AssertionError("unexpected extra fetch: %r" % self.fetched)
        return self.response


class HttpLane:
    """Owns a loopback-only ephemeral server rooted at `root`."""

    REDIRECTS = (301, 302, 303, 307, 308)

    def __init__(self, root, timeout=5.0, max_redirects=5):
        self.root = Path(root)
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.server = None
        self.thread = None
        self.port = None
        # Per-test opener: an empty proxy map (no configured proxy is used) plus a
        # no-auto-redirect handler, so get() manually enforces every hop.
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def __enter__(self):
        def handler(*args, **kwargs):
            return _Handler(*args, directory=str(self.root), **kwargs)

        self.server = _Server(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            if self.thread is not None:
                self.thread.join(timeout=5)
            self.server = None

    def url(self, path):
        if not path.startswith("/"):
            path = "/" + path
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def assert_same_origin(self, parsed):
        """Reject any resolved HTTP target outside this owned server, before any
        fetch of it."""
        if parsed.scheme not in ("http", ""):
            raise AssertionError(
                "resolved target outside this server (non-http) rejected: %r"
                % (parsed.geturl(),)
            )
        if parsed.scheme and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise AssertionError(
                "resolved HTTP target outside this server rejected: %r" % (parsed.geturl(),)
            )
        if parsed.scheme and parsed.port != self.port:
            raise AssertionError(
                "resolved HTTP target outside this server rejected (port): %r"
                % (parsed.geturl(),)
            )

    def _fetch_once(self, url, opener):
        """Exactly one hop. `opener` never auto-follows a redirect, so a 3xx is
        returned here for get() to validate before the next fetch."""
        self.assert_same_origin(urlparse(url))
        request = urllib.request.Request(url, headers={"User-Agent": "rm-p8-tests"})
        try:
            response = opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as err:
            status, headers = err.code, err.headers
            body = err.read()
            err.close()
            return status, headers, body, url
        except urllib.error.URLError as err:
            raise AssertionError("request failed for %s: %s" % (url, err))
        with response:
            return response.status, response.headers, response.read(), response.geturl()

    def get(self, path, opener=None):
        """Return (status, headers, body_bytes, final_url, chain), with redirects
        bounded by max_redirects and each hop's target validated as same-origin
        BEFORE it is fetched. An external Location is therefore never fetched."""
        url = self.url(path)
        opener = opener or self.opener
        chain = []
        for _ in range(self.max_redirects + 1):
            status, headers, body, final_url = self._fetch_once(url, opener)
            if status in self.REDIRECTS:
                location = headers.get("Location")
                if not location:
                    raise AssertionError("redirect without Location for %s" % url)
                chain.append((status, url, location))
                next_url = urljoin(url, location)
                # Enforce the hop bound here, before the next target is fetched.
                self.assert_same_origin(urlparse(next_url))
                url = next_url
                continue
            return status, headers, body, final_url, chain
        raise AssertionError(
            "redirect bound (%d) exceeded for %s (chain=%r)" % (self.max_redirects, path, chain)
        )


def content_type(headers):
    return (headers.get("Content-Type") or "").lower()


# ---------------------------------------------------------------------------
# Static CSS guard (screen + nested media/print rules)
# ---------------------------------------------------------------------------

HIDING_PROPS = {
    "display", "visibility", "opacity", "font-size", "clip", "clip-path",
    "position", "inset", "top", "left", "right", "bottom", "transform",
    "text-indent", "color", "width", "height", "max-height", "overflow",
}

VALUE_INDIRECTION = re.compile(r"var\s*\(|calc\s*\(|attr\s*\(|env\s*\(", re.I)

_TRANSPARENT_COLORS = {
    "transparent", "rgba(0,0,0,0)", "rgba(0, 0, 0, 0)",
    "hsla(0,0%,0%,0)", "hsla(0, 0%, 0%, 0)",
}

# A trailing !important is normalized away before any hiding decision.
_IMPORTANT_SUFFIX = re.compile(r"!\s*important\s*$", re.I)

SKIP_LINK_CLASS = re.compile(r"\.skip-link(?![\w-])")

# Strict skip-selector grammar for this bounded stylesheet: a rule is exempt only
# when EVERY selector's actual SUBJECT is the skip link, scoped under .policy-page.
_SKIP_SUBJECT = re.compile(
    r"^\.policy-page(?:[ \t]*>\s*|[ \t]+)%s(?:[.:][\w-]+(?:\([^()]*\))?)*$"
    % SKIP_LINK_CLASS.pattern
)

# Bounded at-rule handling. Only these block at-rules may appear.
ALLOWED_AT_RULES = ("media", "supports", "layer", "print")
_AT_RULE_PRELUDE = re.compile(r"^@[a-z][-a-z]*\s*(?:\([^()]*\)|[^(){}]*)?$", re.I)
_IMPORT_RULE = re.compile(r"@import\b", re.I)
_EXTERNAL_URL = re.compile(r"url\s*\(\s*['\"]?(?:https?:)?//", re.I)

# A compound is a sequence of simple selectors: type/universal, class, id,
# attribute and pseudo, in any order (e.g. `a[aria-current="page"]`).
_SIMPLE_SELECTOR = (
    r"(?:[a-z][\w-]*|\*|#[-\w]+|\.[-\w]+|::?[-\w]+(?:\([^()]*\))?|\[[^\]]*\])"
)
_SELECTOR_COMPOUND = re.compile(r"^%s+$" % _SIMPLE_SELECTOR)


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def normalize_value(value):
    """Strip a trailing !important, normalize whitespace, and lower-case so a
    suffixed value can never slip past a hiding decision."""
    text = _IMPORTANT_SUFFIX.sub("", value.strip()).strip()
    return re.sub(r"\s+", " ", text).lower()


def css_rules(css):
    """Yield (selector_list_raw, body_raw) for every rule, recursively flattened
    out of media/supports/layer/print at-rules, so print/media nesting cannot
    bypass the guard. @import and external font/asset url() are rejected; an
    unsupported at-rule raises rather than swallowing the following rule."""
    text = strip_comments(css)
    if _IMPORT_RULE.search(text) or _EXTERNAL_URL.search(text):
        raise AssertionError("external @import/font/asset reference rejected")
    rules = []

    def walk_block(chunk):
        i = 0
        while i < len(chunk):
            brace = chunk.find("{", i)
            if brace == -1:
                tail = chunk[i:].strip()
                if tail:
                    raise AssertionError("unsupported trailing CSS syntax: %r" % tail)
                return
            prelude = chunk[i:brace].strip()
            depth = 1
            j = brace + 1
            while j < len(chunk) and depth:
                if chunk[j] == "{":
                    depth += 1
                elif chunk[j] == "}":
                    depth -= 1
                j += 1
            body = chunk[brace + 1:j - 1]
            if prelude.startswith("@"):
                if "\\" in prelude or not _AT_RULE_PRELUDE.match(prelude):
                    raise AssertionError("unsupported at-rule syntax: %r" % prelude)
                name = prelude[1:].split("(", 1)[0].strip().split()[0].lower()
                if name not in ALLOWED_AT_RULES:
                    raise AssertionError("unsupported at-rule: %r" % prelude)
                walk_block(body)
            elif prelude:
                if "\\" in prelude:
                    raise AssertionError("unsupported/escaped selector syntax: %r" % prelude)
                rules.append((prelude, body))
            i = j

    walk_block(text)
    return rules


def split_declarations(body):
    """Yield (property, value) pairs; reject unsupported/escaped declaration
    syntax inside the guarded stylesheet rather than silently passing it."""
    for decl in body.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        if "\\" in decl:
            raise AssertionError("unsupported/escaped declaration syntax: %r" % decl)
        if ":" not in decl:
            raise AssertionError("malformed declaration: %r" % decl)
        prop, value = decl.split(":", 1)
        prop = prop.strip().lower()
        if not prop or not re.fullmatch(r"[-a-z]+", prop):
            raise AssertionError("unsupported declaration property: %r" % prop)
        yield prop, value.strip()


def selector_list(raw):
    return [s.strip() for s in raw.split(",") if s.strip()]


def normalize_selector(sel):
    s = re.sub(r"\s+", " ", sel).strip().lower()
    s = re.sub(r"\s*([>+~])\s*", r"\1", s)
    return s


def skip_subject_only(raw):
    """True only when every selector's actual SUBJECT is the skip link.

    `.policy-page .skip-link`            -> exempt (subject is the skip link)
    `.policy-page > .skip-link`          -> exempt
    `.policy-page .policy-notice:has(+ .skip-link)` -> NOT exempt
    `.policy-page .policy-version`       -> NOT exempt
    `.skip-link` (bare, unscoped)        -> NOT exempt
    """
    parts = selector_list(raw)
    return bool(parts) and all(_SKIP_SUBJECT.match(normalize_selector(p)) for p in parts)


def references_skip_link(raw):
    """True when any selector in the list mentions the skip-link class at all,
    whether as the subject or only inside :has()/a combinator (e.g. `.skip-link::before`)."""
    return any(SKIP_LINK_CLASS.search(normalize_selector(p)) for p in selector_list(raw))


def numeric_tokens(value):
    for match in re.finditer(r"(-?\d*\.?\d+)([a-z%]*)", value, re.I):
        yield float(match.group(1)), match.group(2).lower()


def guard_css(css, source="policy.css"):
    """Raise AssertionError for any hiding/off-screen declaration outside a rule
    whose every selector's subject is the skip link, in screen OR nested
    print/media rules. Returns True when the stylesheet is clean."""
    for selector_raw, body in css_rules(css):
        exempt = skip_subject_only(selector_raw)
        mentions_skip = references_skip_link(selector_raw)
        for prop, value in split_declarations(body):
            # No content may be generated off the skip-link class at all, whether
            # the skip link is the exempt subject or merely referenced.
            if prop == "content" and mentions_skip:
                raise AssertionError(
                    "%s: content declared on a skip-link selector (%s)" % (source, selector_raw)
                )
            # Any clip/clip-path is rejected unless the rule is a valid skip-only
            # exemption AND the value is non-clipping (auto/none); every other
            # spelling (polygon/rect/inset/clamp/escaped) is rejected outright.
            if prop in ("clip", "clip-path"):
                if exempt and normalize_value(value) in ("auto", "none"):
                    continue
                raise AssertionError(
                    "%s: %s:%s not a valid skip-link-only rule (%s)"
                    % (source, prop, value, selector_raw)
                )
            if prop not in HIDING_PROPS:
                continue
            lv = normalize_value(value)
            if VALUE_INDIRECTION.search(value):
                raise AssertionError(
                    "%s: value indirection not allowed in hiding property %s: %s (%s)"
                    % (source, prop, value, selector_raw)
                )
            if exempt:
                continue
            if prop == "display" and lv.replace(" ", "") == "none":
                raise AssertionError("%s: display:none outside skip-link rule (%s)" % (source, selector_raw))
            if prop == "visibility" and lv in ("hidden", "collapse"):
                raise AssertionError("%s: visibility:%s outside skip-link rule (%s)" % (source, lv, selector_raw))
            if prop == "opacity":
                for num, _unit in numeric_tokens(value):
                    if abs(num) < 1e-9:
                        raise AssertionError("%s: opacity:0 outside skip-link rule (%s)" % (source, selector_raw))
            if prop == "font-size":
                for num, unit in numeric_tokens(value):
                    if abs(num) < 1e-9:
                        raise AssertionError("%s: font-size:0 outside skip-link rule (%s)" % (source, selector_raw))
                    if unit in ("px", "rem", "em") and num < 0:
                        raise AssertionError("%s: negative font-size (%s)" % (source, selector_raw))
            if prop == "color" and lv.replace(" ", "") in _TRANSPARENT_COLORS:
                raise AssertionError("%s: color:transparent outside skip-link rule (%s)" % (source, selector_raw))
            if prop == "position" and lv in ("absolute", "fixed", "sticky"):
                raise AssertionError("%s: position:%s outside skip-link rule (%s)" % (source, lv, selector_raw))
            if prop == "transform" and (
                "translate" in lv or "matrix(" in lv or re.search(r"scale\(\s*0", lv)
            ):
                raise AssertionError("%s: transform %r outside skip-link rule (%s)" % (source, value, selector_raw))
            if prop in ("top", "left", "right", "bottom", "inset", "text-indent"):
                for num, _unit in numeric_tokens(value):
                    if num < 0:
                        raise AssertionError("%s: negative offset %s:%s outside skip-link rule (%s)" % (source, prop, value, selector_raw))
            if prop in ("width", "height", "max-height"):
                for num, _unit in numeric_tokens(value):
                    if abs(num) < 1e-9:
                        raise AssertionError("%s: zero size %s:%s outside skip-link rule (%s)" % (source, prop, value, selector_raw))
            if prop == "overflow" and lv in ("hidden", "clip"):
                raise AssertionError("%s: overflow:%s outside skip-link rule (%s)" % (source, lv, selector_raw))
    return True


def guard_selectors_scoped(css):
    """Raise unless every selector is scoped strictly under .policy-page and every
    compound is a supported simple-selector sequence. A bare `.skip-link` is never
    accepted unscoped."""
    for selector_raw, _body in css_rules(css):
        for sel in selector_list(selector_raw):
            norm = normalize_selector(sel)
            if "\\" in norm or not norm.startswith(".policy-page"):
                raise AssertionError("selector not scoped under .policy-page: %r" % sel)
            for compound in re.split(r"[\s>+~]+", norm):
                if compound and not _SELECTOR_COMPOUND.match(compound):
                    raise AssertionError("unsupported selector structure: %r" % sel)
    return True


# ---------------------------------------------------------------------------
# Ancestor-chain visibility guard shared by the DOM test and its controls.
# ---------------------------------------------------------------------------


class HiddenAncestorGuard:
    """M1: reject hidden / aria-hidden (any value) / inert / inline style, and
    details / template / noscript, on the element carrying the content and on
    every ancestor. Returns a diagnostic string, or None when clean."""

    REJECT_TAGS = ("details", "template", "noscript")
    REJECT_ATTRS = ("hidden", "aria-hidden", "inert", "style")

    @staticmethod
    def check(dom, needle, what="content"):
        node = find_deepest_exact(dom, needle)
        if node is None:
            node = find_deepest_containing(dom, needle)
        if node is None:
            return "could not locate %s node for ancestor check" % what
        # The element carrying the text is itself a containing element, so it is
        # inspected together with every ancestor.
        for candidate in list(node.ancestors()) + [node]:
            if candidate.tag == "#document":
                continue
            for attr in HiddenAncestorGuard.REJECT_ATTRS:
                if attr in candidate.attrs:
                    return "%s attribute on <%s> containing %s" % (attr, candidate.tag, what)
            if candidate.tag in HiddenAncestorGuard.REJECT_TAGS:
                return "<%s> containing %s" % (candidate.tag, what)
        return None


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------


class LaneTestCase(unittest.TestCase):
    root = BASE

    @classmethod
    def setUpClass(cls):
        cls.lane = HttpLane(cls.root)
        cls.lane.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.lane.close()

    def path(self, rel):
        return Path(self.root) / rel

    def read(self, rel):
        return self.path(rel).read_text(encoding="utf-8")

    def read_bytes(self, rel):
        return self.path(rel).read_bytes()

    def fetch_doc(self, request_path):
        status, headers, body, final_url, chain = self.lane.get(request_path)
        self.assertEqual(
            status, 200,
            "expected 200 for %s, got %s (final_url=%s, chain=%r)"
            % (request_path, status, final_url, chain),
        )
        self.assertTrue(
            content_type(headers).startswith("text/html"),
            "expected text/html for %s, got %r (final_url=%s)"
            % (request_path, content_type(headers), final_url),
        )
        return parse_dom(body.decode("utf-8")), final_url, chain


# ---------------------------------------------------------------------------
# Nine named tests
# ---------------------------------------------------------------------------


class PolicyRouteTests(LaneTestCase):

    def test_each_route_resolves_to_its_document(self):
        for slug in SLUGS:
            for request_path in (
                "/%s" % slug,
                "/%s/" % slug,
                "/%s/index.html" % slug,
                "/%s?from=footer" % slug,
            ):
                with self.subTest(slug=slug, path=request_path):
                    dom, final_url, chain = self.fetch_doc(request_path)
                    self.lane.assert_same_origin(urlparse(final_url))
                    titles = [normalize_ws(dom.text(n)) for n in dom.by_tag("title")]
                    self.assertIn(
                        TITLES[slug], titles,
                        "title mismatch for %s: final_url=%s expected=%r actual=%r"
                        % (request_path, final_url, TITLES[slug], titles),
                    )
                    h1s = [normalize_ws(dom.text(n)) for n in dom.by_tag("h1")]
                    self.assertEqual(
                        h1s, [H1S[slug]],
                        "h1 mismatch for %s: final_url=%s expected h1=%r actual=%r"
                        % (request_path, final_url, H1S[slug], h1s),
                    )
                    doc_text = normalize_ws(dom.document_text())
                    self.assertIn(
                        normalize_ws(BODY[slug]), doc_text,
                        "unique body copy missing for %s (final_url=%s)" % (request_path, final_url),
                    )
                    self.assertNotIn(
                        "Turn cards you don't love into cards you do", doc_text,
                        "homepage hero leaked into %s (final_url=%s)" % (request_path, final_url),
                    )

                    if request_path.endswith("/") or request_path.endswith("index.html"):
                        # Direct slash/index requests must already be the document.
                        self.assertEqual(
                            chain, [],
                            "direct request %s must not redirect (final_url=%s, chain=%r)"
                            % (request_path, final_url, chain),
                        )
                    else:
                        # Slashless/query forms: the real same-origin directory 301
                        # chain is recorded, query preserved, landing on the dir form.
                        self.assertTrue(
                            chain,
                            "expected a directory redirect chain for %s, got none (final_url=%s)"
                            % (request_path, final_url),
                        )
                        status, hop_url, location = chain[0]
                        self.assertIn(
                            status, (301, 308),
                            "expected a permanent directory redirect for %s (got %s)" % (request_path, status),
                        )
                        self.assertEqual(
                            urlparse(hop_url).netloc, urlparse(self.lane.url("/")).netloc,
                            "redirect hop left the owned origin for %s" % request_path,
                        )
                        self.assertEqual(
                            urlparse(location).path, "/%s/" % slug,
                            "same-origin directory target for %s must be /%s/ (got %r)"
                            % (request_path, slug, location),
                        )
                        if "from=footer" in request_path:
                            self.assertIn(
                                "from=footer", location,
                                "first-hop Location must preserve the query for %s (got %r)"
                                % (request_path, location),
                            )
                            self.assertIn(
                                "from=footer", final_url,
                                "query string dropped across the redirect for %s (final_url=%s)"
                                % (request_path, final_url),
                            )

        self._assert_external_redirect_rejected_before_fetch()

    def _assert_external_redirect_rejected_before_fetch(self):
        """Guard control: an absolute https://cardbarter.app/... Location must be
        rejected BEFORE any fetch of that target. A lane-owned controlled handler
        supplies the Location, so no real external traffic occurs."""
        external = "https://cardbarter.app/privacy/"
        headers = Message()
        headers["Location"] = external
        headers["Content-Type"] = "text/html"
        handler = _ControlledLaneHandler(_FakeResponse(302, headers, b""))

        with self.assertRaises(AssertionError) as ctx:
            self.lane.get("/privacy", opener=handler)

        message = str(ctx.exception)
        self.assertIn(
            "outside this server", message,
            "an external Location must be rejected as outside the owned server: %s" % message,
        )
        self.assertIn(
            external, message,
            "the diagnostic must name the rejected external target: %s" % message,
        )
        self.assertEqual(
            len(handler.fetched), 1,
            "only the original same-origin request may be fetched (fetched=%r)" % handler.fetched,
        )
        self.assertNotIn(
            external, handler.fetched,
            "the external redirect target must never be fetched (fetched=%r)" % handler.fetched,
        )

    def test_home_footer_targets(self):
        dom, final_url, _chain = self.fetch_doc("/")
        footer = next((n for n in dom.by_tag("footer")), None)
        self.assertIsNotNone(footer, "no footer element on homepage (final_url=%s)" % final_url)

        mapping = {}
        for anchor in [n for n in walk(footer) if n.tag == "a"]:
            label = normalize_ws(dom.text(anchor))
            if label in FOOTER_EXPECTED:
                mapping[label] = anchor.get("href")

        for label, expected in FOOTER_EXPECTED.items():
            self.assertIn(label, mapping, "homepage footer link %r missing" % label)
            self.assertEqual(
                mapping[label], expected,
                "footer %s href expected %r got %r" % (label, expected, mapping[label]),
            )
            self.assertNotEqual(
                mapping[label], "#", "footer %s is still the placeholder anchor #" % label,
            )

        for label, expected in FOOTER_EXPECTED.items():
            with self.subTest(label=label):
                dom2, final2, _c = self.fetch_doc(expected)
                h1s = [normalize_ws(dom2.text(n)) for n in dom2.by_tag("h1")]
                self.assertEqual(
                    h1s, [H1S[expected.strip("/")]],
                    "footer %s must reach %s, got final_url=%s h1s=%r"
                    % (label, expected, final2, h1s),
                )

    def test_policy_navigation_and_assets(self):
        for slug in SLUGS:
            for request_path in ("/%s/" % slug, "/%s" % slug):
                with self.subTest(slug=slug, path=request_path):
                    dom, final_url, _chain = self.fetch_doc(request_path)
                    anchors = [n for n in dom.by_tag("a")]

                    labels = {}
                    for anchor in anchors:
                        labels.setdefault(normalize_ws(dom.text(anchor)), []).append(anchor.get("href"))

                    self.assertIn("Back to home", labels, "no Back to home link on %s" % final_url)
                    self.assertIn("/", labels["Back to home"], "Back to home must target / on %s" % final_url)

                    skip = [a for a in anchors if "skip-link" in a.classes()]
                    self.assertTrue(skip, "no skip link on %s" % final_url)
                    self.assertIn("#main", [a.get("href") for a in skip], "skip link must target #main on %s" % final_url)
                    self.assertTrue(
                        [n for n in dom.by_tag("main") if n.get("id") == "main"],
                        "skip link #main has no target on %s" % final_url,
                    )

                    for other in SLUGS:
                        target = "/%s" % other
                        sibling_hrefs = [
                            href
                            for label in ("Privacy", "Terms", "Support")
                            for href in labels.get(label, [])
                        ]
                        self.assertIn(
                            target, sibling_hrefs,
                            "missing sibling anchor %s on %s (found %r)" % (target, final_url, sibling_hrefs),
                        )

                    current = [
                        a for a in anchors
                        if normalize_ws(dom.text(a)) == H1S[slug] and a.get("aria-current") == "page"
                    ]
                    self.assertTrue(current, "no aria-current=page link for %s on %s" % (slug, final_url))

                    for node in dom.all_elements():
                        for attr in ("href", "src"):
                            value = node.get(attr)
                            if not value:
                                continue
                            scheme = urlparse(value).scheme.lower()
                            self.assertNotIn(
                                scheme, ("mailto", "tel"),
                                "external contact scheme %r on %s" % (value, final_url),
                            )
                            resolved = urljoin(final_url, value)
                            parsed = urlparse(resolved)
                            if parsed.scheme not in ("http", "https"):
                                continue
                            self.lane.assert_same_origin(parsed)
                            target_path = parsed.path or "/"
                            status, headers, _b, _f, _c = self.lane.get(target_path)
                            self.assertEqual(
                                status, 200,
                                "referenced %r -> %s returned %s on %s" % (value, resolved, status, final_url),
                            )
                            ct = content_type(headers)
                            if parsed.path.endswith(".css"):
                                self.assertIn("text/css", ct, "stylesheet %s content-type %r" % (resolved, ct))
                            elif parsed.path.endswith(".svg"):
                                self.assertIn("svg", ct, "image %s content-type %r" % (resolved, ct))
                            elif parsed.path.endswith(".png"):
                                self.assertIn("image", ct, "image %s content-type %r" % (resolved, ct))

    def test_provisional_dom_presence_and_attributes(self):
        """M1 — DOM presence/attribute proof only; never rendered visibility."""
        for slug in SLUGS:
            with self.subTest(slug=slug):
                dom, final_url, _chain = self.fetch_doc("/%s/" % slug)

                self.assertEqual(
                    [n.get("lang") for n in dom.by_tag("html")], ["en"],
                    "lang=en missing on %s" % final_url,
                )
                charsets = [n.get("charset") for n in dom.by_tag("meta") if n.get("charset")]
                self.assertIn("utf-8", [c.lower() for c in charsets], "UTF-8 not declared on %s" % final_url)
                viewports = [
                    n.get("content") for n in dom.by_tag("meta")
                    if (n.get("name") or "").lower() == "viewport"
                ]
                self.assertTrue(
                    any(v and "width=device-width" in v for v in viewports),
                    "viewport meta missing on %s" % final_url,
                )

                titles = [normalize_ws(dom.text(n)) for n in dom.by_tag("title")]
                self.assertEqual(titles, [TITLES[slug]], "unique title wrong on %s: %r" % (final_url, titles))
                raw_markup = self.read("%s/index.html" % slug)
                self.assertEqual(raw_markup.count("<title>"), 1, "expected exactly one <title> on %s" % final_url)

                h1s = dom.by_tag("h1")
                self.assertEqual(len(h1s), 1, "expected exactly one h1 on %s" % final_url)
                self.assertEqual(normalize_ws(dom.text(h1s[0])), H1S[slug], "h1 text wrong on %s" % final_url)

                # One main landmark, and the h1 plus all four key content nodes in it.
                mains = [n for n in dom.by_tag("main") if n.get("id") == "main"]
                self.assertEqual(
                    len(mains), 1,
                    "expected exactly one main id=main on %s, found %d" % (final_url, len(mains)),
                )
                main = mains[0]
                self.assertTrue(has_ancestor(h1s[0], main), "the h1 must live inside main id=main on %s" % final_url)
                for cls in ("policy-notice", "policy-version", "policy-body"):
                    nodes = find_by_class(dom, cls)
                    self.assertTrue(nodes, "no %s node on %s" % (cls, final_url))
                    for node in nodes:
                        self.assertTrue(
                            has_ancestor(node, main),
                            "%s must live inside main id=main on %s" % (cls, final_url),
                        )

                # Navigation landmark targeting exactly the three sibling routes.
                navs = [n for n in dom.by_tag("nav") if n.get("aria-label") == "Policy"]
                self.assertEqual(len(navs), 1, "expected exactly one policy nav landmark on %s" % final_url)
                nav_hrefs = [a.get("href") for a in walk(navs[0]) if a.tag == "a"]
                self.assertEqual(
                    sorted(nav_hrefs), sorted("/%s" % s for s in SLUGS),
                    "policy nav sibling hrefs wrong on %s: %r" % (final_url, nav_hrefs),
                )

                doc_text = normalize_ws(dom.document_text())
                for needle, what in (
                    (NOTICE, "draft notice"),
                    (VERSION, "preview metadata"),
                    (BODY[slug], "page-specific body copy"),
                ):
                    self.assertIn(normalize_ws(needle), doc_text, "missing %s on %s" % (what, final_url))

                lowered = doc_text.lower()
                for phrase in FORBIDDEN_EFFECTIVE_PHRASES:
                    self.assertNotIn(phrase, lowered, "effective-date claim %r on %s" % (phrase, final_url))

                for tag in ("form", "input", "textarea", "select", "button", "script"):
                    self.assertEqual(
                        [n.tag for n in dom.by_tag(tag)], [],
                        "disallowed <%s> present on %s" % (tag, final_url),
                    )

                for anchor in dom.by_tag("a"):
                    href = (anchor.get("href") or "").lower()
                    self.assertFalse(href.startswith("mailto:"), "mailto link on %s" % final_url)
                    self.assertFalse(href.startswith("tel:"), "tel link on %s" % final_url)

                for node in dom.all_elements():
                    for attr in ("href", "src"):
                        value = (node.get(attr) or "").lower()
                        self.assertFalse(
                            value.startswith("http://") or value.startswith("https://") or value.startswith("//"),
                            "external reference %r on %s" % (value, final_url),
                        )
                    self.assertNotIn("reveal-up", node.classes(), "reveal-up class on %s" % final_url)
                    if node.tag == "style":
                        self.fail("inline <style> present on %s" % final_url)
                    if node.tag == "link":
                        rel = (node.get("rel") or "").lower()
                        self.assertNotEqual(rel, "canonical", "canonical link present on %s" % final_url)
                        if rel == "stylesheet":
                            href = node.get("href") or ""
                            self.assertTrue(
                                href.startswith("/"),
                                "stylesheet href %r is not root-relative on %s" % (href, final_url),
                            )

                for what, needle in (
                    ("h1", H1S[slug]),
                    ("notice", NOTICE),
                    ("preview metadata", VERSION),
                    ("body copy", BODY[slug]),
                ):
                    problem = HiddenAncestorGuard.check(dom, needle, what)
                    self.assertIsNone(problem, "%s on %s" % (problem, final_url))

        self._assert_hidden_content_mutations_are_rejected()

    def _assert_hidden_content_mutations_are_rejected(self):
        """Mutation controls for M1: each hiding mutation must be rejected by the
        same guard the live pages pass."""
        mutations = (
            ("hidden attribute on the notice",
             '<p class="policy-notice" hidden>%s</p>' % NOTICE),
            ("aria-hidden=false on the notice",
             '<p class="policy-notice" aria-hidden="false">%s</p>' % NOTICE),
            ("inline style on the notice",
             '<p class="policy-notice" style="color: #BE3A2E">%s</p>' % NOTICE),
            ("inert on a wrapping element",
             '<div inert><p class="policy-notice">%s</p></div>' % NOTICE),
            ("notice inside closed details",
             '<details><p class="policy-notice">%s</p></details>' % NOTICE),
            ("notice inside template",
             '<template><p class="policy-notice">%s</p></template>' % NOTICE),
            ("notice inside noscript",
             '<noscript><p class="policy-notice">%s</p></noscript>' % NOTICE),
            ("inline visibility:HIDDEN !important on the notice",
             '<p class="policy-notice" style="visibility:HIDDEN !important">%s</p>' % NOTICE),
            ("inline position:fixed!important on the notice",
             '<p class="policy-notice" style="position:fixed!important">%s</p>' % NOTICE),
        )
        for label, markup in mutations:
            with self.subTest(mutation=label):
                dom = parse_dom(markup)
                self.assertIsNotNone(
                    HiddenAncestorGuard.check(dom, NOTICE, "notice"),
                    "mutation %r must be rejected by the ancestor guard" % label,
                )

        with self.subTest(mutation="notice removed entirely"):
            dom = parse_dom('<main id="main"><h1>Privacy</h1></main>')
            self.assertIsNotNone(
                HiddenAncestorGuard.check(dom, NOTICE, "notice"),
                "a missing notice must be rejected",
            )

        with self.subTest(mutation="preview metadata hidden"):
            dom = parse_dom(
                '<p class="policy-version" hidden>%s</p>' % VERSION
            )
            self.assertIsNotNone(
                HiddenAncestorGuard.check(dom, VERSION, "preview metadata"),
                "a hidden preview metadata node must be rejected",
            )

    def test_policy_css_has_no_hidden_content(self):
        """M1 — static CSS guard only, covering screen and nested print/media."""
        css = self.read("policy.css")
        guard_css(css, "policy.css")
        guard_selectors_scoped(css)

        for slug in SLUGS:
            dom, final_url, _c = self.fetch_doc("/%s/" % slug)
            carriers = [n for n in dom.all_elements() if "skip-link" in n.classes()]
            self.assertEqual(
                len(carriers), 1,
                "exactly one element may carry the skip-link class on %s, found %d"
                % (final_url, len(carriers)),
            )
            self.assertEqual(
                carriers[0].tag, "a",
                "the skip-link class may only be on the skip link anchor, found <%s> on %s"
                % (carriers[0].tag, final_url),
            )
            self.assertEqual(
                carriers[0].get("href"), "#main",
                "the skip-link class carrier must be the skip link on %s" % final_url,
            )
            for node in list(dom.by_tag("h1")) + list(dom.by_tag("p")):
                self.assertNotIn(
                    "skip-link", node.classes(),
                    "content-bearing <%s> must not carry the skip-link class on %s"
                    % (node.tag, final_url),
                )

        self._assert_css_mutations_are_rejected()

    def _assert_css_mutations_are_rejected(self):
        """Mutation controls for M1. The four proven guard counterexamples come
        first and must each be rejected; the rest cover unsupported/escaped,
        indirect, nested-print, off-screen and grouped-selector hiding."""
        mutations = (
            ("proven: display:none!important",
             ".policy-page .policy-notice { display:none!important; }"),
            ("proven: clip-path:polygon(0 0,0 0,0 0)",
             ".policy-page .policy-notice { clip-path:polygon(0 0,0 0,0 0); }"),
            ("proven: color:transparent !important",
             ".policy-page .policy-notice { color:transparent !important; }"),
            ("proven: :has(+ .skip-link) nested display:none",
             ".policy-page .policy-notice:has(+ .skip-link){display:none;}"),
            ("display:none!important in a print rule",
             "@media print { .policy-page .policy-notice { display:none!important; } }"),
            ("visibility:HIDDEN !important",
             ".policy-page .policy-version { visibility:HIDDEN !important; }"),
            ("position:fixed!important",
             ".policy-page .policy-notice { position:fixed!important; }"),
            ("color:transparent!important in a print rule",
             "@media print { .policy-page .policy-notice { color:transparent!important; } }"),
            ("display:none in a normal rule",
             ".policy-page .policy-notice { display: none; }"),
            ("visibility:hidden in a print rule",
             "@media print { .policy-page .policy-version { visibility: hidden; } }"),
            ("opacity:0",
             ".policy-page .policy-notice { opacity: 0; }"),
            ("font-size:0",
             ".policy-page .policy-version { font-size: 0; }"),
            ("clip-path inset off-screen",
             ".policy-page .policy-notice { clip-path: inset(50%); }"),
            ("clip rect off-screen",
             ".policy-page .policy-notice { clip: rect(0 0 0 0); }"),
            ("color:transparent",
             ".policy-page .policy-notice { color: transparent; }"),
            ("absolute negative offset",
             ".policy-page .policy-notice { position: absolute; left: -9999px; }"),
            ("fixed positioning",
             ".policy-page .policy-header { position: fixed; }"),
            ("transform translate",
             ".policy-page .policy-notice { transform: translateY(-200%); }"),
            ("value indirection via var()",
             ".policy-page .policy-notice { opacity: var(--policy-fade); }"),
            ("value indirection via calc()",
             ".policy-page .policy-version { font-size: calc(0px); }"),
            ("escaped declaration property",
             ".policy-page .policy-notice { d\\isplay: none; }"),
            ("escaped value",
             ".policy-page .policy-notice { visibility: hid\\64 den; }"),
            ("grouped with the skip link",
             ".skip-link, .policy-page .policy-version { display: none; }"),
            ("nested under a media query",
             "@media (max-width: 40rem) { .policy-page .policy-notice { display: none; } }"),
            ("clip-path hidden inside a print rule",
             "@media print { .policy-page .policy-notice { clip-path: polygon(0 0,0 0,0 0); } }"),
            ("skip-link used as a sibling operand",
             ".skip-link + .policy-page .policy-notice { display: none; }"),
            ("skip-link used as an ancestor of content",
             ".policy-page .skip-link .policy-notice { display: none; }"),
            ("@import of a remote stylesheet",
             '@import url("https://cdn.example.test/x.css");'),
            ("external font url() reference",
             '.policy-page .policy-brand { background-image: url("//cdn.example.test/x.woff2"); }'),
            ("unsupported at-rule cannot swallow the next rule",
             "@unsupported at-rule here { }\n.policy-page .policy-notice { display: none; }"),
        )
        for label, extra in mutations:
            with self.subTest(mutation=label):
                with self.assertRaises(AssertionError):
                    guard_css(self.read("policy.css") + "\n" + extra, "mutated policy.css")

        with self.subTest(mutation="unscoped selector"):
            with self.assertRaises(AssertionError):
                guard_selectors_scoped(
                    ".reveal-up { opacity: 0; }\n.policy-page { margin: 0; }"
                )

        with self.subTest(mutation="bare .skip-link is not scoped"):
            with self.assertRaises(AssertionError):
                guard_selectors_scoped(".skip-link { display: inline-block; }")

        with self.subTest(mutation="content on skip-link class"):
            with self.assertRaises(AssertionError):
                guard_css('.skip-link::before { content: "hidden"; }')

        with self.subTest(mutation="skip-link class moved onto content"):
            dom = parse_dom(
                '<p class="policy-notice skip-link">%s</p>'
                '<a class="skip-link" href="#main">Skip</a>' % NOTICE
            )
            carriers = [n for n in dom.all_elements() if "skip-link" in n.classes()]
            self.assertNotEqual(len(carriers), 1, "content carrying skip-link must not pass")
            self.assertTrue(
                any(n.tag == "p" for n in carriers),
                "the content element must be seen carrying the skip-link class",
            )

    def test_exact_case_paths_and_added_file_inventory(self):
        """M2 — case-exact component checks and exact added-file inventory."""
        for rel in ("privacy/index.html", "terms/index.html", "support/index.html", "policy.css"):
            with self.subTest(rel=rel):
                self._assert_case_exact(rel, Path(self.root))
        self._assert_inventory(Path(self.root))
        self._assert_inventory_mutations_are_rejected()

    def _assert_inventory_mutations_are_rejected(self):
        """Mutation controls for M2, run against disposable fixture copies that
        are created and removed inside this test."""
        tempdir = tempfile.mkdtemp(prefix="rm-p8-m2-")
        try:
            fixture = make_fixture_root(Path(tempdir) / "case-mismatch", self.root)
            os.rename(fixture / "privacy", fixture / "Privacy")
            with self.subTest(mutation="privacy renamed to Privacy"):
                with self.assertRaises(AssertionError):
                    self._assert_case_exact("privacy/index.html", fixture)

            fixture = make_fixture_root(Path(tempdir) / "uppercase-file", self.root)
            os.rename(fixture / "policy.css", fixture / "Policy.css")
            with self.subTest(mutation="policy.css renamed to Policy.css"):
                with self.assertRaises(AssertionError):
                    self._assert_case_exact("policy.css", fixture)

            fixture = make_fixture_root(Path(tempdir) / "extra-file", self.root)
            replace_fixture_file(
                fixture / "privacy-notes.html",
                '<!doctype html><html lang="en"><body></body></html>',
            )
            with self.subTest(mutation="unexpected production file added"):
                with self.assertRaises(AssertionError):
                    self._assert_inventory(fixture)

            fixture = make_fixture_root(Path(tempdir) / "missing-file", self.root)
            (fixture / "policy.css").unlink()
            with self.subTest(mutation="policy.css missing"):
                with self.assertRaises(AssertionError):
                    self._assert_inventory(fixture)

            fixture = make_fixture_root(Path(tempdir) / "missing-page", self.root)
            shutil.rmtree(fixture / "terms")
            with self.subTest(mutation="terms page removed"):
                with self.assertRaises(AssertionError):
                    self._assert_inventory(fixture)

            fixture = make_fixture_root(Path(tempdir) / "hardlink-check", self.root)
            with self.subTest(mutation="asset fixtures are hardlinks, not copies"):
                source_stat = self.path("assets/hero-base.png").stat()
                linked_stat = (fixture / "assets" / "hero-base.png").stat()
                self.assertEqual(
                    (source_stat.st_dev, source_stat.st_ino),
                    (linked_stat.st_dev, linked_stat.st_ino),
                    "hero fixture asset must be a hardlink, not a 7.6MB copy",
                )
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_noindex_on_each_draft(self):
        for slug in SLUGS:
            with self.subTest(slug=slug):
                dom, final_url, _c = self.fetch_doc("/%s/" % slug)
                robots = [n for n in dom.by_tag("meta") if (n.get("name") or "").lower() == "robots"]
                self.assertEqual(len(robots), 1, "expected exactly one robots meta on %s" % final_url)
                directives = [d.strip().lower() for d in (robots[0].get("content") or "").split(",")]
                self.assertIn("noindex", directives, "noindex missing on %s" % final_url)
                self.assertIn("nofollow", directives, "nofollow missing on %s" % final_url)

    def test_homepage_and_existing_files_preserved(self):
        raw = self.read("index.html")
        self.assertEqual(
            raw.count('<a href="#" class="footer-link">'), 0,
            "candidate homepage still contains a placeholder footer anchor",
        )
        reversed_html = raw
        for new, old in FOOTER_SUBSTITUTIONS:
            self.assertEqual(
                reversed_html.count(new), 1,
                "expected exactly one %r in the candidate homepage" % new,
            )
            reversed_html = reversed_html.replace(new, old)
        self.assertEqual(
            hashlib.sha256(reversed_html.encode("utf-8")).hexdigest(),
            BASE_INDEX_SHA256,
            "homepage changed beyond the three footer hrefs against pinned base %s" % BASE_COMMIT,
        )

        self.assertEqual(
            hashlib.sha256(self.read_bytes("styles.css")).hexdigest(),
            BASE_STYLES_SHA256,
            "styles.css must remain byte-identical to the pinned base",
        )

        # CNAME and the three assets must be unchanged BLOBS, not merely present:
        # recompute the Git blob SHA1 from candidate bytes against the pinned base.
        for rel, expected in sorted(PINNED_BLOB_SHA1.items()):
            with self.subTest(rel=rel):
                actual = blob_sha1(self.read_bytes(rel))
                self.assertEqual(
                    actual, expected,
                    "blob differs from pinned base for %s: expected %s got %s (base %s)"
                    % (rel, expected, actual, BASE_COMMIT),
                )

    def test_unknown_path_is_not_a_policy_page(self):
        status, _headers, body, final_url, _chain = self.lane.get("/rm-p8-no-such-page")
        self.assertEqual(
            status, 404,
            "expected 404 for an unknown path, got %s (final_url=%s)" % (status, final_url),
        )
        text = body.decode("utf-8", "replace")
        for slug in SLUGS:
            self.assertNotIn(
                "<h1>%s</h1>" % H1S[slug], text,
                "unknown path served the %s policy document" % slug,
            )

    # -- M2 helpers --
    def _assert_case_exact(self, rel, root):
        parts = rel.split("/")
        current = Path(root)
        for part in parts:
            self.assertTrue(current.is_dir(), "not a directory: %s" % current)
            entries = os.listdir(current)
            self.assertIn(
                part, entries,
                "case-exact component %r not found in %s (entries=%r)"
                % (part, current, sorted(entries)),
            )
            current = current / part
        self.assertTrue(current.is_file(), "not a file: %s" % current)

    def _assert_inventory(self, root):
        tracked = self._base_inventory()
        present = set()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules")]
            rel_dir = os.path.relpath(dirpath, root)
            for name in filenames:
                rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                present.add(rel.replace(os.sep, "/"))
        added = {r for r in present if r not in tracked}
        self.assertEqual(
            sorted(added - EXPECTED_ADDED_ALL), [],
            "unexpected added files: %r" % sorted(added - EXPECTED_ADDED_ALL),
        )
        self.assertEqual(
            sorted(EXPECTED_ADDED_ALL - present), [],
            "expected additions missing: %r" % sorted(EXPECTED_ADDED_ALL - present),
        )
        self.assertEqual(
            sorted(added), sorted(EXPECTED_ADDED_ALL),
            "added file inventory differs from the reviewed set",
        )

    def _base_inventory(self):
        """Recorded pinned six-file base inventory. The tests/base_inventory.txt
        fallback was never allowed to exist and has been removed, so this pinned
        set is the only source of truth."""
        return {
            "CNAME",
            "index.html",
            "styles.css",
            "assets/hero-base.png",
            "assets/icon.svg",
            "assets/logo.svg",
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)

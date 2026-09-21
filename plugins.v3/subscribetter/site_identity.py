"""Extract a site's explicit identity statement, never a verified mapping."""
from hashlib import sha256
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit


def _provider_id(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.username is not None or
                parts.password is not None or parts.port not in (None, 443) or
                parts.query or parts.fragment):
            return None
    except ValueError:
        return None
    if parts.hostname in {'movie.douban.com', 'www.douban.com'}:
        source, pattern = 'douban', r'/subject/([1-9][0-9]{0,19})/?'
    elif parts.hostname in {'imdb.com', 'www.imdb.com'}:
        source, pattern = 'imdb', r'/title/(tt[0-9]{1,20})/?'
    else:
        return None
    match = re.fullmatch(pattern, parts.path)
    return (source, match[1]) if match and match[1].removeprefix('tt').strip('0') else None


class _Description(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html = html
        self.offsets = [0]
        self.offsets.extend(match.end() for match in re.finditer('\n', html))
        self.count = 0
        self.invalid = False
        self.depth = 0
        self.start = self.end = None
        self.frames = []
        self.text = []
        self.urls = []

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if len(attrs) != len({name for name, _ in attrs}):
            self.invalid = True
        attrs = dict(attrs)
        hidden = (bool(self.frames and self.frames[-1][1]) or
                  tag in {'script', 'style', 'nav', 'aside', 'template'} or 'hidden' in attrs or
                  bool(re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b',
                                 attrs.get('style') or '', re.I)))
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.frames.append((tag, hidden))
        if tag == 'div' and attrs.get('id') == 'kdescr':
            self.count += 1
            if not self.depth and self.start is None:
                self.start = self.position() + len(self.get_starttag_text())
                self.depth = 1
                return
        if not self.depth:
            return
        if tag == 'div':
            self.depth += 1
        if not hidden and tag == 'a' and attrs.get('href'):
            self.urls.append(attrs['href'])

    def handle_endtag(self, tag):
        for index in range(len(self.frames)-1, -1, -1):
            if self.frames[index][0] == tag:
                del self.frames[index:]
                break
        if not self.depth:
            return
        if tag == 'div':
            self.depth -= 1
            if not self.depth:
                self.end = self.position()

    def handle_data(self, data):
        if self.depth and not (self.frames and self.frames[-1][1]):
            self.text.append(data)


def extract_site_identity(html: str, *, media_type: str) -> dict:
    """Parse the supported NexusPHP main description; caller verifies media type.

    The returned IDs are site assertions only. No HTTP, provider recognition,
    target mutation, or quality decision is performed here.
    """
    if media_type not in {'电影', '电视剧'}:
        return {'state': 'UNKNOWN', 'reason': 'MEDIA_TYPE_REQUIRED'}
    if not isinstance(html, str) or len(html) > 2_000_000:
        return {'state': 'UNKNOWN', 'reason': 'INVALID_HTML'}
    body = _Description(html)
    body.feed(html)
    body.close()
    if body.invalid:
        return {'state': 'UNKNOWN', 'reason': 'AMBIGUOUS_HTML_ATTRIBUTES'}
    if not body.count:
        return {'state': 'UNSUPPORTED', 'reason': 'MAIN_DESCRIPTION_NOT_FOUND'}
    if body.count != 1:
        return {'state': 'CONFLICT', 'reason': 'MULTIPLE_MAIN_DESCRIPTIONS'}
    if body.end is None:
        return {'state': 'UNKNOWN', 'reason': 'INCOMPLETE_MAIN_DESCRIPTION'}
    evidence = {'locator': '#kdescr', 'media_type': media_type,
                'body_sha256': sha256(html[body.start:body.end].encode('utf-8')).hexdigest()}
    for url, label in re.findall(r'\[url=(https://[^\s\[\]]+)\](.*?)\[/url\]',
                                 '\n'.join(body.text), flags=re.IGNORECASE | re.DOTALL):
        body.urls.extend((url, label.strip()))
    identities = {'douban': set(), 'imdb': set()}
    for url in body.urls:
        pair = _provider_id(url)
        if pair:
            identities[pair[0]].add(pair[1])
    if any(len(values) > 1 for values in identities.values()):
        return dict(evidence, state='CONFLICT', reason='MULTIPLE_PROVIDER_IDS')
    if not all(identities.values()):
        return dict(evidence, state='UNKNOWN', reason='PROVIDER_PAIR_MISSING')
    return dict(evidence, state='DECLARED',
                provider_ids={source: next(iter(ids)) for source, ids in identities.items()})

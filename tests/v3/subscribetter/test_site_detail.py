"""Public SDK detail transport contract; no host or network access."""
from contextlib import contextmanager
import sys
import types
import unittest
from unittest.mock import Mock, patch
from test_planner import load


class SiteDetailTests(unittest.TestCase):
    def setUp(self):
        self.m = load('candidates')
        self.raw = dict(site=7, page_url='https://site.invalid/details.php?id=42')
        self.site = dict(id=7, url='https://site.invalid/', cookie='SECRET', ua='fixture', proxy=True)
        self.response = Mock(status_code=200, url=self.raw['page_url'], headers={}, encoding='utf-8')
        self.response.iter_content.return_value = [b'<div>fixture</div>']
        self.request = Mock()
        @contextmanager
        def stream(*args, **kwargs):
            try: yield self.response
            finally: self.response.close()
        self.request.get_stream.side_effect = stream
        self.constructor = Mock(return_value=self.request)
        self.safe = Mock(return_value=True)
        network = types.ModuleType('app.sdk.network')
        network.RequestUtils = self.constructor
        network.SecurityUtils = types.SimpleNamespace(is_safe_url=self.safe)
        config = types.ModuleType('app.sdk.config')
        config.settings = types.SimpleNamespace(PROXY={'https':'http://proxy.invalid'})
        self.enterContext(patch.dict(sys.modules, {'app.sdk.network':network, 'app.sdk.config':config}))
        self.enterContext(patch.object(self.m.HostCandidateAdapter, 'sites', return_value=[self.site]))
        self.enterContext(patch.object(self.m.time, 'monotonic', return_value=100))

    def call(self, selected=None):
        return self.m.HostCandidateAdapter.site_description(self.raw, [7] if selected is None else selected, deadline=110)

    def test_success_uses_selected_configuration_and_closes(self):
        self.assertEqual('<div>fixture</div>', self.call())
        args = self.constructor.call_args.kwargs
        self.assertEqual(('SECRET','fixture',True), (args['cookies'],args['ua'],args['verify']))
        self.assertEqual({'https':'http://proxy.invalid'}, args['proxies'])
        self.assertEqual(10, args['timeout'])
        self.request.get_stream.assert_called_once_with(self.raw['page_url'], allow_redirects=False, raise_exception=True)
        self.response.close.assert_called_once()

    def test_foreign_origin_unselected_and_unsafe_send_no_request(self):
        for url in ('https://other.invalid/detail', 'http://site.invalid/detail',
                    'https://site.invalid:444/detail', 'https://SECRET@site.invalid/detail'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.raw['page_url'] = url
                self.call()
        self.raw['page_url'] = 'https://site.invalid/detail'
        with self.assertRaises(ValueError): self.call([8])
        self.safe.return_value = False
        with self.assertRaises(ValueError): self.call()
        self.constructor.assert_not_called()

    def test_redirect_and_oversize_close_without_full_content(self):
        for status, headers, chunks in ((302,{},[]),(200,{'Content-Length':str(2*1024*1024+1)},[]),
                                       (200,{},[b'x'*(2*1024*1024),b'x'])):
            with self.subTest(status=status, headers=headers), self.assertRaises(ValueError):
                self.response.status_code=status; self.response.headers=headers
                self.response.iter_content.return_value=chunks
                self.call()
        self.assertEqual(3,self.response.close.call_count)

    def test_expired_deadline_and_transport_errors_are_sanitized(self):
        with patch.object(self.m.time,'monotonic',return_value=111), self.assertRaisesRegex(ValueError,'^SITE_DETAIL_TIMEOUT$'):
            self.call()
        self.constructor.assert_not_called()
        self.request.get_stream.side_effect = RuntimeError('SECRET cookie URL')
        with self.assertRaisesRegex(ValueError,'^SITE_DETAIL_FAILED$'):
            self.call()

    def test_stream_deadline_closes(self):
        def chunks(**_):
            yield b'first'
            self.m.time.monotonic.return_value=111
            yield b'second'
        self.response.iter_content.side_effect=chunks
        with self.assertRaisesRegex(ValueError,'^SITE_DETAIL_TIMEOUT$'): self.call()
        self.response.close.assert_called_once()

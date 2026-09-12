"""Executable NameRecognize contract excerpt, NOT a complete MoviePilot runtime.

Source: jxxghp/MoviePilot app/chain/media.py, v2.15.6 and V2 HEAD
6a02e7de21c110d758e3fc44e79150ac1d4d7949, blob 2c17871a2f42454685c7069ad2bdb6f63f658682.
The method body below preserves the upstream branch/assignment order. Type
annotations, comments and logger lines are omitted; lookup/event dependencies
are supplied by the test. The async version changes only def and await sites.
"""
from types import SimpleNamespace


BODY = '''def recognize_help(self, title, org_meta, share_meta=None, source=None, episode_group=None):
    result = eventmanager.send_event(ChainEventType.NameRecognize, {"title": title})
    if not result:
        return None
    event_data = result.event_data or {}
    title, year, season_number, episode_number = None, None, None, None
    if event_data.get("name"):
        title = str(event_data["name"]).split("/")[0].strip().replace(".", " ")
    if event_data.get("year"):
        year = str(event_data["year"]).split("/")[0].strip()
    season_number = self._parse_recognize_event_number(event_data.get("season"))
    episode_number = self._parse_recognize_event_number(event_data.get("episode"))
    if not title:
        return None
    if title == "Unknown":
        return None
    if not str(year).isdigit():
        year = None
    if title == org_meta.name and year == org_meta.year:
        return None
    org_meta.name = title
    org_meta.year = year
    org_meta.begin_season = season_number
    org_meta.begin_episode = episode_number
    if org_meta.begin_season is not None or org_meta.begin_episode is not None:
        org_meta.type = MediaType.TV
    return self.recognize_media(meta=org_meta, source=source,
                                share_meta=share_meta, episode_group=episode_group)
'''


def make_contract(handler):
    class Events:
        def send_event(self, kind, data):
            event = SimpleNamespace(event_data=data)
            handler(event)
            return event
        async def async_send_event(self, kind, data):
            return self.send_event(kind, data)

    context = {'eventmanager': Events(), 'ChainEventType': SimpleNamespace(NameRecognize='recognize'),
               'MediaType': SimpleNamespace(TV='TV')}
    exec(BODY, context)
    async_body = BODY.replace('def recognize_help', 'async def async_recognize_help')
    async_body = async_body.replace('eventmanager.send_event(', 'await eventmanager.async_send_event(')
    async_body = async_body.replace('return self.recognize_media(', 'return await self.async_recognize_media(')
    exec(async_body, context)

    class Host:
        recognize_help = context['recognize_help']
        async_recognize_help = context['async_recognize_help']
        calls = None
        def __init__(self):
            self.calls = []
        @staticmethod
        def _parse_recognize_event_number(value):
            if value is None:
                return None
            text = str(value).strip()
            return int(text) if text.isdigit() else None
        def recognize_media(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs['meta']
        async def async_recognize_media(self, **kwargs):
            return self.recognize_media(**kwargs)
    return Host()

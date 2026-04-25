from typing import Any, Optional

class Mock:
    def __init__(self, *args, **kwargs):
        pass
    def __getattr__(self, name):
        return Mock()
    def __call__(self, *args, **kwargs):
        return Mock()

class Embed(Mock): pass
class Interaction(Mock): pass
class Message(Mock): pass
class Member(Mock): pass
class VoiceChannel(Mock): pass
class Guild(Mock): pass
class Bot(Mock): pass
class Context(Bot): pass
class View(Mock): pass
class ChannelType(Mock): pass

class MISSING:
    pass

class utils:
    MISSING = MISSING()

class commands:
    Context = Context
    Bot = Bot

class ext:
    commands = commands

class ui:
    View = View

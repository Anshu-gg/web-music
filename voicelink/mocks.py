from typing import Any, Optional

class Mock:
    def __init__(self, *args, **kwargs):
        pass
    def __getattr__(self, name):
        return Mock()
    def __call__(self, *args, **kwargs):
        return Mock()

class Embed(Mock):
    pass

class Interaction(Mock):
    pass

class Message(Mock):
    pass

class Member(Mock):
    pass

class VoiceChannel(Mock):
    pass

class Guild(Mock):
    pass

class MISSING:
    pass

class utils:
    MISSING = MISSING()

class ext:
    class commands:
        Context = Mock
        Bot = Mock

class ui:
    View = Mock

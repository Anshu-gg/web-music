from __future__ import annotations
import time, logging, asyncio
from random import shuffle
from typing import Any, Dict, List, Optional, Union, Tuple, TYPE_CHECKING

from .config import Config
from .pool import Node, NodePool
from .objects import Track, Playlist
from .filters import Filters
from .enums import SearchType, RequestMethod
from .queue import Queue, QUEUE_TYPES
from .language import LangHandler
from .placeholders import PlayerPlaceholder

if TYPE_CHECKING:
    from .pool import Node

class StandalonePlayer:
    def __init__(
        self,
        node: Node,
        guild_id: int,
        user: Any,
        settings: dict = None
    ):
        self._user = user
        self._node: Node = node
        self.guild_id: int = guild_id
        self.settings: dict = settings or {}
        
        self.queue: Queue = QUEUE_TYPES.get(self.settings.get("queue_type", "queue").lower())(
            self.settings.get("max_queue", Config().max_queue),
            self.settings.get("duplicate_track", True), self.get_msg
        )

        self._current: Optional[Track] = None
        self._filters: Filters = Filters()
        self._paused: bool = False
        self._is_connected: bool = True  # Always connected in standalone web
        self._ping: float = 0.0
        
        self._position: int = 0
        self._last_position: int = 0
        self._last_update: int = 0
        self._volume: int = self.settings.get('volume', 100)
        self.autoplay: bool = self.settings.get('autoplay', False)
        self.autoplay_count: int = 0
        self._lock = asyncio.Lock()

        self._logger: logging.Logger = node._logger
        self._ph = PlayerPlaceholder(None, self)
        self._is_playing = False
    
    async def _update_state(self, data: dict):
        state = data.get("state", {})
        self._last_position = state.get("position", 0)
        self._last_update = time.time() * 1000
        self._ping = state.get("ping", 0)
        self._is_playing = state.get("connected", False)

    async def _dispatch_event(self, data: dict):
        event_type = data.get("type")
        self._logger.info(f"Player in {self.guild_id} received event: {event_type}, data: {data}")
        if event_type == "TrackEndEvent":
            reason = data.get("reason")
            if reason == "cleanup":
                self._logger.warning(f"Player in {self.guild_id} received cleanup event (likely due to lack of voice connection). Ignoring to prevent loops.")
                return

            if reason != "replaced":
                self._current = None
                await self.do_next()
        elif event_type == "TrackStartEvent":
            self.autoplay_count = 0 # Reset autoplay count when a new track starts
        
        # Notify the user to sync state with client
        if self._user:
            await self._user.send_player_state()

    @property
    def position(self) -> float:
        if not self._current:
            return 0
        if self._paused:
            return min(self._last_position, self._current.length)
        
        difference = (time.time() * 1000) - self._last_update
        position = self._last_position + difference
        return min(position, self._current.length)

    @property
    def is_playing(self) -> bool:
        return self._current is not None

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def current(self) -> Optional[Track]:
        return self._current

    @property
    def node(self) -> Node:
        return self._node

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def dj(self) -> Any:
        # Mock DJ object for standalone mode
        class MockDJ:
            def __init__(self, name):
                self.name = name
                self.mention = f"@{name}"
                self.display_avatar = type('obj', (object,), {'url': "https://i.imgur.com/dIFBwU7.png"})
        return MockDJ("Guest")

    def get_msg(self, *keys) -> Union[list[str], str]:
        return LangHandler._get_lang(self.settings.get("lang"), *keys)


    async def do_next(self):
        async with self._lock:
            self._logger.info(f"Player in {self.guild_id} calling do_next. Current: {self._current.title if self._current else 'None'}")
            if self._current:
                return

        track = self.queue.get()
        if track:
            await self.play(track)
        elif self.autoplay and self.autoplay_count < 10:
            if await self.get_recommendations():
                # Re-call do_next to play the newly added recommendation
                # This is safe because we are inside the lock
                track = self.queue.get()
                if track:
                    await self.play(track)
        
    async def get_recommendations(self) -> bool:
        """Fetches and adds a recommended track based on recent history."""
        from random import choice
        self._logger.info(f"Player in {self.guild_id} attempting to get recommendations.")
        try:
            history = self.queue.history(incTrack=True)
            if not history:
                self._logger.info(f"Player in {self.guild_id} history is empty, cannot get recommendations.")
                return False
            track = choice(history[-5:])
            self._logger.info(f"Player in {self.guild_id} selected track '{track.title}' for recommendations.")
        except Exception as e:
            self._logger.error(f"Player in {self.guild_id} error choosing track from history: {e}")
            return False

        try:
            tracks = await track.get_recommendations(self._node)
            if tracks:
                # Filter out tracks already in queue or history using identifier and title
                existing_ids = [t.identifier for t in self.queue._queue]
                existing_titles = [t.title.lower() for t in self.queue._queue]
                recommended_track = None
                for t in tracks:
                    if t.identifier not in existing_ids and t.title.lower() not in existing_titles:
                        recommended_track = t
                        break
                
                if not recommended_track:
                    recommended_track = tracks[0] # Fallback if all are "existing"

                self._logger.info(f"Player in {self.guild_id} adding recommended track '{recommended_track.title}'.")
                self.queue.put(recommended_track)
                self.autoplay_count += 1
                # We don't need to call do_next here because do_next is already running or will be triggered
                # But wait! do_next was called and found nothing, so it called us.
                # So we should return and let do_next proceed? No, do_next already failed the 'if track' check.
                
                # Correct way: let get_recommendations return the track and let do_next handle it
                return True
            else:
                self._logger.info(f"Player in {self.guild_id} no recommendations found for track '{track.title}'.")
        except Exception as e:
            self._logger.error(f"Player in {self.guild_id} error fetching recommendations: {e}")
            
        return False
    async def play(self, track: Track, start: int = 0):
        data = {
            "track": {"encoded": track.track_id},
            "position": int(start or 0)
        }
        await self._node.send(RequestMethod.PATCH, query=f"players/{self.guild_id}", data=data)
        self._current = track
        self._last_position = start
        self._last_update = time.time() * 1000

    async def set_pause(self, pause: bool):
        self._paused = pause
        await self._node.send(RequestMethod.PATCH, query=f"players/{self.guild_id}", data={"paused": pause})

    async def set_volume(self, volume: int):
        self._volume = volume
        await self._node.send(RequestMethod.PATCH, query=f"players/{self.guild_id}", data={"volume": volume})

    async def seek(self, position: int):
        await self._node.send(RequestMethod.PATCH, query=f"players/{self.guild_id}", data={"position": position})
        self._last_position = position
        self._last_update = time.time() * 1000

    async def stop(self):
        self._logger.info(f"Player in {self.guild_id} was stopped.")
        self._current = None
        await self._node.send(RequestMethod.PATCH, query=f"players/{self.guild_id}", data={'encodedTrack': None})

    async def teardown(self):
        self._logger.info(f"Player in {self.guild_id} is tearing down.")
        try:
            await self._node.send(RequestMethod.DELETE, query=f"players/{self.guild_id}")
        except:
            pass
        if self.guild_id in self._node._players:
            del self._node._players[self.guild_id]

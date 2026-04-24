import asyncio
import json
import quart
import os
import logging

from babel.languages import get_official_languages
from geoip2 import records

from typing import (
    Optional,
    Dict,
    Any,
    List,
)

from utils import (
    ROOT_DIR,
    LANGUAGES,
    LOGGER,
)

from voicelink import NodePool, StandalonePlayer, Track, Playlist
from voicelink.transformer import decode


class User:
    """Represents a guest user session for the standalone music web app."""

    def __init__(self, pool, data: Dict):
        self.id: str = data.get("id")
        self.name: str = data.get("name", f"Guest_{self.id[:4]}")
        self.avatar_url: str = "/static/img/notFound.png"
        self.country: Optional[records.Country] = data.get("country")

        self.player: Optional[StandalonePlayer] = None

        self._pool = pool
        self._websocket: Optional[quart.Websocket] = None

    async def send(self, payload: Dict) -> None:
        """Send a JSON payload to the connected WebSocket client."""
        if self._websocket:
            try:
                await self._websocket.send_json(payload)
            except Exception:
                pass

    async def _listen(self) -> None:
        """Listen for incoming WebSocket messages from the client."""
        try:
            while True:
                data = await self._websocket.receive()
                if data is None:
                    break
                
                try:
                    payload = json.loads(data)
                    await self.handle_command(payload)
                except json.JSONDecodeError:
                    LOGGER.warning(f"Received invalid JSON from user {self.id}")
                except Exception as e:
                    LOGGER.error(f"Error in handle_command: {e}", exc_info=True)
        except Exception as e:
            LOGGER.error(f"WebSocket listener error for user {self.id}: {e}")
        finally:
            LOGGER.info(f"WebSocket listener for user {self.id} stopped.")

    def _ensure_player(self):
        """Lazily initialize a StandalonePlayer for this user."""
        if not self.player:
            node = NodePool.get_node()
            # Convert guest hex ID to a numeric ID for Lavalink compatibility
            numeric_id = int(self.id, 16) % (2**63 - 1)
            self.player = StandalonePlayer(node, numeric_id, self)
            node._players[numeric_id] = self.player

    async def handle_command(self, payload: Dict) -> None:
        """Route incoming WebSocket operations to handler methods."""
        op = payload.get("op")
        if op == "heartbeat":
            return


        handler = {
            "initPlayer": self._handle_init_player,
            "getTracks": self._handle_get_tracks,
            "addTracks": self._handle_add_tracks,
            "updatePause": self._handle_pause,
            "skipTo": self._handle_skip,
            "backTo": self._handle_back,
            "trackFinished": self._handle_track_finished,
            "updateVolume": self._handle_volume,
            "updatePosition": self._handle_seek,
            "repeatTrack": self._handle_repeat,
            "shuffleTrack": self._handle_shuffle,
            "removeTrack": self._handle_remove_track,
            "moveTrack": self._handle_move_track,
            "clearQueue": self._handle_clear_queue,
            "toggleAutoplay": self._handle_toggle_autoplay,
        }.get(op)

        if handler:
            try:
                self._ensure_player()
                await handler(payload)
            except Exception as e:
                LOGGER.error(f"Error handling op '{op}': {e}", exc_info=True)
                await self.send({"op": "errorMsg", "level": "error", "msg": str(e)})
        else:
            LOGGER.warning(f"Unknown op received: {op}")

    # ── Handler Methods ──────────────────────────────────────────

    async def _handle_init_player(self, payload: Dict):
        await self.send_player_state()

    async def _handle_get_tracks(self, payload: Dict):
        query = payload.get("query", "")
        callback = payload.get("callback", "")
        
        track_ids = []
        try:
            result = await self.player.node.get_tracks(query, requester=self)
            if result:
                if isinstance(result, Playlist):
                    track_ids = [t.track_id for t in result.tracks]
                elif isinstance(result, list):
                    track_ids = [t.track_id for t in result]
            LOGGER.info(f"Search result for '{query}': {len(track_ids)} tracks found.")
        except Exception as e:
            LOGGER.error(f"Error searching tracks for query '{query}': {e}", exc_info=True)

        await self.send({
            "op": "getTracks",
            "tracks": track_ids,
            "callback": callback,
        })

    async def _handle_add_tracks(self, payload: Dict):
        tracks = payload.get("tracks", [])
        position = payload.get("position", -1)

        for t in tracks:
            try:
                info = decode(t)
                track = Track(track_id=t, info=info, requester=self)
                self.player.queue.put(track)
            except Exception as e:
                LOGGER.error(f"Failed to decode/add track: {e}")
                continue

        if not self.player.is_playing:
            await self.player.do_next()

        await self.send_player_state()

    async def _handle_pause(self, payload: Dict):
        pause = payload.get("pause", not self.player.is_paused)
        await self.player.set_pause(pause)
        await self.send({
            "op": "updatePause",
            "pause": self.player.is_paused,
            "requesterId": self.id,
        })

    async def _handle_skip(self, payload: Dict):
        index = payload.get("index", 1)
        # Skip forward by advancing the queue
        for _ in range(index):
            if self.player.queue:
                self.player.queue.get()
        await self.player.stop()
        await self.player.do_next()
        await self.send_player_state()

    async def _handle_back(self, payload: Dict):
        index = payload.get("index", 1)
        # Back is handled by the queue's history
        if hasattr(self.player.queue, 'backto'):
            self.player.queue.backto(index)
        await self.player.stop()
        await self.player.do_next()
        await self.send_player_state()

    async def _handle_track_finished(self, payload: Dict):
        # Only skip if we are actually playing something
        if self.player.current:
            LOGGER.info(f"User {self.name} browser signal: trackFinished. Transitioning...")
            self.player._current = None
            await self.player.do_next()
            await self.send_player_state()

    async def _handle_volume(self, payload: Dict):
        volume = payload.get("volume", 100)
        volume = max(0, min(100, int(volume)))
        await self.player.set_volume(volume)
        await self.send({
            "op": "updateVolume",
            "volume": self.player.volume,
            "requesterId": self.id,
        })

    async def _handle_seek(self, payload: Dict):
        position = payload.get("position", 0)
        if self.player.current and not self.player.current.is_stream:
            pos_ms = int((position / 500) * self.player.current.length)
            await self.player.seek(pos_ms)
            await self.send({
                "op": "playerUpdate",
                "lastUpdate": 0,
                "isConnected": True,
                "lastPosition": pos_ms,
            })

    async def _handle_repeat(self, payload: Dict):
        modes = ["off", "queue", "track"]
        if hasattr(self.player.queue, 'repeat'):
            current = self.player.queue.repeat
            idx = modes.index(current) if current in modes else 0
            self.player.queue.repeat = modes[(idx + 1) % len(modes)]
            await self.send({
                "op": "repeatTrack",
                "repeatMode": self.player.queue.repeat,
                "requesterId": self.id,
            })

    async def _handle_shuffle(self, payload: Dict):
        if hasattr(self.player.queue, 'shuffle'):
            self.player.queue.shuffle()
        await self.send_player_state()

    async def _handle_remove_track(self, payload: Dict):
        index = payload.get("index", -1) - 1 # Frontend is 1-indexed relative to "Next"
        try:
            q = self.player.queue._queue
            pos = self.player.queue._position
            target_idx = pos + index
            if pos <= target_idx < len(q):
                removed = q.pop(target_idx)
                await self.send({
                    "op": "removeTrack",
                    "index": index + 1,
                    "track": removed.track_id,
                    "requesterId": self.id,
                })
        except Exception:
            pass

    async def _handle_move_track(self, payload: Dict):
        old_index = payload.get("index", 0) - 1
        new_index = payload.get("newIndex", 0) - 1
        try:
            q = self.player.queue._queue
            pos = self.player.queue._position
            target_old = pos + old_index
            target_new = pos + new_index
            if pos <= target_old < len(q) and pos <= target_new < len(q):
                track = q.pop(target_old)
                q.insert(target_new, track)
                await self.send_player_state()
        except Exception:
            pass

    async def _handle_clear_queue(self, payload: Dict):
        if hasattr(self.player.queue, 'clear'):
            self.player.queue.clear()
        else:
            self.player.queue._queue.clear()
        await self.send_player_state()

    # ── State Sync ───────────────────────────────────────────────

    async def _handle_toggle_autoplay(self, payload: Dict):
        status = payload.get("status", False)
        self.player.autoplay = status
        self.player.autoplay_count = 0 # Reset count when toggled
        await self.send({
            "op": "toggleAutoplay",
            "status": status,
            "requesterId": self.id
        })
        await self.send({
            "op": "toast",
            "type": "success",
            "msg": f"Autoplay {'enabled' if status else 'disabled'}"
        })

    async def send_player_state(self) -> None:
        """Send the full current player state to the client."""
        if not self.player:
            payload = {
                "op": "initPlayer",
                "guildId": self.id,
                "tracks": [],
                "currentQueuePosition": 0,
                "isPaused": True,
                "currentPosition": 0,
                "volume": 100,
                "repeatMode": "off",
                "autoplay": getattr(self.player, 'autoplay', False) if self.player else False,
                "isDj": True,
                "channelName": "Web Player",
                "users": [{"userId": self.id, "name": self.name, "avatarUrl": self.avatar_url}],
                "availableFilters": [],
                "filters": [],
            }
            await self.send(payload)
            return

        queue_tracks = []
        if hasattr(self.player.queue, '_queue'):
            queue_tracks = list(self.player.queue._queue)
        
        # Build track list including current track at front
        all_tracks = []
        if self.player.current:
            all_tracks.append({
                "trackId": self.player.current.track_id,
                "requesterId": self.id,
            })
        for t in queue_tracks:
            all_tracks.append({
                "trackId": t.track_id,
                "requesterId": self.id,
            })

        payload = {
            "op": "initPlayer",
            "guildId": self.id,
            "tracks": all_tracks,
            "currentQueuePosition": 1 if self.player.current else 0,
            "isPaused": self.player.is_paused,
            "currentPosition": self.player.position,
            "volume": self.player.volume,
            "repeatMode": getattr(self.player.queue, 'repeat', 'off'),
            "autoplay": getattr(self.player, 'autoplay', False),
            "isDj": True,
            "channelName": "Web Player",
            "users": [{"userId": self.id, "name": self.name, "avatarUrl": self.avatar_url}],
            "availableFilters": [],
            "filters": [],
        }
        await self.send(payload)

    # ── Connection Management ────────────────────────────────────

    async def connect(self, websocket: quart.Websocket) -> None:
        if self._websocket:
            await self.disconnect()

        self._websocket = websocket
        LOGGER.info(f"User {self.name}({self.id}) connected.")
        received = asyncio.create_task(self._listen())
        await asyncio.gather(received)

    async def disconnect(self) -> None:
        if self._websocket:
            self._websocket = None
            LOGGER.info(f"User {self.name}({self.id}) disconnected.")

    @property
    def is_connected(self) -> bool:
        return self._websocket is not None

    @property
    def language_code(self) -> str:
        try:
            language = get_official_languages(self.country.iso_code if self.country else "US")
            return language[0] if language and language[0] in LANGUAGES else list(LANGUAGES.keys())[0]
        except Exception:
            return list(LANGUAGES.keys())[0] if LANGUAGES else "en"

    def __repr__(self) -> str:
        return f"<User id={self.id} name={self.name}>"


class UserPool:
    """Pool of active user sessions."""
    _users: Dict[str, User] = {}

    @classmethod
    def add(cls, data: Dict) -> User:
        user = User(cls, data)
        cls._users[user.id] = user
        return user

    @classmethod
    def get(cls, *, user_id: str = None) -> Optional[User]:
        if user_id:
            return cls._users.get(user_id)
        return None


class Settings:
    def __init__(self, settings_file: str = "settings.json"):
        self.settings_file = settings_file
        self.settings = self.load()

        self.host: str = self.get_setting("host") or os.getenv("HOST", "0.0.0.0")
        self.port: int = self.get_setting("port") or os.getenv("PORT", 5000)
        self.secret_key: str = self.get_setting("secret_key") or os.getenv("SECRET_KEY", "standalone_music_secret")

        self.logging: Dict[str, Any] = self.get_setting("logging")

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.settings.get(key, default)

    def load(self) -> Dict:
        try:
            with open(self.settings_file, "r") as file:
                return json.load(file)
        except FileNotFoundError as e:
            LOGGER.error(f"Unable to load the settings file.", exc_info=e)
            return {}
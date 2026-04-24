import asyncio
import os
import functools
import update

from dotenv import load_dotenv

from hypercorn import Config
from hypercorn.asyncio import serve
from babel import Locale

from quart_babel import Babel
from quart import (
    Quart,
    render_template,
    redirect,
    url_for,
    jsonify,
    session,
    websocket,
    request
)

from objects import (
    Settings,
    UserPool,
    User
)

from utils import (
    ROOT_DIR,
    LANGUAGES,
    get_locale,
    process_js_files,
    compile_scss,
    download_geoip_db,
    check_country_with_ip,
    setup_logging,
    LOGGER
)

from voicelink import NodePool

SETTINGS: Settings = Settings()

app = Quart(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.secret_key = SETTINGS.secret_key

babel = Babel(app)
babel.init_app(app, locale_selector=get_locale)

load_dotenv()


def login_required(func):
    """Decorator that auto-creates guest sessions for unauthenticated visitors."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            user_id = str(os.urandom(8).hex())
            session["user_id"] = user_id

        user = UserPool.get(user_id=user_id)
        if not user:
            user = UserPool.add({"id": user_id, "name": f"Guest_{user_id[:4]}"})

        return await func(user, *args, **kwargs)
    return wrapper


@app.before_serving
async def setup():
    try:
        lang_codes = ["en"] + [
            lang for lang in os.listdir(os.path.join(ROOT_DIR, "translations"))
            if not lang.startswith(".")
        ]
        for lang_code in lang_codes:
            LANGUAGES[lang_code] = {"name": Locale.parse(lang_code).get_display_name(lang_code).capitalize()}

        process_js_files()
        compile_scss()
        await download_geoip_db()

        # Initialize Lavalink Node
        node = await NodePool.create_node(
            host="193.226.78.187",
            port=4036,
            password="titli",
            identifier="love",
            user_id="1234567890", # Use numeric user_id for Lavalink compatibility
            secure=False
        )
        # Wait for node to be connected (max 10 seconds)
        for i in range(10):
            if node.is_connected:
                LOGGER.info(f"Lavalink node connected after {i} seconds.")
                break
            await asyncio.sleep(1)
        else:
            LOGGER.warning("Lavalink node failed to connect within 10 seconds.")
    except Exception as e:
        LOGGER.error(f"Error during initialization setup: {e}")
        # On Render, we might want to continue even if GeoIP fails


@app.route("/health", methods=["GET"])
async def health():
    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
@login_required
async def home(user: User):
    forwarded_for = request.headers.get('X-Forwarded-For')
    user_ip = forwarded_for.split(',')[0] if forwarded_for else request.remote_addr
    country = await check_country_with_ip(user_ip)
    user.country = country

    return await render_template("index.html", user=user, languages=LANGUAGES)


@app.route('/logout', methods=["GET"])
@login_required
async def logout(user: User):
    session.pop("user_id", None)
    return redirect(url_for("home"))


@app.route('/language/<language>')
@login_required
async def set_language(user: User, language=None):
    if language in LANGUAGES:
        session["language_code"] = language
    return redirect(url_for('home'))


@app.websocket("/ws_user")
@login_required
async def ws_user(user: User):
    try:
        await user.connect(websocket._get_current_object())
    except asyncio.CancelledError:
        raise


if __name__ == "__main__":
    # update.check_version(with_msg=True)
    setup_logging(SETTINGS.logging)
    config = Config()
    
    # Use environment variables for Render deployment compatibility
    host = os.environ.get("HOST", SETTINGS.host)
    port = int(os.environ.get("PORT", SETTINGS.port))
    
    config.bind = [f"{host}:{port}"]
    asyncio.run(serve(app, config))
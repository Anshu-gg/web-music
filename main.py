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
        # Initialize MongoDB with a timeout to prevent hanging
        mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        try:
            from voicelink.mongodb import MongoDBHandler
            # Use a shorter server selection timeout for faster failure
            await asyncio.wait_for(
                MongoDBHandler.init(uri=mongo_uri, db_name="titli_music"),
                timeout=10.0
            )
            LOGGER.info("MongoDB initialized successfully.")
        except asyncio.TimeoutError:
            LOGGER.error("MongoDB initialization timed out after 10 seconds.")
        except Exception as e:
            LOGGER.error(f"Failed to initialize MongoDB: {e}")

        # Basic setup that must be sync/fast
        lang_codes = ["en"]
        translations_path = os.path.join(ROOT_DIR, "translations")
        if os.path.exists(translations_path):
            try:
                lang_codes += [
                    lang for lang in os.listdir(translations_path)
                    if not lang.startswith(".")
                ]
            except Exception as e:
                LOGGER.error(f"Error listing translations: {e}")
        
        for lang_code in lang_codes:
            try:
                LANGUAGES[lang_code] = {"name": Locale.parse(lang_code).get_display_name(lang_code).capitalize()}
            except:
                LANGUAGES[lang_code] = {"name": lang_code}

        # Run heavy tasks in background to avoid blocking port binding
        asyncio.create_task(background_setup())

    except Exception as e:
        LOGGER.error(f"Critical error during initialization setup: {e}")

async def background_setup():
    """Heavy initialization tasks that can run after port binding."""
    try:
        # Static files processing in a thread
        try:
            await asyncio.to_thread(process_js_files)
            await asyncio.to_thread(compile_scss)
            LOGGER.info("Static files processed in background.")
        except Exception as e:
            LOGGER.error(f"Error processing static files in background: {e}")

        # GeoIP download
        try:
            await download_geoip_db()
        except Exception as e:
            LOGGER.error(f"Error downloading GeoIP: {e}")

        # Initialize Lavalink Node
        try:
            node = await NodePool.create_node(
                host="193.226.78.187",
                port=4036,
                password="titli",
                identifier="love",
                user_id="1234567890",
                secure=False
            )
            # Wait for node to be connected
            for i in range(10):
                if node.is_connected:
                    LOGGER.info(f"Lavalink node connected in background after {i} seconds.")
                    break
                await asyncio.sleep(1)
        except Exception as e:
            LOGGER.error(f"Error creating Lavalink node in background: {e}")
    except Exception as e:
        LOGGER.error(f"Error in background setup: {e}")


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
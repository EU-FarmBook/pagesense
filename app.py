from __future__ import annotations

from pagesense import create_app


app = create_app()


if __name__ == "__main__":
    config = app.extensions["pagesense_config"]
    app.run(
        host=config.host,
        port=config.port,
        debug=config.debug,
        use_reloader=config.auto_reload,
        load_dotenv=False,
    )

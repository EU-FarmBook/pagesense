from __future__ import annotations

from flask import Flask

from pagesense.config import AppConfig, load_config
from pagesense.routes.api import api_bp
from pagesense.routes.web import web_bp
from pagesense.services.request_logs import init_request_log_db


def create_app(config_overrides: dict[str, object] | None = None) -> Flask:
    config = load_config()
    if config_overrides:
        config = config.with_overrides(**config_overrides)
    app = Flask(
        __name__,
        template_folder=str(config.base_dir / "templates"),
    )
    app.extensions["pagesense_config"] = config

    try:
        from flask_cors import CORS

        CORS(app, resources={r"/api/*": {"origins": "*"}})
    except Exception:
        pass

    init_request_log_db(config)
    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)
    return app

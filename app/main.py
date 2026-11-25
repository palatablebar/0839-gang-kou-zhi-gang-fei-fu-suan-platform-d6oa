import os
from pathlib import Path

from flask import Flask, jsonify, render_template
from werkzeug.exceptions import RequestEntityTooLarge

from app.catalog import SPEC
from app.config import MAX_FILE, ROOT
from app.errors import DomainIssue, error_response
from app.review.store import Store
from app.routes import api
from app.settlement.api import settlement
from app.settlement.seed import payload as settlement_seed
from app.settlement.store import SettlementStore


def create_app(database=None, settlement_database=None, seed=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE + 64 * 1024
    default_db = str(ROOT / "instance" / "workbench.sqlite3")
    database = database or os.getenv("WORKBENCH_DB", default_db)
    app.extensions["runs"] = Store(database)
    if settlement_database is None:
        settlement_database = os.getenv("WORKBENCH_SETTLEMENT_DB")
    if settlement_database is None:
        parent = ROOT / "instance" if database == ":memory:" else Path(database).parent
        settlement_database = str(parent / "settlement.sqlite3")
    app.extensions["settlement"] = SettlementStore(settlement_database)
    app.register_error_handler(DomainIssue, error_response)
    app.register_error_handler(RequestEntityTooLarge, lambda _: error_response(DomainIssue("FILE_TOO_LARGE", 413)))
    app.register_blueprint(api)
    app.register_blueprint(settlement)
    if seed is None:
        seed = os.getenv("WORKBENCH_AUTOSEED", "1") != "0"
    if seed and not app.extensions["settlement"].snapshot()["bills"]:
        app.extensions["settlement"].seed(settlement_seed())

    @app.get("/")
    def home():
        return render_template("index.html", p=SPEC)

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    return app


app = create_app()

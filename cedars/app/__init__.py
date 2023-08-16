"""
Entrypoint for the flask application.
"""

__version__ = "0.1.0"
__author__ = "Rohan Singh"

import os
from flask_login import login_required, current_user
from flask import Flask, redirect, url_for, render_template, session

from flask_pymongo import PyMongo
from faker import Faker

mongo = PyMongo()
fake = Faker()


def create_app(config_filename=None):
    """Create flask application"""
    app = Flask(__name__, instance_path=os.path.join(os.path.dirname(__file__), "static"))
    if config_filename:
        app.config.from_object(config_filename)
    app.config["UPLOAD_FOLDER"] = os.path.join(app.instance_path)
    mongo.init_app(app)

    from . import db
    db.create_project(project_name=fake.slug(),
                      investigator_name=fake.name(),
                      cedars_version=__version__)

    from . import auth
    auth.login_manager.init_app(app)
    app.register_blueprint(auth.bp)

    from . import ops
    app.register_blueprint(ops.bp)

    from . import stats
    app.register_blueprint(stats.bp)

    @app.route('/', methods=["GET"])
    @login_required
    def homepage():
        return render_template('index.html', **db.get_info())

    return app

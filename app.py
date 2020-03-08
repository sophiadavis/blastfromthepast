from authlib.integrations.flask_client import OAuth
import datetime
from flask import Flask, flash, abort, get_flashed_messages, request, redirect, render_template, url_for, send_from_directory
from flask_login import LoginManager, login_user, login_required, current_user
import logging
from loginpass import Google, create_flask_blueprint
import json
import os
from werkzeug.utils import secure_filename
import redis
import time

import sys
sys.stdout = sys.stderr

## todo 
# clean up old redis keys?
# check if image already present
# if any single file fails upload, don't break the others

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                    filename=os.path.join(os.path.dirname(os.path.realpath(__file__)), 'log', f'{datetime.date.today()}-uploadserver.log'),
                    filemode='a')

ALLOWED_EXTENSIONS = set(['pdf', 'png', 'jpg', 'jpeg', 'pjpeg'])

app = Flask(__name__)
app.config.from_pyfile('flask_config.py')


current_user = 'sophia'


login_manager = LoginManager()
# https://stackoverflow.com/questions/16693653/how-to-add-or-change-return-uri-in-google-console-for-oauth2
login_manager.login_view = '/google/login'
login_manager.init_app(app)


oauth = OAuth(app, {})
redis_client = redis.Redis()


# Upload logic: http://flask.pocoo.org/docs/1.0/patterns/fileuploads/
# Authorization logic: https://github.com/authlib/loginpass/tree/master/flask_example


class User:
    def __init__(self, token, user_info):
        self.email = user_info.get('email')
        self.email_verified = user_info.get('email_verified')
        self.token = token.get('access_token')

    def is_authenticated(self):
        return self.email in app.config['ALLOWED_USERS'] and self.email_verified and self.token

    def is_active(self):
        return True

    def is_anonymous(self):
        return False

    def get_id(self):
        return self.email


def _get_uniquified_name(filename, email):
    base, extension = filename.rsplit('.', 1)
    email_id = email.split('@')[0]
    timestamp = int(time.time())
    return f'{base}-{email_id}-{timestamp}.{extension}'


def handle_authorize(remote, token, user_info):
    app.logger.info('User authorization request')
    user = User(token, user_info)
    if user.is_authenticated():
        app.logger.info(f'Saving user {user.email}')
        redis_client.set(user.get_id(), json.dumps({'token': token, 'user_info': user_info}))
        login_user(user)
        return redirect(url_for('upload_file'))
    return abort(401)


bp = create_flask_blueprint(Google, oauth, handle_authorize)
app.register_blueprint(bp, url_prefix='/google')


@login_manager.user_loader
def load_user(user_id):
    app.logger.info('Loading user...')
    existing_user = redis_client.get(user_id)
    if existing_user:
        app.logger.info(f'Load user: {user_id} in cache!')
        cached_info = json.loads(existing_user)
        return User(cached_info['token'], cached_info['user_info'])
    app.logger.info(f'Load user: {user_id} NOT in cache!')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.errorhandler(404)
def page_not_found(e):
    if not current_user.is_authenticated:
        return login_manager.unauthorized()


@app.route('/', methods=['GET', 'POST'])
#@login_required
def upload_file():
    if request.method == 'POST':
        photos = request.files
        saved_files = []
        for photo in request.files.getlist('image_uploads'):
            if photo and allowed_file(photo.filename):
                app.logger.info(f'{current_user}: submitted {photo.filename}')
                filename = secure_filename(photo.filename)
                uniquified_name = _get_uniquified_name(filename, current_user)
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], uniquified_name))
                app.logger.info(f'{current_user}: submitted {photo.filename} ; save successful')
                saved_files.append(uniquified_name)
            else:
                app.logger.info(f'{current_user}: submitted {photo}, skipping')
        save_key = f'{current_user}-{time.time()}'
        redis_client.set(save_key, json.dumps([f for f in saved_files]))
        redis_client.expire(save_key, 60*60*24*7)
        return redirect(url_for('success', save_key=save_key))
    return render_template('upload.html', font_awesome_cdn=app.config['FONT_AWESOME_CDN'])


@app.route('/success/<save_key>')
#@login_required
def success(save_key):
    uploaded_files = json.loads(redis_client.get(save_key))
    links_to_uploads = [url_for('uploaded_file', filename=f) for f in uploaded_files]
    app.logger.info(links_to_uploads)
    upload_url = url_for('upload_file')
    return render_template('success.html', uploads=links_to_uploads, upload_url=upload_url)


@app.route('/privacy')
# @login_required
def privacy():
    return 'Everything on this site is private.'


@app.route('/uploads/<filename>')
# @login_required
def uploaded_file(filename):
    if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    return abort(404)


@app.route('/favicon.ico')
# @login_required
def favicon():
    return ''

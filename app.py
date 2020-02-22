from authlib.flask.client import OAuth
import datetime
from flask import Flask, flash, abort, get_flashed_messages, request, redirect, url_for, send_from_directory
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

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                    filename=os.path.join(os.path.dirname(os.path.realpath(__file__)), 'log', f'{datetime.date.today()}-uploadserver.log'),
                    filemode='a')

ALLOWED_EXTENSIONS = set(['pdf', 'png', 'jpg', 'jpeg', 'gif'])

html_head = '''
<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<head>
<script type="text/javascript">
  function checkFileSize(event) {
    var uploadSelection = document.getElementById("photos");
    if (uploadSelection.files.length > 0) {
      var totalSize = 0;
      for (var i = 0; i < uploadSelection.files.length; i++) {
        totalSize += uploadSelection.files[i].size;
      }
      if (totalSize > 1000**3) {
        // nginx client_max_body_size is set to 1000M
        alert('Uploads must be less than 1Gb. Your files are: '+ (totalSize / 1000**3) + ' Gb');
        return false;
      }
    }
  return true;
  }
</script>
</head>
'''
html_body_template = '''
<body style="background-color:steelblue; font-family:Courier New, monospace; text-align:center;">
{content}
</body>
'''

app = Flask(__name__)
app.config.from_pyfile('flask_config.py')


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
    base, extension = filename.split('.')
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
@login_required
def upload_file():
    if request.method == 'POST':
        if 'photos' not in request.files:
            flash('Please select some photos')
            app.logger.info(f'{current_user.email}: submission did not include a file')
            return redirect(request.url)
        photos = request.files
        for photo in request.files.getlist('photos'):
            if photo.filename == '':
                flash('Please select some photos')
                app.logger.info(f'{current_user.email}: filename was empty')
                return redirect(request.url)
            if photo and allowed_file(photo.filename):
                app.logger.info(f'{current_user.email}: submitted {photo.filename}')
                filename = secure_filename(photo.filename)
                uniquified_name = _get_uniquified_name(filename, current_user.email)
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], uniquified_name))
                app.logger.info(f'{current_user.email}: submitted {photo.filename} ; save successful')
        return redirect(url_for('success', filename=uniquified_name))
    messages = [m for m in get_flashed_messages() if not m.startswith('Please log in')]
    message_to_show = ''
    if messages:
        message_to_show = messages[0]
    content = f'''
    <title>Upload new photos</title>
    <h1>Upload new photos</h1>
    <h4>**headsup, 1Gb max upload size**</h4>
    <div>{message_to_show}</div>
    <form method="POST" enctype="multipart/form-data" onsubmit="return checkFileSize(event)" id="uploadForm">
      <input type="file" name="photos" accept="image/*" id="photos" multiple>
      <input type="submit" value="Click to upload!">
    </form>
    '''
    return html_head + html_body_template.format(content=content)


@app.route('/success/<filename>')
@login_required
def success(filename):
    uploaded_path = url_for('uploaded_file', filename=filename)
    upload_url = url_for('upload_file')
    content = f'''
    <title>Thanks for submitting!</title>
    <h1>Submissions successful.</h1>
    <img src="{uploaded_path}" alt="Yay!" height="250">
    <h1><a href="{upload_url}">Submit more!</a></h1>
    '''
    return html_head + html_body_template.format(content=content)


@app.route('/privacy')
# @login_required
def privacy():
    return 'Everything on this site is private.'


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    return abort(404)


@app.route('/favicon.ico')
@login_required
def favicon():
    return send_from_directory(app.config['FAVICON'], 'calvin-favicon.jpg')

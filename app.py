from authlib.flask.client import OAuth
import datetime
from flask import Flask, flash, abort, get_flashed_messages, request, redirect, render_template, url_for, send_from_directory
from flask_login import LoginManager, login_user, login_required, current_user
import logging
from loginpass import Google, create_flask_blueprint
import hashlib
import imagehash
import io
import json
import os
from PIL import Image
import psycopg2
from psycopg2 import extras
from werkzeug.utils import secure_filename
import redis
import time

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                    filename=os.path.join(os.path.dirname(os.path.realpath(__file__)), 'log', f'{datetime.date.today()}-uploadserver.log'),
                    filemode='a')

ALLOWED_EXTENSIONS = set(['pdf', 'png', 'jpg', 'jpeg', 'pjpeg'])

app = Flask(__name__)
app.config.from_pyfile('flask_config.py')

UPLOAD_FOLDER = app.config['UPLOAD_FOLDER']
THUMB_FOLDER = app.config['THUMB_FOLDER']

BLAST = app.config['BLAST']
DBPARAMS = {
    'host': app.config['DBPARAMS_HOST'],
    'dbname': app.config['DBPARAMS_DBNAME'],
    'user': app.config['DBPARAMS_USER'],
    'password': app.config['DBPARAMS_PASSWORD'],
}
PHOTOS_TABLE = app.config['DBPARAMS_TABLE']


login_manager = LoginManager()
login_manager.login_view = '/google/login'
login_manager.init_app(app)


oauth = OAuth(app, {})
redis_client = redis.Redis()


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


def _get_uniquified_name(filename, user_id):
    base, extension = filename.rsplit('.', 1)
    timestamp = int(time.time())
    return f'{base}-{user_id}-{timestamp}.{extension}'


def _filter_flash(messages):
    return [m for m in messages if 'log in' not in m]


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

user_info = json.loads(redis_client.get('scdgrapefruit@gmail.com'))
current_user = User(user_info['token'], user_info['user_info'])


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

def register_files(saved_files, user_id):
    to_insert = []
    for saved_file in saved_files:
        path = os.path.join(UPLOAD_FOLDER, saved_file)
        image = Image.open(path)
        with open(path, 'rb') as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        try:
            thumbpath = os.path.join(THUMB_FOLDER, f'thumb-{saved_file}')
            image.thumbnail((128, 128), Image.ANTIALIAS)
            image.save(thumbpath, "JPEG")
            app.logger.info("Saved thumbnail to '%s'", path)
        except IOError as e:
            app.logger.error("Cannot create thumbnail for '%s', %s", path, e)
        to_insert.append((saved_file, 
                                None, thumbpath, md5, 
                                _to_bitstring(imagehash.phash(image)), 
                                _to_bitstring(imagehash.dhash(image)), 
                                _to_bitstring(imagehash.average_hash(image)), 
                                user_id, datetime.date.today(), BLAST))
    

    app.logger.info('Inserting %s entries', len(to_insert))
    with psycopg2.connect(**DBPARAMS) as conn:
        with conn.cursor(cursor_factory=extras.DictCursor) as cursor:
            cursor.executemany(f"""
                INSERT INTO {PHOTOS_TABLE} (         
                    name,       
                    sent_date, 
                    thumb_path,       
                    md5,     
                    phash,      
                    dhash,      
                    ahash,     
                    upload_by,  
                    upload_date,
                    blast
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, to_insert)
    app.logger.info('Inserted %s entries', len(to_insert))


@app.route('/', methods=['GET', 'POST'])
@login_required
def upload_file():
    user_id = current_user.email.split('@')[0]
    if request.method == 'POST':
        photos = request.files
        saved_files = []
        for photo in request.files.getlist('image_uploads'):
            if photo and allowed_file(photo.filename):
                try:
                    app.logger.info(f'{user_id}: submitted {photo.filename}')
                    filename = secure_filename(photo.filename)
                    uniquified_name = _get_uniquified_name(filename, user_id)
                    photo.save(os.path.join(UPLOAD_FOLDER, uniquified_name))
                    app.logger.info(f'{user_id}: submitted {photo.filename} ; save successful')
                    saved_files.append(uniquified_name)
                except Exception as e:
                    message = f'{user_id}: failed to save {photo.filename}'
                    flash(message)
                    app.logger.exception(message)
            else:
                app.logger.info(f'{user_id}: submitted {photo}, skipping')
        save_key = f'{user_id}-{time.time()}'
        redis_client.set(save_key, json.dumps([f for f in saved_files]))
        redis_client.expire(save_key, 60*60*24*7)
        register_files(saved_files, user_id)
        return redirect(url_for('success', save_key=save_key))
    return render_template('upload.html', font_awesome_cdn=app.config['FONT_AWESOME_CDN'])


@app.route('/success/<save_key>')
@login_required
def success(save_key):
    uploaded_files = json.loads(redis_client.get(save_key))
    links_to_uploads = [url_for('uploaded_file', filename=f) for f in uploaded_files]
    app.logger.debug(links_to_uploads)
    upload_url = url_for('upload_file')
    return render_template('success.html', messages=_filter_flash(get_flashed_messages()),
                           uploads=links_to_uploads, upload_url=upload_url)


def _to_bitstring(imagehash_obj):
    return ''.join(str(b) for b in 1 * imagehash_obj.hash.flatten())


@app.route('/check', methods=['POST'])
@login_required
def check_perceptually_similar():
    app.logger.info('Checking %s upload against previous uploads', current_user.email.split('@')[0])
    content = request.get_json(force=True)['file_content']
    app.logger.info('Getting content')
    bytes_ = bytes.fromhex(content)
    app.logger.info('Getting bytes')
    similar_files = []

    image = Image.open(io.BytesIO(bytes_))
    perceptual_hashes = {
        'dhash': _to_bitstring(imagehash.dhash(image)),
        'ahash': _to_bitstring(imagehash.average_hash(image)),
        'phash': _to_bitstring(imagehash.phash(image)),
    }

    with psycopg2.connect(**DBPARAMS) as conn:
        with conn.cursor(cursor_factory=extras.DictCursor) as cursor:
            for colname in perceptual_hashes.keys():
                app.logger.info('Finding photos with similar %s', colname)
                hash_value = perceptual_hashes[colname]
                # sort by hamming_distance == the number of bits in two hashes that are different
                # length(replace((dhash # %s::bit(64))::text, '0', ''))
                # get xor of the hashes: dhash # %s::bit(64)
                # convert to text, remove the 0's -- these bits are the same
                # count remaining 1's
                cursor.execute(f"""
                    select 
                        name, thumb_path, 
                        length(replace(({colname} # %s::bit(64))::text, '0', '')) as hamming_distance 
                    from {PHOTOS_TABLE} 
                    where 
                        blast = %s and 
                        length(replace(({colname} # %s::bit(64))::text, '0', '')) < 15
                """, (hash_value, BLAST, hash_value,))
                results = list(cursor.fetchall())
                app.logger.info('Submitted data %s: %s -- matched %s existing photos', colname, hash_value, len(results))
                for res in results:
                    app.logger.info('%s: %s -- distance of %s', res['name'], colname, res['hamming_distance'])
                similar_files.extend(results)
    unique_files = set((f['name'], f['thumb_path'], f['hamming_distance'],) for f in similar_files)
    return json.dumps({
        'similar': [
            url_for('uploaded_file', 
                filename=f[0], 
                thumb_path=f[1]
            )
            for f in sorted(unique_files, key=lambda f: f[2])
        ][:5]
    })


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    thumb_path = request.args.get('thumb_path', None) 
    if thumb_path:
        return send_from_directory(THUMB_FOLDER, os.path.basename(thumb_path))
    elif os.path.exists(os.path.join(UPLOAD_FOLDER, filename)):
        return send_from_directory(UPLOAD_FOLDER, filename)
    return abort(404)


@app.route('/favicon.ico')
@login_required
def favicon():
    return send_from_directory(app.config['FAVICON'], 'favicon.jpg')

# BlastFromThePast

A web frontend for "users" to upload favorite photos, and a script for distributing these photos via email


### Background
I live far away from my family, and this is one way that we keep in touch. We can all submit photos from times we spent together in the past, and one of these pictures gets emailed daily to all of us. It's not the most gorgeous thing ever, but it works and I like it :)


### Setup
#### app.py
A flask app, which uses Oauth+Google for authentication (and redis for session management), and is served with uwsgi+nginx (SSL only, thanks to [certbot](https://certbot.eff.org)), & managed by systemd

##### Suggested config:
```
### flask_app.py
FLASK_ENV = 'production'
SECRET_KEY = 'FLASK_SECRET_KEY'
GOOGLE_CLIENT_ID = 'YOUR_GOOGLE_APP_OAUTH_CLIENT_ID'
GOOGLE_CLIENT_SECRET = 'YOUR_GOOGLE_APP_OAUTH_CLIENT_SECRET'

UPLOAD_FOLDER = '/absolute/path/to/photo/directory'
FAVICON = '/absolute/path/to/favicon/directory/to/avoid/endless/404s/in/logs'  # Put a file there & change the code (I use 'calvin-favicon.jpg', hackity hack)

ALLOWED_USERS = ['mom@gmail.com', 'dad@gmail.com', ...]  # Only Gmail addresses on this list will be allowed to authenticate

REDIS_URL = "redis://localhost:6379/0"  # Address of local redis server for session management -- follow simple redis installation instructions & you should be good (i.e. https://www.digitalocean.com/community/tutorials/how-to-install-and-secure-redis-on-ubuntu-18-04)
```
\+ generic systemd config file for uwsgi, uwsgi config, nginx config, SSL certificates

#### distribute.py
A python script for selecting a photo at random, compressing it if necessary, and emailing to all your friends and family

##### Usage:
```
./distribute.py --help
usage: distribute.py [-h] [--dryrun] [--photo PHOTO]

Email a randomly selected photo to a group of people

optional arguments:
  -h, --help     show this help message and exit
  --dryrun       Email "dryrun" addresses, do not move the photo to the "sent" folder
  --photo PHOTO  Send photo at this path, instead of a random selection
 ```
##### Suggested config:
```
### config.ini
[google_api]
app_name = your_google_app_name

[photos]
upload_dir = '/absolute/path/to/photo/directory/from/upload/server'
sent_images_dir = '/absolute/path/to/sent/photo/directory'

[distribution]
sender_email = youremail@domain.com
recipients = mom@domain.com, dad@domain.com, brother@domain.com...
dryrun_recipients = probablyyouremail@domain.com
subject = Blast from the Past!

[app]
upload_url = your-domain-hosting-the-upload-server.com
```

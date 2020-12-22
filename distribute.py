#!/usr/bin/env python

from apiclient import discovery
import argparse
import base64
import configparser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import httplib2
from jinja2 import Template
import logging
from oauth2client import client, tools, file
import os
import os.path
from PIL import Image
import random
import datetime

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                    filename=os.path.join(THIS_DIR, 'log', f'{datetime.date.today()}-distribute.log'),
                    filemode='a')

logger = logging.getLogger('distribute')

SCOPES = 'https://www.googleapis.com/auth/gmail.send'
CLIENT_SECRET_GENERATED = os.path.join(THIS_DIR, 'client_secret.json')
CLIENT_SECRET_ORIG = os.path.join(THIS_DIR, 'client_secret_orig.json')
GOOGLE_API_SIZE_CUTOFF_MB = 5

EMAIL_HTML_BODY = Template("""
<html>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <head></head>
  <body style="background-color:steelblue; font-family:Courier New, monospace">
    <h1>Here is your photo blast of the day!</h1>
    <h3>Go to <a href="{{ url }}/">{{ url }}</a> to submit more pictures!</h3>
  </body>
</html>
""")


def format_image_attachment(attachment):
    image = Image.open(attachment)

    size_mb = os.stat(attachment).st_size / 10**6
    logger.debug(f'- Size is {size_mb} mb')
    compression_quality = 99
    while size_mb > GOOGLE_API_SIZE_CUTOFF_MB:
        logger.debug(f'- Compressing to {compression_quality}')
        image.save(attachment, quality=compression_quality, optimize=True)
        size_mb = os.stat(attachment).st_size / 10**6
        logger.debug(f'- Size is now {size_mb} mb')
        compression_quality -= 1

    subtype = image.get_format_mimetype()
    logger.debug(f'- Using {subtype} as subtype')

    with open(attachment, 'rb') as fp:
        mime_image = MIMEImage(fp.read(), _subtype=subtype)

    return mime_image


def create_message_with_image_attachment(sender, to, subject, attachment, html_body):
    """Create a message for an email.

    Args:
        sender: Email address of the sender.
        to: Email address of the receiver.
        subject: The subject of the email message.
        attachment: The path to the image to be attached.
        html_body: The text of the email message.

    Returns:
        An object containing a base64url encoded email object.
    """
    message = MIMEMultipart()
    message['to'] = ', '.join(to)
    message['from'] = sender
    message['subject'] = subject

    body = MIMEText(html_body, 'html')
    logger.debug('- attaching body...')
    message.attach(body)

    logger.debug('- attaching image file...')
    filename = os.path.basename(attachment)
    image = format_image_attachment(attachment)
    image.add_header('Content-Disposition', 'attachment', filename=filename)
    message.attach(image)
    logger.debug('- base64 encoding message...')
    encoded_message = base64.urlsafe_b64encode(message.as_bytes())
    return {
        'raw': encoded_message.decode("utf-8") # convert to str so google can jsonify it
    }


def send_message(service, user_id, message):
    """Send an email message.

    Args:
        service: Authorized Gmail API service instance.
        user_id: User's email address. The special value "me"
        can be used to indicate the authenticated user.
        message: Message to be sent.

    Returns:
        Sent Message.
    """
    logger.debug('Getting message service...')
    message = (service.users().messages().send(userId=user_id, body=message).execute())
    logger.debug(f'Message Id: {message["id"]}\n')
    return message


def get_service(app_name):
    store = file.Storage(CLIENT_SECRET_GENERATED)
    credentials = store.get()
    if not credentials or credentials.invalid:
        flow = client.flow_from_clientsecrets(CLIENT_SECRET_ORIG, SCOPES)
        flow.user_agent = app_name
        credentials = tools.run_flow(flow, store)
    http = credentials.authorize(httplib2.Http())
    service = discovery.build('gmail', 'v1', http=http, cache_discovery=False)
    return service


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Email a randomly selected photo to a group of people')
    parser.add_argument('--dryrun', action='store_true', help='Email a "dryrun" address, do not move the photo to the "sent" folder')
    parser.add_argument('--photo', help='Send photo at this path, instead of a random selection')
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(os.path.join(THIS_DIR, 'config.ini'))

    upload_dir = config['photos']['upload_dir']
    sent_images_dir = config['photos']['sent_images_dir']
    recipients_key = 'dryrun_recipients' if args.dryrun else 'recipients'
    recipients = config['distribution'][recipients_key].split(',')
    sender_email = config['distribution']['sender_email']
    subject = config['distribution']['subject']
    upload_url = config['app']['upload_url']
    app_name = config['google_api']['app_name']

    pictures = os.listdir(upload_dir)
    if pictures:
        picture_path = os.path.join(upload_dir, random.choice(pictures))
    else:
        pictures = os.listdir(sent_images_dir)
        picture_path = os.path.join(sent_images_dir, random.choice(pictures))

    if args.photo:
        assert os.path.exists(args.photo), f'{args.photo} does not point to a file.'
        picture_path = args.photo
    logger.info(f"Will distribute {picture_path}")

    service = get_service(app_name)

    logger.info(f"Creating email for {recipients}")
    html_body = EMAIL_HTML_BODY.render(url=upload_url)
    msg = create_message_with_image_attachment(sender_email, recipients, subject, picture_path, html_body)

    logger.info("Sending email...")
    send_message(service, "me", msg)
    logger.info("Sent.")

    if not args.dryrun:
        archived_picture_path = os.path.join(sent_images_dir, os.path.basename(picture_path))
        os.rename(picture_path, archived_picture_path)
        logger.info(f"Moved file {picture_path} to {archived_picture_path}")
    else:
        logger.info(f"Dryrun -- not removing {picture_path}")

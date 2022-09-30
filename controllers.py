# -*- coding: utf-8 -*-

import logging
import os
import werkzeug
from base64 import b64decode
from openerp.addons.web.http import Controller, httprequest
from openerp.addons.web.controllers.main import content_disposition
from mimetypes import guess_type
from scription import OrmFile

CONFIG = '/%s/config/fnx.ini' % os.environ['VIRTUAL_ENV']
settings = OrmFile(CONFIG).openerp
db = settings.db
login = settings.user
password = settings.pw

_logger = logging.getLogger(__name__)


class Wiki(Controller):

    _cp_path = '/wiki'

    @httprequest
    def image(self, request, model, img_id, **kw):
        session = request.session
        session._db = db
        session._login = login
        session._uid = 1
        session._password = password
        img_id = int(img_id)
        Model = request.session.model(model)
        page = Model.read([img_id], ['name', 'source_img'], request.context)[0] 
        image_name = page['name']
        image = b64decode(page['source_img'])
        try:
            return request.make_response(
                    image,
                    headers=[
                        ('Content-Disposition',  content_disposition(image_name, request)),
                        ('Content-Type', guess_type(image_name)[0] or 'octet-stream'),
                        ('Content-Length', len(image)),
                        ],
                    )
        except Exception:
            _logger.exception('error accessing %r [%r]', image_name, img_id)
            return werkzeug.exceptions.InternalServerError(
                    'An error occured attempting to access %r; please let IT know.' % image_name
                    )


# -*- coding: utf-8 -*-

import logging
import os
import werkzeug
from base64 import b64decode
from odoo import http
from openerp.addons.web.controllers.main import content_disposition
from mimetypes import guess_type
from scription import OrmFile

CONFIG = '/%s/config/fnx.ini' % os.environ['VIRTUAL_ENV']
settings = OrmFile(CONFIG).openerp
db = settings.db
login = settings.user
password = settings.pw

_logger = logging.getLogger(__name__)


class Wiki(http.Controller):

    @http.route('/wiki/image/<string:model>/<int:img_id>', type='http', auth='public')
    def image(self, model=None, img_id=None, **kwds):
        _logger.warning('model: %r;  img_id=%d', model, img_id)
        page = http.request.env[model].search([('id','=',img_id)])[0]
        image_name = page['name']
        image = b64decode(page['wiki_img'])
        try:
            return http.request.make_response(
                    image,
                    headers=[
                        ('Content-Disposition',  content_disposition(image_name)),
                        ('Content-Type', guess_type(image_name)[0] or 'octet-stream'),
                        ('Content-Length', len(image)),
                        ],
                    )
        except Exception:
            _logger.exception('error accessing %r [%r]', image_name, img_id)
            return werkzeug.exceptions.InternalServerError(
                    'An error occured attempting to access %r; please let IT know.' % image_name
                    )


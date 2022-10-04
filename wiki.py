"""
Use to create a wiki
====================

- add `wiki.page` to `__oenerp__.py`s `depends` list
- create new model with:
  - `_inherit = wiki.page`
  - `_defaults = {'wiki_key': '<unique value compared to other wikis>'}

example `.py` file
------------------

    from osv import osv

    class some_wiki(osv.Model):
        "Some Wiki"
        _name = 'some.wiki'
        _inherit = 'wiki.page'
        _description = 'Some Wiki Page'

        _defaults = {
                'wiki_key': 'some-wiki',
                }

example `_view.xaml` file
-------------------------

    !!! coding: utf-8
    !!! xml1.0
    
    -view = 'ir.ui.view'
    -action = 'ir.actions.act_window'
    -wiki = 'some.wiki'
    
    ~openerp
        ~data
            // Wiki
    
            ~record model=view #view_some_wiki_tree
                @name: some.wiki.tree
                @model: = wiki
                @arch type='xml'
                    ~tree $Wiki_Pages
                        @name
    
            ~record model=view #view_some_wiki_form
                @name: some.wiki.form
                @model: = wiki
                @arch type='xml'
                    ~form $Wiki_Document version='7.0'
                        ~div
                            ~h1
                                @name
                            ~label for='source_type' string='Document type'
                            @source_type
                            ~div attrs="{'invisible': [('source_type','!=','txt')]}"
                                @source_doc .oe_edit_only placeholder="wiki document..."
                                @wiki_doc .oe_view_only
                            ~div attrs="{'invisible': [('source_type','!=','img')]}"
                                @source_img
                            ~hr
                            ~label for='reverse_links' string='Pages linking here:'
                            @reverse_links widget='many2many_tags'
    
            ~record model=action #action_some_wiki
                @name: Wiki
                @res_model: = wiki
                @view_type: form
                @view_id ref='view_some_wiki_tree'
                @view_mode: tree,form
    
            ~menuitem @Wiki #menu_some_wiki parent='<some_wiki_parent>' action='action_some_wiki' sequence='40'
"""

from antipathy import Path
from base64 import b64decode
import logging
from openerp import VAR_DIR, SUPERUSER_ID
from openerp.exceptions import ERPError
from openerp.osv import osv, fields
import re
from stonemark import Document, escape
from VSS.utils import translator

_logger = logging.getLogger(__name__)

_name_key = translator(
     frm='ABCDEFGHIJKLMNOPQRSTUVWXYZ +/',
      to='abcdefghijklmnopqrstuvwxyz_--',
    keep='abcdefghijklmnopqrstuvwxyz_0123456789.-',
   strip='_',
    )


class wiki_doc(osv.AbstractModel):
    "wiki documents"
    _name = 'wiki.page'
    _inherit = []
    _description = 'wiki page'
    _order = 'name'

    _wiki_path = Path(VAR_DIR) / 'wiki'
    _wiki_tables = set()

    _columns = {
        'wiki_key': fields.char('Wiki Key', size=64, help="each logical wiki has its own wiki key"),
        'name': fields.char('Name', size=64, required=True),
        'name_key': fields.char('Name Key', size=64, required=True),
        'source_type': fields.selection(
                (('txt', 'Text'), ('img', 'Image')),
                'Source Type',
                ),
        'source_doc': fields.text('Source Document', ),
        'source_img': fields.binary('Source Image', ),
        'wiki_doc': fields.html('Wiki Document'),
        'forward_links': fields.many2many(
            'wiki.page',
            rel='wiki_links', id1='src', id2='tgt',
            string='Links from page',
            ),
        'reverse_links': fields.many2many(
            'wiki.page',
            rel='wiki_links', id1='tgt', id2='src',
            string='Links to page',
            ),
        }

    _defaults = {
        'source_type': 'txt',
        }

    _sql_constraints = [
        ('name_uniq', 'unique(name_key)', 'name (or close match) already in use'),
        ]

    def __init__(self, pool, cr):
        super(wiki_doc, self).__init__(pool, cr)
        if self.__class__.__name__ != 'wiki_doc':
            # record table
            self.__class__._wiki_tables.add(self._name)
            # set file path
            self._wiki_path = self.__class__._wiki_path / self._defaults['wiki_key']
            _logger.warning('self._wiki_path = %r', self._wiki_path)

    def _auto_init(self, cr, context=None):
        res = super(wiki_doc, self)._auto_init(cr, context)
        if self.__class__.__name__ != 'wiki_doc':
            if not self._wiki_path.exists():
                self._wiki_path.makedirs()
                for rec in self.browse(cr, SUPERUSER_ID, [(1,'=',1)], context=context):
                    try:
                        if not rec.name_key:
                            self.write(cr, SUPERUSER_ID, rec.id, {'name_key': self._name_key(rec.name)})
                        if rec.source_type == 'txt':
                            self._write_html_file(cr, SUPERUSER_ID, rec.id, context=context)
                        elif rec.source_type == 'img':
                            self._write_image_file(cr, SUPERUSER_ID, rec.id, context=context)
                        else:
                            _logger.error('rec id %d is missing `source_type`', rec.id)
                    except Exception:
                        pass
        return res

    def _write_html_file(self, cr, uid, id, context=None):
        def repl(mo):
            href, target, close = mo.groups()
            if target.startswith('http'):
                return href + target + close
            key = self._name_key(target)
            return "%s%s.html%s" % (href, key, close)
        rec = self.browse(cr, uid, id, context=context)
        name = rec.name
        title = '%s\n%s\n%s\n\n' % (len(name)*'=', name, len(name)*'=')
        source_doc = title + rec.source_doc
        document = self._text2html(name, source_doc)
        link = re.compile('(<a href=")([^"]*)(">)')
        document = re.sub(link, repl, document)
        file = self._wiki_path/self._name_key(name) + '.html'
        with open(file, 'w') as fh:
            fh.write(document)

    def _write_image_file(self, cr, uid, id, context=None):
        rec = self.browse(cr, uid, id, context=context)
        name = rec.name_key
        file = self._wiki_path/name
        with open(file, 'w') as fh:
            fh.write(b64decode(rec.source_img))

    def _convert_links(self, cr, uid, id, document, context=None):
        context = (context or {}).copy()
        context['wiki_reverse_link'] = id
        forward_links = []
        def repl_image_link(mo):
            src, target, close = mo.groups()
            if target.startswith('http'):
                return src + target + close
            key = self._name_key(target)
            target_ids = self.search(cr, uid, [('name_key','=',key)], context=context)
            if not target_ids:
                # create empty image
                target_ids = [self.create(
                        cr, uid,
                        values={'name':target, 'source_type':'img', 'source_img':placeholder},
                        context=context,
                        )]
            forward_links.extend(target_ids)
            return "%s/wiki/image?model=%s&img_id=%d%s" % (
                    src,
                    self._name,
                    target_ids[0],
                    close,
                    )
        def repl_page_link(mo):
            href, target, close = mo.groups()
            if target.startswith('http'):
                return href + target + close
            key = self._name_key(target)
            target_ids = self.search(cr, uid, [('name_key','=',key)], context=context)
            if not target_ids:
                # create empty page
                target_ids = [self.create(
                        cr, uid,
                        values={'name':target, 'source_doc':'[under construction]'},
                        context=context,
                        )]
            forward_links.extend(target_ids)
            return "%s#id=%d%s" % (href, target_ids[0], close)
        web_link = re.compile('(<a href=")([^"]*)(">)')
        img_link = re.compile('(<img src=")([^"]*)(")')
        document = re.sub(web_link, repl_page_link, document)
        document = re.sub(img_link, repl_image_link, document)
        return document, forward_links

    _name_key = staticmethod(_name_key)

    def _text2html(self, name, source_doc, context=None):
        try:
            return Document(source_doc).to_html()
        except Exception:
            _logger.exception('stonemark unable to convert document %s', name)
            return '<pre>' + escape(source_doc) + '</pre>' 


    #-----------------------------------------------------------------------------------
    # create: parse links, maybe create empty linked pages
    # write:  same as create, plus maybe remove links
    # delete: remove content, remove forward links, if no reverse links remove record
    # read: normal
    #-----------------------------------------------------------------------------------

    def create(self, cr, uid, values, context=None):
        # convert source_doc to wiki_doc
        # collect page_name links in wiki doc
        # ensure records exist for each page_name
        # replace page_name links in wiki_doc with ids of linked records
        if context is None:
            context = {}
        name = values.get('name', '')
        values['name_key'] = _name_key(name)
        new_id = super(wiki_doc, self).create(cr, uid, values, context=context)
        del values['name']
        del values['name_key']
        self.write(cr, uid, [new_id], values, context=context)
        return new_id

    def write(self, cr, uid, ids, values, context=None):
        context = context or {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        for rec in self.browse(cr, uid, ids, context=context):
            if 'name' in values:
                name_key = self._name_key(values['name'])
                if rec.name_key != name_key and rec.reverse_links:
                    raise ERPError('invalid name change', 'document is linked to, and change would modify name key')
                values['name_key'] = name_key
            if 'source_type' in values:
                st = values['source_type']
                if st == 'txt':
                    values['source_img'] = False
                else:  # 'img'
                    values['source_doc'] = False
                    values['wiki_doc'] = False
                    values['forward_links'] = [(5, False)]
            source_doc = values.get('source_doc')
            if source_doc:
                name = values.get('name', rec.name)
                document = self._text2html(name, source_doc)
                document, forward_links = self._convert_links(
                        cr, uid, rec.id,
                        document,
                        context=context,
                        )
                values['wiki_doc'] = document
                if forward_links:
                    values['forward_links'] = [(6, 0, forward_links)]
                else:
                    values['forward_links'] = [(5, False)]
            if not super(wiki_doc, self).write(cr, uid, ids, values, context=context):
                return False
        for rec in self.browse(cr, uid, ids, context=context):
            if rec.source_type == 'img':
                self._write_image_file(cr, uid, rec.id, context=context)
            else: # 'txt'
                self._write_html_file(cr, uid, rec.id, context=context)
        return True

    def unlink(self, cr, uid, ids, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        forward_ids = []
        files = []
        for rec in self.browse(cr, uid, ids, context=None):
            # collect file names that will need to be deleted
            # collect forward links for processing so they can update their back links section
            if uid != SUPERUSER_ID and rec.reverse_links:
                raise ERPError('linked document', 'cannot delete %r as other documents link to it' % rec.name)
            forward_ids.extend([f.id for f in rec.forward_links])
            if rec.source_type == 'txt':
                files.append(self._wiki_path/'%s.html' % rec.name_key)
            else: # image file
                files.append(self._wiki_path/rec.name_key)
        if not super(wiki_doc, self).unlink(cr, uid, ids, context=context):
            return False
        # records successfully deleted
        for file in files:
            # remove files that that match deleted records
            try:
                file.unlink()
            except Exception:
                _logger.exception('unable to delete file')
        return True

placeholder = """\
iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAAACXBIWXMAAABIAAAASABGyWs+AAAACXZwQWcAAABAAAAAQAD\
q8/hgAAAEaElEQVRYw82X6XLbNhCA+f4PVomk5MRyHDtp63oEgDcl3vfRBQhQIEVKSvsnO+OxRBEfFnthV+n/pyi/NaCryz\
zL8rJu/wOgzQPXJBgjhDExnXPW/Aqgy30DI0yIwYQQ4Bhe2j0I6BIbI1jL9meC2TdkRu0jgMxCGN5H2HT8IIzjKPAdE9Nng\
EjuAhqfv3rOpe3aIrDAFoB1qtuA3ADlMXKuz9vlLqZokt4CxPAOQXa2bPDCRVSJYB0QIDA4ibp+TVKDbuCvAeh6YpX9DWkc\
UGJCkAARXW9UfXeL0PmUcF4CZBA4cALv5nqQM+yD4mtATQMOGMi9RzghiKriCuBiAzsB1e8uwUUGtroZIAEsqfqHCI2JjdG\
ZHNDSZzHYb0boQK4JOTVXNQFEoJXDPskEvrYTrJHgIwOdZEBrggXzfkbo+sY7Hp0Fx9bUYbUEAAtgV/waHAcCnOew3arbLy\
5lVXGSXIrKGQkrKKMLcnHsPjEGAla1PYi+/YCV37e7DRp1qUDjwREK1wjbo56hezRoPLxt9lzUg+m96Hvtz3BMcU9syQAxK\
BSJ/c2Nqv0Em5C/97q+BdGoEuoORN98CkAqzsAAPh690vdv2tOOEcx/dodP0zq+qjpoQQF7/Vno2UA0OgLQQbUZI6t/1+Bl\
RgAlyywvqtNXja0HFQ7jGVwoUA0HUBNcMvRdpW8PpzDPYRAERfmNE/TDuE8Ajis4oJAiUwB2+g+am3YEEmT5kz4HgOdRygH\
UIPEMsFf/YvXJYoSKbPczQI4HwysSbKKBdk4dLAhJsptrUHK1lSERUDYD6E9pGLsjoXzRZgAIJVaYBCCfA57zMBoJYfV9CX\
DigHhRgww2Hgngh4UjnCUbJAs2CEdCkl25kbou5ABh0KkXPupA6IB8fOUF4TpFOs5Eg50eFSOBfOz0GYCWoJwDoJzwcjQBf\
M2rMAjD0CEsL/Qp4ISG/FHkuJ4A9toXv66KomosMMNAuAA6GxOWPwqP64sb3kTm7HX1Fbsued9BXjACZKNIphLz/FF4WIps\
6vqff+jaIFAONiBbTf1hDITti5RLg+cYoDOxqJFwxb0dXmT5Bn/Pn8wOh9dQnMASK4aaSGuk+G24DObCbm5XzkXs9RdASTu\
ytUZO6Czdm2BCA2cSgNbIWedxk0AV4FVYEYFJpLK4SuA3DrsceQEQl6svXy33CKfxIrwAanqZBA8R4AAQWeUMwJ6CZ7t7BI\
h6utfos0uLwxqP7BECMaTUuQCoawhO+9sSUWtjs1kA9I1Fm8DoNiCl64nUCsp9Ym1SgncjoLoz7YTl9dNOtbGRYSAjWbMDN\
PKw3py0otNeufVYN2wvzha5g6iGzlTDebsfEdbtW9EsLOvYZs06Dmbsq4GjcoeBgThBWtRN2zZ1mYUuGZ7axfz9hZEns+mM\
Q+ckzIYm/gn+WQvWWRq6uoxuSNi4RWWAYGfRuCtjXx25Bh25MGaTFzaccCVX1wfPtkiCk+e6nh/ExXps/N6z80PyL8wPTYg\
PwzDiAAAAJXRFWHRkYXRlOmNyZWF0ZQAyMDExLTAxLTE5VDAzOjU5OjAwKzAxOjAwaFry6QAAACV0RVh0ZGF0ZTptb2RpZn\
kAMjAxMC0xMi0yMVQxNDozMDo0NCswMTowMGxOe/8AAAAZdEVYdFNvZnR3YXJlAEFkb2JlIEltYWdlUmVhZHlxyWU8AAAAA\
ElFTkSuQmCC"""

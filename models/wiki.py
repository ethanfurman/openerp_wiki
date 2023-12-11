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

        _wiki_key = 'some-wiki'


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
                        ~group
                            @source_type .oe_edit_only widget='radio' options="{'horizontal': 1}"
                            @top_level .oe_edit_only
                        ~div attrs="{'invisible': [('source_type','!=','txt')]}"
                            @source_doc .oe_edit_only placeholder="wiki document..."
                            @wiki_doc .oe_view_only
                        ~div attrs="{'invisible': [('source_type','!=','img')]}"
                            @source_img widget='image' .oe_edit_only
                            @wiki_img widget='image' .oe_view_only
                        ~div
                            ~hr
                            @reverse_links .oe_view_only widget='many2many_tags'

        ~record model=view #view_main_wiki_search
            @name: wiki.page.search
            @model: wiki.page
            @arch type='xml'
                ~search $Wiki_Page
                    ~filter $Top_Level_Pages @type_top_level domain="[('top_level','=',True)]"
                    ~separator
                    ~filter $Documents @type_document domain="[('source_type','=','txt')]"
                    ~filter $Images @type_images domain="[('source_type','=','img')]"
                    ~separator
                    ~filter $Empty @type_empty domain="[('is_empty','=',True)]"
                    ~filter $Not_Empty @type_not_empty domain="[('is_empty','=',False)]"

        ~record model=action #action_some_wiki
            @name: Wiki
            @res_model: = wiki
            @view_type: form
            @view_id ref='view_some_wiki_tree'
            @view_mode: tree,form
            @context: {'search_default_type_top_level':'1', 'search_default_type_document':'1', 'search_default_type_not_empty':'1'}

            ~menuitem @Wiki #menu_some_wiki parent='<some_wiki_parent>' action='action_some_wiki' sequence='40'
"""

from antipathy import Path
from base64 import b64decode, b64encode
import io
import logging
import os
from PIL import Image, ImageOps
import re
from textwrap import dedent
from stonemark import Document, escape, write_css, write_html as write_html_file
import threading
from VSS.utils import translator



import odoo
from odoo import api, fields, models, tools, VAR_DIR, SUPERUSER_ID
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

_name_key = translator(
     frm=' +/',
      to='_--',
    keep='abcdefghijklmnopqrstuvwxyz_0123456789.-',
    )

def calc_name_key(name):
    name = name.replace('&','and')
    name = re.sub(r' ?- ?', '-', name.lower().strip())
    name = re.sub(r' +', ' ', name)
    name = _name_key(name)
    name = re.sub(r'_+', '_', name)
    return name

WIKI_PATH = Path(VAR_DIR) / 'wiki'

class WikiKey(models.Model):
    "wiki categories (i.e. keys)"
    _name = 'wiki.key'
    _inherit = []
    _description = 'wiki key'
    _order = 'name'

    name = fields.Char('Wiki Key', required=True, size=64)
    private = fields.Boolean('System', help='Omit from Wiki -> Pages ?', readonly=True)
    template = fields.Text('Template')

    @api.constrains('name')
    def unique(self):
        seen = set()
        for rec in self.browse():
            tname = calc_name_key(rec.name)
            if tname in seen:
                raise ValidationError('wiki name already in use: %r' % rec.name)
            seen.add(tname)

    def init(self):
        """
        ensure each non-system key exists on disk
        """
        for rec in self.read():
            wiki_path = WikiDoc._wiki_path / calc_name_key(rec.name)
            if not wiki_path.exists():
                wiki_path.makedirs()

    @api.model_create_multi
    def create(self, vals_list):
        categories = super().create(vals_list)
        for cat in categories:
            wiki_path = WikiDoc._wiki_path / calc_name_key(cat.name)
            if not wiki_path.exists():
                wiki_path.makedirs()
        return categories

    def write(self, values):
        if len(self) > 1:
            raise ValidationError('attempt to change multiple record to same name: %r' % values['name'])
        if 'name' in values:
            current_name = self[0]['name']
        res = super().write(values)
        if 'name' in values:
            # update any pages in this categary
            self = self.with_context(wiki_maintenance=True)
            wiki_pages = self.env['wiki.page']
            wiki_pages = wiki_page.search([('wiki_key','=',current_name)])
            wiki_pages.write({'wiki_key': values['name']})
            # and rename the on-disk directory
            old_path = WikiDoc._wiki_path / calc_name_key(current_name)
            new_path = WikiDoc._wiki_path / calc_name_key(values['name'])
            if old_path != new_path:
                # not just an aesthetic change
                old_path.move(new_path)
        return res

    def unlink(self):
        wiki_page = self.env['wiki.page']
        names = [r.name for r in self]
        in_use = [r.wiki_key for r in wiki_page.search([('wiki_key','in',names)])]
        if in_use:
            raise ValidationError('Cannot delete categories that are in use: %s' % ', '.join(in_use))
        res = super().unlink()
        for name in names:
            wiki_path = WikiDoc._wiki_path / calc_name_key(name)
            wiki_path.rmtree(onerror=self._log_disc_error)
        return res

    @api.model
    def _log_disc_error(self, func, path, exc_info):
        _logger.error('unable to %s() %r [%s]', func.__name__, path, exc_info[1])


class WikiDoc(models.Model):
    "wiki documents"
    _name = 'wiki.page'
    _inherit = []
    _description = 'wiki page'
    _order = 'name'

    _wiki_path = WIKI_PATH
    _wiki_tables = set()
    _wiki_key = ''

    @api.depends('source_doc','source_type','source_img')
    def _finalize_page(self):
        # set wiki_doc, wiki_img, and is_empty
        for rec in self:
            if isinstance(rec.id, models.NewId):
                continue
            # update wiki_doc
            rec.wiki_doc = rec.source_doc
            rec.forward_links = [(5, False)]
            if rec.source_doc:
                document = self._text2html(rec.name, rec.source_doc)
                rec.wiki_doc, forward_links = self._convert_links(rec.id, document, category=rec.wiki_key)
                if forward_links:
                    rec.forward_links = [(6, 0, list(set(forward_links)))]
            # update wiki_img
            rec.wiki_img = rec.source_img
            if rec.source_img:
                file_type = os.path.splitext(rec.name)[1][1:]  # strip leading period
                image_stream = io.BytesIO(b64decode(rec.source_img))
                image = Image.open(image_stream)
                target_width = 900
                if target_width < image.size[0]:
                    target_height = int(image.size[1] * (float(target_width) / image.size[0]))
                    size = target_width, target_height
                    image = ImageOps.fit(image, size, Image.Resampling.LANCZOS)
                    if image.mode not in ["1", "L", "P", "RGB", "RGBA"]:
                        image = image.convert("RGB")
                    new_image_stream = io.BytesIO()
                    image.save(new_image_stream, file_type)
                    rec.wiki_img = b64encode(new_image_stream.getvalue())
            if rec.source_type == 'txt' and rec.source_doc in (False, '', '[under construction]', '[[under construction]]'):
                rec.is_empty = True
            elif rec.source_type == 'img' and rec.source_img in (False, placeholder):
                rec.is_empty = True
            else:
                rec.is_empty = False

    def _create_reverse_html(self):
        action, menu = self._get_action_menu()
        for rec in self:
            if isinstance(rec.id, models.NewId):
                continue
            result = []
            for link in rec.reverse_links:
                result.append('<div><a href="#id=%d&action=%d&model=%s&view_type=form&cids=&menu_id=%d">%s</a></div>' % (
                    link.id, action.id, self._name, menu.id, link.name,
                    ))
            rec.reverse_links_html = ''.join(result)

    @api.model
    def _select_key(self):
        key = self.env['wiki.key']
        if self._wiki_key:
            # private wiki
            domain = [('name','=',self._wiki_key)]
        else:
            # public wiki
            domain = [('private','=',False)]
        res = [(calc_name_key(r['name']), r['name']) for r in key.search(domain)]
        return res

    wiki_key = fields.Selection(_select_key, required=True, string='Wiki Key', help="each logical wiki has its own wiki key")
    name = fields.Char('Name', size=64, required=True)
    name_key = fields.Char('Name key', size=64, required=True)
    top_level = fields.Boolean('Top level doc', default=False, help='Top level docs are shown by default.')
    source_type = fields.Selection([('txt','Text'),('img','Image')], 'Source Type', default='txt')
    source_doc = fields.Text('Source Document')
    source_img = fields.Image('Source Image', attachment=False)
    wiki_doc = fields.RawHtml('Wiki Document', compute='_finalize_page', store=True)
    wiki_img = fields.Image('Wiki Image', compute='_finalize_page', store=True)
    forward_links = fields.Many2many(
            'wiki.page',
            'wiki_links_rel', 'src', 'tgt',
            string='Links from page',
            )
    reverse_links = fields.Many2many(
            'wiki.page',
            'wiki_links_rel', 'tgt', 'src',
            string='Links to page',
            )
    reverse_links_html = fields.RawHtml('Reverse Links', compute='_create_reverse_html')
    is_empty = fields.Boolean('Empty?', compute='_finalize_page', store=True)

    _sql_constraints = [
        ('name_uniq', 'unique(name_key)', 'name (or close match) already in use'),
        ]

    def __init__(self, pool, cr):
        super().__init__(pool, cr)
        if self.__class__.__name__ != 'WikiDoc':
            # record table
            self.__class__._wiki_tables.add(self._name)

    def init(self):
        if self.__class__.__name__ == 'WikiDoc':
            subwikis = [
                    (rec.name, self.calc_name_key(rec.name))
                    for rec in self.env['wiki.key'].search([('private','=',False)])
                    ]
        else:
            subwikis = [(self._wiki_key, self.calc_name_key(self._wiki_key))]
        wiki_cr = self._cr
        for name, path in subwikis:
            wiki_path = self._wiki_path / path
            # if not wiki_path.exists():
            _logger.info('wiki: writing files for %r to %r', name, wiki_path)
            wiki_path.makedirs()
            for rec in self.search([('wiki_key','=',name)]):
                wiki_cr.execute(dedent('''
                        UPDATE %s
                        SET name=%%s, name_key=%%s
                        WHERE id=%%s
                        ''' % (self._table, )), (rec.name.strip(), self.calc_name_key(rec.name.strip()), rec.id)
                        )
                try:
                    if rec.source_type == 'txt':
                        rec.write({'source_doc': rec.source_doc})
                    elif rec.source_type == 'img':
                        rec.write({'source_img': rec.source_img})
                    else:
                        _logger.error('rec id %d is missing `source_type`', rec.id)
                except Exception:
                    _logger.exception('error processing %r' % rec.name)

    @api.model
    def _write_html_file(self, rec):
        def repl(mo):
            href, target, close = mo.groups()
            if target.startswith(('http','#footnote-')):
                return href + target + close
            key = self.calc_name_key(target)
            return "%s%s.html%s" % (href, key, close)
        name = rec.name
        title = '%s\n%s\n%s\n\n' % (len(name)*'=', name, len(name)*'=')
        source_doc = title + (rec.source_doc or '')
        document = Document(source_doc, first_header_is_title=True).to_html()
        link = re.compile('(<a href=")([^"]*)(">)')
        document = re.sub(link, repl, document)
        file = self._wiki_path/self.calc_name_key(rec.wiki_key)/rec.name_key + '.html'
        write_html_file(file, document, title)
        css_file = self._wiki_path/self.calc_name_key(rec.wiki_key)/'stonemark.css'
        if not css_file.exists():
            write_css(self._wiki_path/self.calc_name_key(rec.wiki_key)/'stonemark.css')

    @api.model
    def _write_image_file(self, rec):
        file = self._wiki_path/self.calc_name_key(rec.wiki_key)/rec.name_key
        with open(file, 'wb') as fh:
            fh.write(b64decode(rec.source_img))

    @api.model
    def _convert_links(self, rec_id, document, category):
        self = self.with_context(wiki_reverse_link=rec_id)
        forward_links = []
        action, menu = self._get_action_menu()
        while menu.parent_id:
            menu = menu.parent_id
        #
        def repl_image_link(mo):
            src, target, attrs, close = mo.groups()
            if target.startswith('http'):
                return src + target + attrs + close
            key = self.calc_name_key(target)
            target_ids = self.search([('name_key','=',key)])
            if not target_ids:
                # create empty image
                target_ids = [self.create(
                        values={
                            'name': target,
                            'source_type': 'img',
                            'source_img': placeholder,
                            'wiki_key': category,
                            },
                        )]
            forward_links.extend([t.id for t in target_ids])
            return '<a href="#id=%d&action=%d&model=%s&view_type=form&cids=&menu_id=%d">%s/wiki/image/%s/%d%s%s</a>' % (
                    target_ids[0],
                    action.id,
                    self._name,
                    menu.id,
                    src,
                    self._name,
                    target_ids[0],
                    attrs,
                    close,
                    )
        def repl_page_link(mo):
            href, target, close = mo.groups()
            if target.startswith((
                    'http',         # external link
                    '#footnote-',   # footnote link
                )):
                return href + target + close
            key = self.calc_name_key(target)
            target_ids = self.search([('name_key','=',key)])
            if not target_ids:
                # create empty page
                target_ids = [self.create(
                        values={
                            'name': target,
                            'source_doc': '[[under construction]]',
                            'wiki_key': category,
                            },
                        )]
            forward_links.extend([t.id for t in target_ids])
            return "%s#id=%d&action=%d&model=%s&view_type=form&cids=&menu_id=%d%s" % (
                    href, target_ids[0], action.id, self._name, menu.id, close,
                    )
        #
        web_link = re.compile('(<a href=")([^"]*)(">)')
        img_link = re.compile('(<img src=")([^"]*)(")([^>]*>)')
        document = re.sub(web_link, repl_page_link, document)
        document = re.sub(img_link, repl_image_link, document)
        return document, forward_links

    calc_name_key = api.model(staticmethod(calc_name_key))

    @api.model
    def _get_action_menu(self):
        action = self.env['ir.actions.act_window'].search([('res_model','=',self._name)])[0]
        menu = self.env['ir.ui.menu'].search([('action','=','ir.actions.act_window,%d' % action.id)])[0]
        return action, menu

    @api.model
    def _text2html(self, name, source_doc):
        try:
            return (
                    '<div class="wiki">\n'
                    + Document(source_doc).to_html()
                    + '\n</div>'
                    )
        except Exception:
            _logger.exception('stonemark unable to convert document <%s>', name)
            return '<pre>' + escape(source_doc) + '</pre>'

    #-----------------------------------------------------------------------------------
    # create: parse links, maybe create empty linked pages
    # write:  same as create, plus maybe remove links
    # delete: remove content, remove forward links, if no reverse links remove record
    # read: normal
    #-----------------------------------------------------------------------------------

    def write_files(self):
        for rec in self:
            if rec.source_type == 'img':
                self._write_image_file(rec)
            else: # 'txt'
                self._write_html_file(rec)

    @api.model_create_multi
    def create(self, values):
        for rec in values:
            name = rec['name'] = rec['name'].strip()
            rec['name_key'] = self.calc_name_key(name)
        new_rec = super().create(values)
        self.write_files()
        return new_rec

    def write(self, values):
        # ?? self is old data, values are new data ??
        #
        # TODO: handle changes to wiki_key
        if self.env.context.get('wiki_maintenance'):
            return super().write(values)
        for rec in self:
            old_file = None
            if 'name' in values:
                # save old name so old file can be deleted
                old_file = self._wiki_path / self.calc_name_key(rec.wiki_key) / rec.name_key
                if rec.source_type == 'txt':
                    old_file += '.html'
                name = values['name'] = values['name'].strip()
                new_name_key = self.calc_name_key(name)
                if rec.name_key != new_name_key and rec.reverse_links:
                    # do not allow name changes as it would require automatically updating the
                    # linking documents' text with the new name
                    raise ValidationError('document is linked to, and change would modify name key')
                values['name_key'] = new_name_key
            if 'source_type' in values:
                st = values['source_type']
                if st == 'txt':
                    values['source_img'] = False
                    values['wiki_img'] = False
                else:  # 'img'
                    values['source_doc'] = False
                    values['wiki_doc'] = False
                    values['forward_links'] = [(5, False)]
            if not super().write(values):
                return False
            try:
                old_file and old_file.unlink()
            except Exception:
                _logger.exception('unable to delete file %s' % old_file)
        self.write_files()
        return True

    def unlink(self):
        forward_ids = []
        files = []
        for rec in self:
            # collect file names that will need to be deleted
            # collect forward links for processing so they can update their back links section
            if not self.env.su and rec.reverse_links:
                raise ValidationError('cannot delete %r as other documents link to it' % rec.name)
            forward_ids.extend([f.id for f in rec.forward_links])
            if rec.source_type == 'txt':
                files.append(self._wiki_path/self.calc_name_key(rec.wiki_key)/rec.name_key + '.html')
            else: # image file
                files.append(self._wiki_path/self.calc_name_key(rec.wiki_key)/rec.name_key)
        if not super().unlink():
            return False
        # records successfully deleted
        for file in files:
            # remove files that that match deleted records
            try:
                file.unlink()
            except Exception:
                _logger.exception('unable to delete file')
        return True

    @api.onchange('wiki_key')
    def onchange_wiki_key(self):
        if self._wiki_key and not self.source_doc:
            category = self.env['wiki.key'].search([('name','=',self._wiki_key)])
            self.source_doc = category.template

back_button = """
<script>
function goBack()
  {
  window.history.back()
  }
</script>
<input type="button" value="Back" onclick="goBack()">
"""

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

'''
http://localhost:8069/web#id=240&action=899&model=wiki.page&view_type=form&cids=&menu_id=594
http://localhost:8069/web#id=257&action=899&model=wiki.page&view_type=form&cids=&menu_id=590
'''

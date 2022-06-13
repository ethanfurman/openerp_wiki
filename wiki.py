from antipathy import Path
import logging
from openerp import VAR_DIR
from openerp.exceptions import ERPError
from openerp.osv import osv, fields
import re
from stonemark import Document, FormatError, escape
from VSS.utils import translator

_logger = logging.getLogger(__name__)

_name_key = translator(
     frm='ABCDEFGHIJKLMNOPQRSTUVWXYZ ',
      to='abcdefghijklmnopqrstuvwxyz_',
    keep='abcdefghijklmnopqrstuvwxyz_0123456789',
   strip='_',
    )


class wiki_doc(osv.AbstractModel):
    "wiki documents"
    _name = 'wiki.page'
    _inherit = []
    _description = 'wiki page'

    _wiki_path = Path(VAR_DIR) / 'wiki'
    _wiki_tables = set()

    _columns = {
        'wiki_key': fields.char('Wiki Key', size=64, help="each logical wiki has its own wiki key"),
        'name': fields.char('Name', size=64, required=True),
        'name_key': fields.char('Name Key', size=64, required=True),
        'source_doc': fields.text('Source Document', required=True),
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

    def __init__(self, pool, cr):
        super(wiki_doc, self).__init__(pool, cr)
        if self.__class__.__name__ != 'wiki_doc':
            # record table
            self.__class__._wiki_tables.add(self._name)
            # set file path
            self._wiki_path = self.__class__._wiki_path / self._defaults['wiki_key']

    def _auto_init(self, cr, context=None):
        res = super(wiki_doc, self)._auto_init(cr, context)
        if self.__class__.__name__ != 'wiki_doc':
            if not self._wiki_path.exists():
                self._wiki_path.makedirs(self._wiki_path)
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
        links = []
        for link in rec.reverse_links:
            links.append('<a href="%s.html">%s</a>' % (link.name_key, link.name))
        if links:
            document += '<hr>\n<div><p>links to this page: %s</p></div>' % ', '.join(links)
        file = self._wiki_path/self._name_key(name) + '.html'
        with open(file, 'w') as fh:
            fh.write(document)

    def _convert_links(self, cr, uid, id, document, context=None):
        context = (context or {}).copy()
        context['wiki_reverse_link'] = id
        forward_links = []
        def repl(mo):
            href, target, close = mo.groups()
            if target.startswith('http'):
                return href + target + close
            key = self._name_key(target)
            target_ids = self.search(cr, uid, [('name_key','=',key)], context=context)
            if not target_ids:
                # create empty page
                target_ids = [self.create(cr, uid, values={'name': target, 'source_doc': ''}, context=context)]
            forward_links.extend(target_ids)
            return "%s#id=%d%s" % (href, target_ids[0], close)
        link = re.compile('(<a href=")([^"]*)(">)')
        document = re.sub(link, repl, document)
        links = []
        rec = self.browse(cr, uid, id, context=context)
        for link in rec.reverse_links:
            links.append('<a href="#id=%d">%s</a>' % (link.id, link.name))
        if links:
            document += '\n<hr>\n<div><p>links to this page: %s</p></div>' % ', '.join(links)
        return document, forward_links

    _name_key = staticmethod(_name_key)

    def _text2html(self, name, source_doc, context=None):
        try:
            return Document(source_doc).to_html()
        except FormatError:
            _logger.exception('stonemark unable to convert document %s', name)
            return escape(source_doc)


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
        source_doc = self._text2html(name, values['source_doc'])
        rl = context.get('wiki_reverse_link')
        if rl:
            values['reverse_links'] = [(4, rl)]
        new_id = super(wiki_doc, self).create(cr, uid, values, context=context)
        document, forward_links = self._convert_links(cr, uid, new_id, source_doc, context=context)
        super(wiki_doc, self).write(cr, uid, new_id, {'wiki_doc': document, 'forward_links': [(6, 0, forward_links)]}, context=context)
        self._write_html_file(cr, uid, new_id, context=context)
        return new_id

    def write(self, cr, uid, ids, values, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        forward_ids = []
        for rec in self.browse(cr, uid, ids, context=None):
            # collect forward links for processing so they can update their back links section
            forward_ids.extend([f.id for f in rec.forward_links])
        for rec in self.browse(cr, uid, ids, context=context):
            if 'name' in values:
                name_key = self._name_key(values['name'])
                if rec.name_key != name_key and rec.reverse_links:
                    raise ERPError('invalid name change', 'document is linked to and change would modify name key')
                values['name_key'] = name_key
            if 'source_doc' in values:
                name = values.get('name', rec.name)
                document = self._text2html(name, values['source_doc'])
                document, forward_links = self._convert_links(cr, uid, rec.id, document, context=context)
                values['wiki_doc'] = document
                values['forward_links'] = [(6, 0, forward_links)]
        res = super(wiki_doc, self).write(cr, uid, ids, values, context=context)
        if res:
            if forward_ids:
                for rec in self.browse(cr, uid, forward_ids, context=context):
                    # update back links
                    values = {}
                    document = self._text2html(rec.name, rec.source_doc)
                    document, forward_links = self._convert_links(cr, uid, rec.id, document, context=context)
                    values['wiki_doc'] = document
                    values['forward_links'] = [(6, 0, forward_links)]
                    self.write(cr, uid, rec.id, values, context=context)
            # process original documens and any forward link documents so they can have properly displayed back links
            for rec in self.browse(cr, uid, ids+forward_ids, context=context):
                self._write_html_file(cr, uid, rec.id, context=context)
        return res

    def unlink(self, cr, uid, ids, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        forward_ids = []
        files = []
        for rec in self.browse(cr, uid, ids, context=None):
            # collect file names that will need to be deleted
            # collect forward links for processing so they can update their back links section
            if rec.reverse_links:
                raise ERPError('linked document', 'cannot delete %r as other documents link to it' % rec.name)
            forward_ids.extend([f.id for f in rec.forward_links])
            files.append(self._wiki_path/'%s.html' % rec.name_key)
        res = super(wiki_doc, self).unlink(cr, uid, ids, context=context)
        if res:
            # records successfully deleted
            if forward_ids:
                for rec in self.browse(cr, uid, forward_ids, context=context):
                    # update back links
                    values = {}
                    document = self._text2html(rec.name, rec.source_doc)
                    document, forward_links = self._convert_links(cr, uid, rec.id, document, context=context)
                    values['wiki_doc'] = document
                    values['forward_links'] = [(6, 0, forward_links)]
                    self.write(cr, uid, rec.id, values, context=context)
            for file in files:
                # remove files that that match deleted records
                file.unlink()
            if forward_ids:
                for rec in self.browse(cr, uid, forward_ids, context=context):
                    self._write_html_file(cr, uid, rec.id, context=context)
        return res


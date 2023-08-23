{
    'name': 'Wiki',
    'version': '0.2',
    'category': 'Knowledge Management',
    'description': """\
            """,
    'author': 'Ethan Furman',
    'maintainer': 'Ethan Furman',
    'website': '',
    'depends': [
            "base",
            "knowledge",
            ],
    'data': [
            'views/wiki.xml',
            'security/ir.model.access.csv',
            ],
    'css':[
        'static/src/css/wiki.css',
        ],
    'js': [
            ],
    'test': [],
    'application': True,
    'installable': True,
    'active': False,
}

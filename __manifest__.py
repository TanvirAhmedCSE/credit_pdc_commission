{
    'name': 'Credit Control, PDC & Collection Commission',
    'version': '19.0.1.0.0',
    'category': 'Sales/Accounting',
    'summary': "Customer credit limits with manager approval, post-dated cheque (PDC) register, "
               "commission on collection, aging/overdue reports, customer portal with SSLCommerz payment.",
    'depends': [
        'base', 'mail', 'sale', 'account', 'portal', 'payment', 'account_payment',
        'payment_sslcommerz',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'report/report_actions.xml',
        'report/report_templates.xml',
        'data/mail_template_data.xml',
        'data/cron_data.xml',
        'wizard/pdc_bounce_wizard_views.xml',
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/account_move_views.xml',
        'views/pdc_views.xml',
        'views/commission_views.xml',
        'views/aging_views.xml',
        'views/res_config_settings_views.xml',
        'views/portal_templates.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.report_assets_common': [
            'credit_pdc_commission/static/src/css/report_fonts.css',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
    'license': 'LGPL-3',
}

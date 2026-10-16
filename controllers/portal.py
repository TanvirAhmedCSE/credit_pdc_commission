from odoo import http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class CreditPortal(CustomerPortal):

    def _commercial_partner(self):
        return request.env.user.partner_id.commercial_partner_id

    def _pdc_domain(self):
        return [('commercial_partner_id', '=', self._commercial_partner().id)]

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'pdc_count' in counters:
            PDC = request.env['cc.pdc']
            values['pdc_count'] = PDC.search_count(self._pdc_domain()) if PDC.has_access('read') else 0
        return values

    @http.route('/my/credit', type='http', auth='user', website=True)
    def portal_my_credit(self, **kw):
        partner = self._commercial_partner()
        data = partner.sudo()._cc_statement_data()
        for row in data['rows']:
            is_invoice = row['move'].move_type == 'out_invoice'
            row['url'] = row['move'].get_portal_url() if is_invoice else False
            row['payable'] = is_invoice and row['residual'] > 0
        return request.render('credit_pdc_commission.portal_my_credit', {
            'partner': partner,
            'data': data,
            'credit': data['credit'],
            'currency': data['credit']['currency'],
            'page_name': 'credit',
        })

    @http.route('/my/credit/statement', type='http', auth='user', website=True)
    def portal_credit_statement(self, **kw):
        return self._show_report(
            model=self._commercial_partner().sudo(), report_type='pdf',
            report_ref='credit_pdc_commission.action_report_customer_statement', download=True)

    @http.route('/my/pdc', type='http', auth='user', website=True)
    def portal_my_pdc(self, **kw):
        pdcs = request.env['cc.pdc'].search(self._pdc_domain(), order='cheque_date desc', limit=100)
        return request.render('credit_pdc_commission.portal_my_pdc', {
            'pdcs': pdcs.sudo(),
            'page_name': 'pdc',
        })

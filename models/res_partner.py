from odoo import api, fields, models
from odoo.exceptions import AccessError

from .utils import BUCKETS, bucket_for

CC_PROTECTED_FIELDS = ('cc_credit_check', 'cc_credit_limit', 'cc_credit_hold')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    cc_credit_check = fields.Boolean("Enforce Credit Limit", tracking=True)
    cc_credit_limit = fields.Monetary("Credit Limit", currency_field='cc_currency_id', tracking=True)
    cc_credit_hold = fields.Boolean("Credit Hold", tracking=True,
                                    help="Blocks every new sales order until a credit manager approves it.")

    cc_currency_id = fields.Many2one('res.currency', compute='_compute_cc_credit')
    cc_receivable = fields.Monetary("Open Receivable", currency_field='cc_currency_id', compute='_compute_cc_credit')
    cc_uninvoiced = fields.Monetary("Confirmed, Not Invoiced", currency_field='cc_currency_id',
                                    compute='_compute_cc_credit')
    cc_exposure = fields.Monetary("Credit Exposure", currency_field='cc_currency_id', compute='_compute_cc_credit')
    cc_available = fields.Monetary("Available Credit", currency_field='cc_currency_id', compute='_compute_cc_credit')
    cc_overdue_amount = fields.Monetary("Overdue Amount", currency_field='cc_currency_id',
                                        compute='_compute_cc_credit')
    cc_pdc_held = fields.Monetary("PDC in Hand", currency_field='cc_currency_id', compute='_compute_cc_credit')
    cc_utilization = fields.Float("Utilization", compute='_compute_cc_credit')
    cc_credit_status = fields.Selection([
        ('disabled', "No Limit"), ('ok', "OK"), ('warning', "Near Limit"),
        ('exceeded', "Exceeded"), ('hold', "On Hold")], compute='_compute_cc_credit')
    cc_pdc_count = fields.Integer(compute='_compute_cc_pdc_count')

    
    # Access guard: only credit managers may change limits
    
    def write(self, vals):
        if (any(f in vals for f in CC_PROTECTED_FIELDS) and not self.env.su
                and not self.env.user.has_group('credit_pdc_commission.group_cc_manager')):
            raise AccessError(self.env._("Only Credit & PDC managers can change credit limit settings."))
        return super().write(vals)

    
    # Credit data
    
    def _cc_receivable_domain(self):
        self.ensure_one()
        return [
            ('partner_id', 'child_of', self.commercial_partner_id.id),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
            ('company_id', '=', self.env.company.id),
        ]

    def _cc_credit_data(self):
        """ Credit position of the commercial partner (company currency). """
        base = self._origin or self
        company = self.env.company
        data = {
            'currency': company.currency_id, 'enabled': False, 'hold': False, 'limit': 0.0,
            'receivable': 0.0, 'overdue': 0.0, 'uninvoiced': 0.0, 'exposure': 0.0,
            'available': 0.0, 'pdc_held': 0.0, 'percent': 0.0, 'status': 'disabled',
        }
        if not base.id:
            return data
        commercial = base.commercial_partner_id
        today = fields.Date.context_today(self)
        Line = self.env['account.move.line'].sudo()
        domain = commercial._cc_receivable_domain()
        receivable = Line._read_group(domain, [], ['amount_residual:sum'])[0][0] or 0.0
        overdue = Line._read_group(
            domain + [('date_maturity', '<', today)], [], ['amount_residual:sum'])[0][0] or 0.0

        uninvoiced = 0.0
        orders = self.env['sale.order'].sudo().search([
            ('partner_id', 'child_of', commercial.id), ('state', '=', 'sale'),
            ('company_id', '=', company.id),
            ('invoice_status', 'in', ('to invoice', 'no', 'upselling')),
        ])
        for order in orders:
            order_date = order.date_order.date() if order.date_order else today
            uninvoiced += order.currency_id._convert(
                order._cc_uninvoiced_amount(), company.currency_id, company, order_date)

        pdc_held = self.env['cc.pdc'].sudo()._read_group(
            [('commercial_partner_id', '=', commercial.id), ('state', 'in', ('received', 'deposited'))],
            [], ['amount:sum'])[0][0] or 0.0

        limit = commercial.cc_credit_limit
        exposure = receivable + uninvoiced
        if commercial.cc_credit_hold:
            status = 'hold'
        elif not commercial.cc_credit_check:
            status = 'disabled'
        elif exposure > limit:
            status = 'exceeded'
        elif limit and exposure >= 0.8 * limit:
            status = 'warning'
        else:
            status = 'ok'
        data.update({
            'enabled': commercial.cc_credit_check, 'hold': commercial.cc_credit_hold, 'limit': limit,
            'receivable': receivable, 'overdue': overdue, 'uninvoiced': uninvoiced,
            'exposure': exposure, 'available': limit - exposure, 'pdc_held': pdc_held,
            'percent': (exposure / limit * 100.0) if limit else (100.0 if exposure > 0 else 0.0),
            'status': status,
        })
        return data

    @api.depends_context('company')
    def _compute_cc_credit(self):
        for partner in self:
            data = partner._cc_credit_data()
            partner.cc_currency_id = data['currency']
            partner.cc_receivable = data['receivable']
            partner.cc_uninvoiced = data['uninvoiced']
            partner.cc_exposure = data['exposure']
            partner.cc_available = data['available']
            partner.cc_overdue_amount = data['overdue']
            partner.cc_pdc_held = data['pdc_held']
            partner.cc_utilization = data['percent'] / 100.0
            partner.cc_credit_status = data['status']

    def _compute_cc_pdc_count(self):
        PDC = self.env['cc.pdc'].sudo()
        for partner in self:
            base = partner._origin or partner
            partner.cc_pdc_count = PDC.search_count(
                [('commercial_partner_id', '=', base.commercial_partner_id.id)]) if base.id else 0

    def action_view_pdc(self):
        self.ensure_one()
        commercial = self.commercial_partner_id
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Post-Dated Cheques"),
            'res_model': 'cc.pdc',
            'view_mode': 'list,form',
            'domain': [('commercial_partner_id', '=', commercial.id)],
            'context': {'default_partner_id': commercial.id},
        }

    
    # Statement / aging data (used by the PDF report and the portal)
    
    def _cc_statement_data(self):
        self.ensure_one()
        commercial = self.commercial_partner_id
        today = fields.Date.context_today(self)
        lines = self.env['account.move.line'].sudo().search(
            commercial._cc_receivable_domain(), order='date_maturity, id')
        aging = {key: 0.0 for key, _label in BUCKETS}
        rows = []
        for line in lines:
            due = line.date_maturity or line.date
            days = (today - due).days
            bucket = bucket_for(days)
            aging[bucket] += line.amount_residual
            rows.append({
                'move': line.move_id, 'name': line.move_id.name or line.name, 'date': line.date,
                'due': due, 'days': max(days, 0), 'bucket': bucket, 'residual': line.amount_residual,
            })
        pdcs = self.env['cc.pdc'].sudo().search(
            [('commercial_partner_id', '=', commercial.id), ('state', 'in', ('received', 'deposited', 'bounced'))],
            order='cheque_date')
        return {
            'rows': rows, 'aging': aging, 'total': sum(aging.values()),
            'overdue_total': sum(v for k, v in aging.items() if k != 'not_due'),
            'credit': commercial._cc_credit_data(), 'pdcs': pdcs, 'buckets': BUCKETS,
        }

    def _get_report_base_filename(self):
        self.ensure_one()
        return self.env._("Credit Statement - %s", self.name or '')
